import json
import os
import shutil
import threading
import time

import fitz
import pytest

from openrevu import recovery
from openrevu.core import Document

pytest.importorskip("PyQt5")
from PyQt5 import QtCore, QtWidgets

from openrevu.gui import Main
from tests.test_sheets import SET_V1, make_set


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def pdf(tmp_path):
    return make_set(tmp_path / "work.pdf", SET_V1)


def files(d):
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def choose(monkeypatch, label):
    """Make the recovery question answer with the button called `label`."""
    def exec_(self):
        for b in self.buttons():
            if b.text() == label:
                self._clicked = b
        return 0
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec_", exec_)
    monkeypatch.setattr(QtWidgets.QMessageBox, "clickedButton", lambda self: getattr(self, "_clicked", None))


def autosave(w):
    """One autosave tick, then wait until its background writes, and the writes it queued one after another,
    have ended and their results reached the window. Returns how many the tick itself queued."""
    n = w.autosave_now()
    while True:
        recovery.wait()
        QtWidgets.QApplication.processEvents()
        if not w._autosave_busy:
            return n


def wait_until(app, cond, timeout=3):
    """Run the event loop until cond() holds or the timeout passes."""
    t0 = time.time()
    while time.time() - t0 < timeout and not cond():
        app.processEvents(); time.sleep(0.01)


def crash(w):
    """Leave without cleanup, as a crash does: no close_tab, no removal of snapshots."""
    w.autosave_timer.stop()


def as_dead_session(isolated):
    """Mark every snapshot as written by a process that no longer exists."""
    for name in os.listdir(isolated):
        if name.endswith(".json"):
            p = os.path.join(isolated, name)
            m = json.load(open(p)); m["pid"] = 999999991; json.dump(m, open(p, "w"))


def test_unmodified_documents_are_not_snapshotted_and_modified_ones_are(app, pdf, isolated_recovery_dir):
    w = Main(pdf); app.processEvents()
    assert autosave(w) == 0 and files(isolated_recovery_dir) == []
    w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change()
    assert autosave(w) == 1 and len(files(isolated_recovery_dir)) == 2           # one .pdf and one .json
    assert autosave(w) == 0                                                       # nothing changed since
    w.doc.add_rect(0, fitz.Rect(70, 70, 90, 90)); w._after_change()
    assert autosave(w) == 1
    snap = [f for f in files(isolated_recovery_dir) if f.endswith(".pdf")][0]
    assert len(Document(os.path.join(isolated_recovery_dir, snap)).markups()) == 2     # the latest state
    assert not [f for f in files(isolated_recovery_dir) if f.endswith(".tmp")]         # writes are completed


def test_an_autosave_error_of_any_kind_goes_to_the_status_bar(app, pdf, isolated_recovery_dir, monkeypatch):
    w = Main(pdf); app.processEvents()
    w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change()

    def broken(self):
        raise RuntimeError("document closed")
    monkeypatch.setattr(Document, "_snapshot", broken)
    assert autosave(w) == 0
    assert "Autosave failed" in w.statusBar().currentMessage()
    assert files(isolated_recovery_dir) == []


def test_saving_or_discarding_removes_the_snapshot(app, pdf, tmp_path, isolated_recovery_dir, monkeypatch):
    w = Main(pdf)
    w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change(); autosave(w)
    assert len(files(isolated_recovery_dir)) == 2
    w.save()
    assert files(isolated_recovery_dir) == []
    w.doc.add_rect(0, fitz.Rect(70, 70, 90, 90)); w._after_change(); autosave(w)
    assert len(files(isolated_recovery_dir)) == 2
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Discard))
    w.close_tab(0)
    assert files(isolated_recovery_dir) == []
    w2 = Main(pdf); w2.doc.add_rect(0, fitz.Rect(1, 1, 9, 9)); w2._after_change(); autosave(w2)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(tmp_path / "copy.pdf"), "")))
    w2.save_as()
    assert files(isolated_recovery_dir) == [] and os.path.exists(tmp_path / "copy.pdf")


def test_crash_then_restart_restores_the_unsaved_work(app, pdf, tmp_path, isolated_recovery_dir, monkeypatch):
    w = Main(pdf)
    w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w.doc.add_length(0, [(0, 100), (100, 100)]); w.doc.set_scale(__import__("openrevu.core", fromlist=["Scale"]).Scale("m", 0.1))
    w._after_change(); autosave(w); crash(w)
    original_bytes = open(pdf, "rb").read()
    as_dead_session(isolated_recovery_dir)
    w2 = Main(); choose(monkeypatch, "Recover")
    assert w2.offer_recovery() == 1
    assert w2.tabs.count() == 1 and w2.tabs.tabText(0) == "*work.pdf (recovered)"
    d = w2.doc
    assert d.path is None and d.modified and len(d.markups()) == 2 and d.scale.unit == "m"       # the work and the scale are back
    assert len(files(isolated_recovery_dir)) == 2                                                # kept until the work is saved or closed
    assert autosave(w2) == 1 and recovery.entries({w2.doc.recovery_id}) == []               # re-written under this session
    assert open(pdf, "rb").read() == original_bytes                                              # the original file is untouched
    shown = {}
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda p, t, start, *a, **k: (shown.update(start=start) or str(tmp_path / "restored.pdf"), "")))
    w2.save()
    assert shown["start"] == pdf and len(Document(str(tmp_path / "restored.pdf")).markups()) == 2   # the dialog starts at the original name
    assert files(isolated_recovery_dir) == []                                                    # saved: the snapshot is no longer needed


def test_discard_and_ask_later(app, pdf, isolated_recovery_dir, monkeypatch):
    w = Main(pdf); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change(); autosave(w); crash(w)
    as_dead_session(isolated_recovery_dir)
    w2 = Main(); choose(monkeypatch, "Ask me later")
    assert w2.offer_recovery() == 0 and len(files(isolated_recovery_dir)) == 2 and w2.tabs.count() == 0     # kept for next time
    choose(monkeypatch, "Discard")
    assert w2.offer_recovery() == 0 and files(isolated_recovery_dir) == []
    assert w2.offer_recovery() == 0                                                                        # nothing left to offer


def test_a_live_session_and_our_own_snapshots_are_not_offered(app, pdf, isolated_recovery_dir):
    w = Main(pdf); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change(); autosave(w)
    assert recovery.entries({w.doc.recovery_id}) == []                                 # a document that is open here
    for name in os.listdir(isolated_recovery_dir):
        if name.endswith(".json"):
            p = os.path.join(isolated_recovery_dir, name)
            m = json.load(open(p)); m["pid"] = os.getppid(); json.dump(m, open(p, "w"))     # another live process
    assert recovery.entries() == [] and recovery.pid_alive(os.getppid()) and not recovery.pid_alive(999999991)
    assert not recovery.pid_alive(0) and not recovery.pid_alive(-5)


def test_protected_documents_are_never_written_in_plain(app, pdf, tmp_path, isolated_recovery_dir, monkeypatch):
    enc = str(tmp_path / "e.pdf"); Document(pdf).save_encrypted(enc, "secret")
    w = Main()
    w.open(enc, "secret")
    w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change(); autosave(w); crash(w)
    snap = [f for f in files(isolated_recovery_dir) if f.endswith(".pdf")][0]
    assert fitz.open(os.path.join(isolated_recovery_dir, snap)).needs_pass                 # encrypted on disk
    assert json.load(open(os.path.join(isolated_recovery_dir, [f for f in files(isolated_recovery_dir) if f.endswith(".json")][0])))["encrypted"]
    as_dead_session(isolated_recovery_dir)
    w2 = Main(); choose(monkeypatch, "Recover")
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("secret", True)))
    assert w2.offer_recovery() == 1 and len(w2.doc.markups()) == 1


def test_wrong_password_skips_that_document_only(app, pdf, tmp_path, isolated_recovery_dir, monkeypatch):
    enc = str(tmp_path / "e.pdf"); Document(pdf).save_encrypted(enc, "secret")
    w = Main(); w.open(enc, "secret"); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change()
    w.open(pdf); w.doc.add_rect(0, fitz.Rect(5, 5, 20, 20)); w._after_change(); autosave(w); crash(w)
    as_dead_session(isolated_recovery_dir)
    w2 = Main(); choose(monkeypatch, "Recover")
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))   # the user cancels the password
    assert w2.offer_recovery() == 1 and w2.tabs.count() == 1


def test_damaged_snapshots_are_cleaned_up_not_offered(app, isolated_recovery_dir, monkeypatch):
    d = recovery.recovery_dir()
    open(os.path.join(d, "a.json"), "w").write("{not json")
    json.dump({"id": "b", "pid": 999999991, "title": "x", "time": 1}, open(os.path.join(d, "b.json"), "w"))     # no pdf
    json.dump({"id": "c", "pid": 999999991, "title": "bad", "time": 2, "path": None}, open(os.path.join(d, "c.json"), "w"))
    open(os.path.join(d, "c.pdf"), "wb").write(b"%PDF-1.4 this is not a real pdf")
    open(os.path.join(d, "stale.pdf.tmp"), "wb").write(b"half")
    old = os.path.getmtime(os.path.join(d, "stale.pdf.tmp")) - 3600
    os.utime(os.path.join(d, "stale.pdf.tmp"), (old, old))
    ents = recovery.entries()
    assert [e["id"] for e in ents] == ["c"] and "a.json" not in files(d) and "b.json" not in files(d) and "stale.pdf.tmp" not in files(d)
    w = Main(); choose(monkeypatch, "Recover")
    warned = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    assert w.offer_recovery() == 0 and warned and "could not be recovered" in warned[0] and files(d) == []


def test_a_full_disk_does_not_interrupt_work(app, pdf, isolated_recovery_dir, monkeypatch):
    w = Main(pdf); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change()
    with monkeypatch.context() as m:               # not monkeypatch.undo(): that would also undo the isolated recovery folder
        m.setattr(recovery, "write", lambda *a, **k: (_ for _ in ()).throw(OSError("No space left on device")))
        autosave(w)
    assert "Autosave failed: No space left on device" in w.statusBar().currentMessage() and files(isolated_recovery_dir) == []
    assert autosave(w) == 1 and len(files(isolated_recovery_dir)) == 2       # the failed write is retried at the next tick


def test_timer_runs_autosave(app, pdf, isolated_recovery_dir, monkeypatch):
    monkeypatch.setenv("OPENREVU_AUTOSAVE_SECONDS", "0.05")
    w = Main(pdf); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change()
    wait_until(app, lambda: files(isolated_recovery_dir))
    assert len(files(isolated_recovery_dir)) == 2


# ---------- third-review regressions ----------
@pytest.mark.parametrize("content", ["[]", "null", "42", '"text"', '{"id": "x"}', '{"id": "x", "pid": null, "time": 1}',
                                      '{"id": "../evil", "pid": 999999991, "time": 1}', '{"id": "ab", "pid": true, "time": 1}',
                                      '{"id": "ab", "pid": 999999991, "time": "yesterday"}'])
def test_odd_descriptions_are_removed_and_never_break_recovery(app, isolated_recovery_dir, content):
    d = recovery.recovery_dir()
    open(os.path.join(d, "bad.json"), "w").write(content)
    open(os.path.join(d, "bad.pdf"), "wb").write(b"x")
    assert recovery.entries() == []                                  # no exception
    assert "bad.json" not in files(d) and "bad.pdf" not in files(d)  # removed, so it cannot break the next start either
    assert recovery.entries() == []


def test_a_snapshot_with_our_own_pid_but_an_unknown_id_is_offered(app, pdf, isolated_recovery_dir):
    """A restarted process (a container, a reused pid) must still see what its predecessor left."""
    w = Main(pdf); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change(); autosave(w)
    assert recovery.entries({w.doc.recovery_id}) == [] and len(recovery.entries()) == 1          # open here, or not
    assert len(recovery.entries({"someotherid"})) == 1                                           # same pid, not ours: offered


def test_orphan_files_are_cleaned_only_when_old(app, isolated_recovery_dir):
    d = recovery.recovery_dir()
    for name in ("fresh.pdf", "old.pdf", "fresh.pdf.tmp", "old.pdf.tmp"):
        open(os.path.join(d, name), "wb").write(b"x")
    old = os.path.getmtime(os.path.join(d, "old.pdf")) - 3600
    for name in ("old.pdf", "old.pdf.tmp"):
        os.utime(os.path.join(d, name), (old, old))
    recovery.entries()
    assert files(d) == ["fresh.pdf", "fresh.pdf.tmp"]                # a live session may be writing: young files stay


def test_recovered_work_survives_a_second_crash(app, pdf, tmp_path, isolated_recovery_dir, monkeypatch):
    w = Main(pdf); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change(); autosave(w); crash(w)
    as_dead_session(isolated_recovery_dir)
    w2 = Main(); choose(monkeypatch, "Recover"); assert w2.offer_recovery() == 1
    crash(w2)                                                         # the new session dies before its first autosave
    as_dead_session(isolated_recovery_dir)
    w3 = Main(); choose(monkeypatch, "Recover")
    assert w3.offer_recovery() == 1 and len(w3.doc.markups()) == 1    # the work is still there


# ---------- autosave off the GUI thread (#9) ----------
def slow_write(monkeypatch, seconds=0.5):
    """Make the low-level snapshot write slow, as on a 250 MB document."""
    real = recovery.write

    def write(*a, **k):
        time.sleep(seconds)
        real(*a, **k)
    monkeypatch.setattr(recovery, "write", write)


def test_autosave_writes_the_snapshot_in_the_background(app, pdf, isolated_recovery_dir, monkeypatch):
    w = Main(pdf); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change()
    slow_write(monkeypatch, 1.0)
    t0 = time.perf_counter()
    assert w.autosave_now() == 1
    assert time.perf_counter() - t0 < 0.5                      # the GUI thread does not wait for the disk
    recovery.wait()
    assert len(files(isolated_recovery_dir)) == 2


@pytest.mark.parametrize("started", [False, True])
def test_closing_a_document_while_its_snapshot_is_written_leaves_no_snapshot(app, pdf, isolated_recovery_dir, monkeypatch, started):
    """A late write would look like a crash of this session at the next start."""
    w = Main(pdf); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change()
    slow_write(monkeypatch, 0.3)
    gate = threading.Event()
    if not started:                                            # keep the writer busy, so the snapshot is still queued
        recovery.write_later("busy", b"x", None, "busy", False)
    real = recovery.write
    monkeypatch.setattr(recovery, "write", lambda *a, **k: (gate.set(), real(*a, **k)))
    assert w.autosave_now() == 1
    if started:
        assert gate.wait(2)                                    # the write is running now
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Discard))
    w.close_tab(0)
    recovery.wait(); app.processEvents()
    assert not [f for f in files(isolated_recovery_dir) if not f.startswith("busy")]


def test_a_background_write_error_reaches_the_status_bar(app, pdf, isolated_recovery_dir, monkeypatch):
    w = Main(pdf); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change()
    monkeypatch.setattr(recovery, "write", lambda *a, **k: (_ for _ in ()).throw(PermissionError("read-only folder")))
    w.autosave_now()                                           # no explicit wait: the window learns it by itself
    wait_until(app, lambda: "Autosave failed" in w.statusBar().currentMessage())
    assert w.statusBar().currentMessage() == "Autosave failed: read-only folder"


def test_a_document_is_not_snapshotted_again_while_its_write_is_pending(app, pdf, isolated_recovery_dir, monkeypatch):
    w = Main(pdf); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change()
    slow_write(monkeypatch, 0.3)
    snaps = []
    real = Document._snapshot
    monkeypatch.setattr(Document, "_snapshot", lambda self: snaps.append(1) or real(self))
    assert w.autosave_now() == 1
    w.doc.add_rect(0, fitz.Rect(70, 70, 90, 90)); w._after_change()
    assert w.autosave_now() == 0 and len(snaps) == 1           # the first write is still running
    assert autosave(w) == 0                                    # it ends; the changed document waits for the next tick
    assert autosave(w) == 1 and len(snaps) == 2
    snap = [f for f in files(isolated_recovery_dir) if f.endswith(".pdf")][0]
    assert len(Document(os.path.join(isolated_recovery_dir, snap)).markups()) == 2


def test_autosave_waits_while_a_mouse_button_is_down(app, pdf, isolated_recovery_dir, monkeypatch):
    from openrevu import gui
    w = Main(pdf); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change()
    monkeypatch.setattr(gui, "AUTOSAVE_RETRY_MS", 50)
    monkeypatch.setattr(QtWidgets.QApplication, "mouseButtons", staticmethod(lambda: QtCore.Qt.LeftButton))
    assert w.autosave_now() == 0 and w.autosave_now() == 0     # mid-drag: nothing is serialized
    recovery.wait(); app.processEvents()
    assert files(isolated_recovery_dir) == []
    monkeypatch.setattr(QtWidgets.QApplication, "mouseButtons", staticmethod(lambda: QtCore.Qt.NoButton))
    wait_until(app, lambda: files(isolated_recovery_dir))      # the button is released: the retry writes the snapshot
    recovery.wait()
    assert len(files(isolated_recovery_dir)) == 2


def test_remove_drops_every_queued_write_of_a_document(isolated_recovery_dir, monkeypatch):
    """Two writes of one document queued behind another: remove() must stop both, not only the latest."""
    gate = threading.Event()
    real = recovery.write
    monkeypatch.setattr(recovery, "write", lambda *a, **k: (gate.wait(2), real(*a, **k)))
    recovery.write_later("busy", b"x", None, "busy", False)
    recovery.write_later("dup", b"1", None, "dup", False)
    recovery.write_later("dup", b"2", None, "dup", False)
    recovery.remove("dup")
    gate.set(); recovery.wait()
    assert not [f for f in files(isolated_recovery_dir) if f.startswith("dup")]


def test_autosave_keeps_one_snapshot_in_memory_at_a_time(app, pdf, tmp_path, isolated_recovery_dir, monkeypatch):
    """Each snapshot is a whole document: with several open, the next is made only when the previous is written."""
    other = str(tmp_path / "other.pdf"); shutil.copy(pdf, other)
    w = Main(pdf); w.open(other)
    for i in range(2):
        w.tabs.widget(i).doc.add_rect(0, fitz.Rect(10, 10, 60, 60))
    w._after_change()
    slow_write(monkeypatch, 0.3)
    assert w.autosave_now() == 1                               # the second document waits for the first write
    wait_until(app, lambda: len(files(isolated_recovery_dir)) == 4)   # then follows without another tick
    recovery.wait(); app.processEvents()
    assert not w._autosave_busy
