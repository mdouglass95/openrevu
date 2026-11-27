"""Autosave and crash recovery.

While a document has unsaved changes, OpenRevu keeps a snapshot of it in the recovery folder.
A snapshot is removed when the document is saved or closed. After a crash the snapshots stay,
and the next start offers to restore them. A snapshot of a password-protected document is encrypted.

Cost on the GUI thread. The autosave tick serializes the document there (Document._snapshot, a full tobytes), because
PyMuPDF is not thread-safe; write_later() hands the bytes to a worker thread. On a 250 MB test file (50 pages of
incompressible images) tobytes takes about 260 ms, the rest of the tick under 1 ms; the file write it no longer waits
for took 250 to 650 ms. A protected document is much slower: MuPDF encrypts while it serializes, about 11 s for the
same file. MuPDF's undo journal (doc.journal_save) is no cheaper replacement: it holds the old versions of changed
objects, keyed to the fingerprint of the file as last written, so it cannot rebuild the current state without a full
write of the document as well, and it does not work for documents that exist only in memory."""
from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import threading
import time
import uuid


def recovery_dir() -> str:
    """The folder for snapshots. OPENREVU_RECOVERY_DIR overrides it (used by the tests)."""
    d = os.environ.get("OPENREVU_RECOVERY_DIR")
    if not d:
        if sys.platform.startswith("win"):
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            d = os.path.join(base, "OpenRevu", "recovery")
        elif sys.platform == "darwin":
            d = os.path.expanduser("~/Library/Application Support/OpenRevu/recovery")
        else:
            d = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "openrevu", "recovery")
    os.makedirs(d, exist_ok=True)
    return d


def new_id() -> str:
    return uuid.uuid4().hex


def _paths(doc_id: str):
    d = recovery_dir()
    return os.path.join(d, doc_id + ".pdf"), os.path.join(d, doc_id + ".json")


def pid_alive(pid: int) -> bool:
    """True if a process with this id is running."""
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # the process exists but belongs to someone else
    return True


def write(doc_id: str, data: bytes, original_path: str | None, title: str, encrypted: bool) -> None:
    """Write a snapshot. The files are replaced in one step, so a crash while writing never leaves half a file."""
    pdf, meta = _paths(doc_id)
    for path, payload, mode in ((pdf, data, "wb"), (meta, json.dumps({
            "id": doc_id, "pid": os.getpid(), "path": original_path, "title": title,
            "time": time.time(), "encrypted": encrypted}), "w")):
        tmp = path + ".tmp"
        with open(tmp, mode, **({"encoding": "utf-8"} if mode == "w" else {})) as f:
            f.write(payload)
        os.replace(tmp, path)


# One worker thread writes all snapshots, one after another. Two writes to the same files therefore never overlap
# (os.replace onto a file that another thread is writing fails on Windows). Python joins the worker at exit.
_writer = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="openrevu-autosave")
_lock = threading.Lock()
_pending: dict[str, list[concurrent.futures.Future]] = {}     # document id -> its queued or running writes


def write_later(doc_id: str, data: bytes, original_path: str | None, title: str, encrypted: bool) -> concurrent.futures.Future:
    """Queue write() on the worker thread and return its Future. The Future raises what write() raised.
    Its done-callbacks run on the worker thread (or, for a write that remove() cancels, on the caller's)."""
    with _lock:      # write is looked up when the job runs, so tests can replace it
        fut = _writer.submit(lambda: write(doc_id, data, original_path, title, encrypted))
        _pending.setdefault(doc_id, []).append(fut)

    def forget(f):
        with _lock:
            futs = _pending.get(doc_id, [])
            if f in futs:
                futs.remove(f)
                if not futs:
                    del _pending[doc_id]
    fut.add_done_callback(forget)
    return fut


def wait() -> None:
    """Block until every queued write has finished and its done-callbacks have run. The worker runs the callbacks
    of a job before it starts the next one, so an empty job queued last is enough."""
    _writer.submit(lambda: None).result()


def remove(doc_id: str) -> None:
    """Remove a document's snapshot. Writes still queued for it are dropped and a running one is waited for,
    so no snapshot of this document appears after remove() returns."""
    with _lock:
        futs = list(_pending.get(doc_id, []))
    running = [f for f in futs if not f.cancel()]
    if running:
        concurrent.futures.wait(running)
    for p in _paths(doc_id):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def _valid(m) -> bool:
    return (isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"].isalnum()
            and isinstance(m.get("pid"), int) and not isinstance(m["pid"], bool)
            and isinstance(m.get("time"), (int, float)) and isinstance(m.get("title", ""), str))


def entries(own_ids=()) -> list[dict]:
    """Snapshots that this session may offer, newest first. Each has the keys of the metadata file plus "file"
    (the snapshot PDF). Not offered: snapshots of documents that are open in this session (own_ids), and snapshots
    of another OpenRevu that is still running. Damaged entries and old leftovers are removed."""
    out = []
    d = recovery_dir()
    names = os.listdir(d)
    for name in names:
        if not name.endswith(".json"):
            continue
        meta_path = os.path.join(d, name)
        try:
            with open(meta_path, encoding="utf-8") as f:
                m = json.load(f)
            if not _valid(m):
                raise ValueError("not a snapshot description")
            pdf = os.path.join(d, m["id"] + ".pdf")
            if not os.path.exists(pdf):
                raise ValueError("missing snapshot")
        except (OSError, ValueError):        # includes a JSON file that holds a list, null, or other types
            for p in (meta_path, os.path.join(d, name[:-5] + ".pdf")):
                try:
                    os.remove(p)
                except OSError:
                    pass
            continue
        if m["id"] in own_ids:
            continue                         # a document that is open in this session
        if m["pid"] != os.getpid() and pid_alive(m["pid"]):
            continue                         # another running OpenRevu owns this one
        m["file"] = pdf
        out.append(m)
    now = time.time()
    for name in names:                       # leftovers: an interrupted write, or a snapshot without its description
        path = os.path.join(d, name)
        try:
            stale = now - os.path.getmtime(path) > 600       # a live session may be writing right now: wait ten minutes
            if name.endswith(".tmp") and stale:
                os.remove(path)
            elif name.endswith(".pdf") and not os.path.exists(os.path.join(d, name[:-4] + ".json")) and stale:
                os.remove(path)
        except OSError:
            pass
    return sorted(out, key=lambda m: -m.get("time", 0))
