#!/usr/bin/env python3
"""Drive the real OpenRevu window for verification, and keep the proof.

    .venv/bin/python .claude/skills/verify/scripts/orv.py doctor
    .venv/bin/python .claude/skills/verify/scripts/orv.py run JOURNEY.py [--visible] [--timeout SECONDS] [--keep-work]
    .venv/bin/python .claude/skills/verify/scripts/orv.py cleanup

A journey is a Python file with `def journey(s):`, where `s` is a Session (below). Each run gets:
    .verify/work/<run-id>/      scratch: sample PDFs, recovery folder, files the journey saves. Removed when the run ends.
    .verify/evidence/<run-id>/  proof: steps.md, result.json, screenshots, CLI transcripts, copies of output files. Kept.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
STATE = REPO / ".verify"
WORK_ROOT = STATE / "work"
EVIDENCE_ROOT = STATE / "evidence"
CANCEL = object()  # answer(...) value that cancels a dialog


class JourneyFailed(AssertionError):
    pass


# ---------------------------------------------------------------- session
class Session:
    """One driven OpenRevu window. Every action is a real user path: menu actions, tool actions, mouse and key events
    on the canvas and widgets. Modal dialogs are answered from a queue the journey fills with `answer(...)`; a dialog
    nobody scripted fails the run (it would block a real user too, and the journey did not expect it)."""

    def __init__(self, run_id: str, work: Path, evidence: Path):
        from PyQt5 import QtWidgets as W
        self.run_id, self.work, self.evidence = run_id, work, evidence
        self.app = W.QApplication.instance() or W.QApplication(["openrevu"])
        self.app.setStyle("Fusion")          # gui.main() does the same
        self.w = None                        # openrevu.gui.Main
        self.expected: dict = {}
        self._n = 0
        self._replies: deque = deque()
        self._errors: list[str] = []
        self._log = (evidence / "steps.md").open("a", encoding="utf-8")
        sys.excepthook = lambda t, v, tb: self._errors.append("".join(traceback.format_exception(t, v, tb)))
        self._patch_dialogs()

    # ---- transcript
    def step(self, text: str):
        self._n += 1
        line = f"{self._n:03d}  {text}"
        print(line, flush=True)
        self._log.write(line + "\n")
        self._log.flush()

    def check(self, cond, what: str):
        """Record an observed fact. A false one stops the journey."""
        self.step(("PASS  " if cond else "FAIL  ") + what)
        if not cond:
            raise JourneyFailed(what)

    # ---- setup
    def samples(self) -> Path:
        """The manual-test-plan sample files (docs/testing/make_samples.py) in this run's work folder.
        Sets self.expected from their expected.json."""
        out = self.work / "samples"
        if not (out / "plan_v1.pdf").exists():
            r = subprocess.run([sys.executable, str(REPO / "docs" / "testing" / "make_samples.py"), str(out)],
                               capture_output=True, text=True, cwd=REPO)
            if r.returncode:
                raise JourneyFailed(f"make_samples.py failed:\n{r.stdout}\n{r.stderr}")
        self.expected = json.loads((out / "expected.json").read_text(encoding="utf-8"))
        self.step(f"samples ready in {out}")
        return out

    def launch(self, *pdfs, size=(1500, 950)):
        """Start the window the way `openrevu a.pdf b.pdf` does (gui.main), including the recovery offer."""
        from PyQt5 import QtCore
        from openrevu.gui import Main
        self.w = Main(str(pdfs[0]) if pdfs else None)
        for extra in pdfs[1:]:
            self.w.open(str(extra))
        self.w.resize(*size)
        self.w.show()
        self.w.activateWindow()
        QtCore.QTimer.singleShot(300, self.w.offer_recovery)
        self.settle(500)
        self.step(f"launched OpenRevu with {[Path(p).name for p in pdfs] or 'no file'}; status: {self.status()!r}")

    def settle(self, ms: int = 50):
        """Let Qt process events for at least ms milliseconds, then surface any exception raised in a handler."""
        from PyQt5 import QtCore
        end = time.monotonic() + ms / 1000
        while True:
            self.app.processEvents(QtCore.QEventLoop.AllEvents, 20)
            if time.monotonic() >= end:
                break
            time.sleep(0.01)
        if self._errors:
            err = self._errors.pop(0)
            self.step("ERROR in an event handler:\n" + err)
            raise JourneyFailed("unhandled exception in the app (see steps.md)")

    # ---- input: menus, tools, keys, typing
    def menu(self, path: str):
        """Choose a menu item, e.g. menu("File > Save"). Fails if the item is missing or disabled."""
        top, _, item = (x.strip() for x in path.partition(">"))
        bar = self.w.menuBar()
        m = next((a.menu() for a in bar.actions() if a.text().replace("&", "") == top), None)
        if m is None:
            raise JourneyFailed(f"no menu {top!r}; menus are {[a.text().replace('&', '') for a in bar.actions()]}")
        act = next((a for a in m.actions() if a.text().replace("&", "") == item), None)
        if act is None:
            raise JourneyFailed(f"no item {item!r} in {top}; items are {[a.text() for a in m.actions() if a.text()]}")
        if not act.isEnabled():
            raise JourneyFailed(f"menu item {path!r} is disabled")
        self.step(f"menu  {path}")
        act.trigger()
        self.settle()

    def tool(self, name: str):
        """Pick a tool from the toolbar (names as in openrevu/gui.py TOOL_GROUPS: Select, Pan, Rectangle, Length, ...)."""
        act = self.w.tool_actions.get(name)
        if act is None:
            raise JourneyFailed(f"no tool {name!r}; tools are {sorted(self.w.tool_actions)}")
        if not act.isEnabled():
            raise JourneyFailed(f"tool {name!r} is disabled")
        self.step(f"tool  {name}")
        act.trigger()
        self.settle()

    def panel(self, name: str):
        """Click a side-panel tab by name: Thumbnails, Bookmarks, Search, Layers, Sheets (left);
        Properties, Tool Chest, Measurements (right)."""
        from PyQt5 import QtCore, QtTest
        for tabs, dock in ((self.w.left_tabs, self.w.left_dock), (self.w.right_tabs, self.w.right_dock)):
            for i in range(tabs.count()):
                if self.w.panel_name(tabs, i) == name:
                    if dock.isHidden():
                        raise JourneyFailed(f"the {dock.windowTitle()!r} dock is hidden; open it from the View menu")
                    bar = tabs.tabBar()
                    self.step(f"panel {name}")
                    QtTest.QTest.mouseClick(bar, QtCore.Qt.LeftButton, pos=bar.tabRect(i).center())
                    self.settle()
                    return
        raise JourneyFailed(f"no panel {name!r}")

    def button(self, text: str, within=None):
        """Click the visible push button whose label is text (ignoring '&' and a trailing '…')."""
        from PyQt5 import QtCore, QtTest, QtWidgets as W
        norm = lambda t: t.replace("&", "").rstrip("…").strip()
        root = within or self.w
        found = [b for b in root.findChildren(W.QAbstractButton) if b.isVisible() and norm(b.text()) == norm(text)]
        if len(found) != 1:
            raise JourneyFailed(f"{len(found)} visible buttons labelled {text!r}")
        if not found[0].isEnabled():
            raise JourneyFailed(f"button {text!r} is disabled")
        self.step(f"button {text}")
        QtTest.QTest.mouseClick(found[0], QtCore.Qt.LeftButton)
        self.settle()

    def cell(self, table, row: int, col: int, ctrl: bool = False, double: bool = False):
        """Click a table cell (Ctrl+click to add to the selection, double-click to edit)."""
        from PyQt5 import QtCore, QtTest
        item = table.item(row, col)
        if item is None:
            raise JourneyFailed(f"no cell ({row}, {col}) in {table.objectName() or type(table).__name__}")
        table.scrollToItem(item)
        pos = table.visualItemRect(item).center()
        mods = QtCore.Qt.ControlModifier if ctrl else QtCore.Qt.NoModifier
        self.step(f"{'dbl-click' if double else 'ctrl+click' if ctrl else 'click'} cell ({row}, {col}) {item.text()!r}")
        if double:
            self.dclick(table.viewport(), pos, mods)
        else:
            QtTest.QTest.mouseClick(table.viewport(), QtCore.Qt.LeftButton, mods, pos)
        self.settle()

    def key(self, seq: str, widget=None):
        """Press a key or shortcut, e.g. key("Ctrl+S"), key("Delete"), key("R"). Goes to the focused widget."""
        from PyQt5 import QtGui, QtTest
        ks = QtGui.QKeySequence(seq)
        if ks.count() != 1:
            raise JourneyFailed(f"cannot parse key {seq!r}")
        combo = ks[0]
        target = widget or self.app.focusWidget() or (self.w.cv or self.w)
        from PyQt5 import QtCore
        if self.app.activeWindow() is not target.window():   # window shortcuts need an active window, as after a
            target.window().activateWindow()                  # user clicks into it (a closed dialog leaves none)
            self.settle()
        mods = QtCore.Qt.KeyboardModifiers(combo & int(QtCore.Qt.KeyboardModifierMask))
        self.step(f"key   {seq}  -> {type(target).__name__}")
        QtTest.QTest.keyClick(target, combo & ~int(QtCore.Qt.KeyboardModifierMask), mods)
        self.settle()

    def type(self, widget, text: str, enter: bool = True):
        """Click into a text field, replace its text by typing, and press Enter (like a user)."""
        from PyQt5 import QtCore, QtTest
        QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton)
        widget.selectAll() if hasattr(widget, "selectAll") else None
        self.step(f"type  {text!r} into {widget.objectName() or type(widget).__name__}" + (" + Enter" if enter else ""))
        QtTest.QTest.keyClicks(widget, text)
        if enter:
            QtTest.QTest.keyClick(widget, QtCore.Qt.Key_Return)
        self.settle()

    # ---- input: mouse on the page, in PDF points
    def _vp(self, page: int, x: float, y: float):
        cv = self.w.cv
        sp = cv._to_scene(page, (x, y))
        pos = cv.mapFromScene(sp)
        if not cv.viewport().rect().contains(pos):      # a user scrolls the point into view before clicking it
            cv.ensureVisible(sp.x(), sp.y(), 1, 1, 60, 60)
            self.settle()
            pos = cv.mapFromScene(sp)
        return pos

    def click(self, page: int, x: float, y: float, double: bool = False, right: bool = False):
        from PyQt5 import QtCore, QtTest
        vp = self.w.cv.viewport()
        pos = self._vp(page, x, y)
        btn = QtCore.Qt.RightButton if right else QtCore.Qt.LeftButton
        self.step(f"{'dbl-click' if double else 'right-click' if right else 'click'} page {page + 1} at ({x:g}, {y:g}) pt")
        if double:
            self.dclick(vp, pos)
        else:
            QtTest.QTest.mouseClick(vp, btn, pos=pos)
        self.settle()

    @staticmethod
    def dclick(widget, pos, mods=None):
        """A real double-click: press, release, double-click, release. QTest.mouseDClick omits the last release, so
        item views never emit `activated` (the Recent files list opens a file on it)."""
        from PyQt5 import QtCore, QtGui, QtTest, QtWidgets as W
        mods = mods if mods is not None else QtCore.Qt.NoModifier
        QtTest.QTest.mousePress(widget, QtCore.Qt.LeftButton, mods, pos)
        QtTest.QTest.mouseRelease(widget, QtCore.Qt.LeftButton, mods, pos)
        W.QApplication.sendEvent(widget, QtGui.QMouseEvent(QtCore.QEvent.MouseButtonDblClick, QtCore.QPointF(pos),
                                                          QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, mods))
        QtTest.QTest.mouseRelease(widget, QtCore.Qt.LeftButton, mods, pos)

    def drag(self, page: int, a, b, steps: int = 6):
        """Press at a, move with the button held, release at b. Points are (x, y) in PDF points on that page."""
        from PyQt5 import QtCore, QtGui, QtTest, QtWidgets as W
        vp = self.w.cv.viewport()
        pa = self._vp(page, *a)
        pb = self._vp(page, *b)
        pa = self._vp(page, *a)      # b may have scrolled the view
        self.step(f"drag  page {page + 1} from ({a[0]:g}, {a[1]:g}) to ({b[0]:g}, {b[1]:g}) pt")
        QtTest.QTest.mousePress(vp, QtCore.Qt.LeftButton, pos=pa)
        for i in range(1, steps + 1):
            p = QtCore.QPointF(pa.x() + (pb.x() - pa.x()) * i / steps, pa.y() + (pb.y() - pa.y()) * i / steps)
            # QTest.mouseMove sends no held button; a real drag does
            W.QApplication.sendEvent(vp, QtGui.QMouseEvent(QtCore.QEvent.MouseMove, p, QtCore.Qt.NoButton,
                                                          QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
            self.settle(10)
        QtTest.QTest.mouseRelease(vp, QtCore.Qt.LeftButton, pos=pb)
        self.settle()

    # ---- dialogs
    def answer(self, *replies):
        """Queue answers for the next modal dialogs, in order. Each is (method, value):
            ("getDouble", 20)  ("getInt", 3)  ("getText", "A-")  ("getItem", "ft")  ("getMultiLineText", "x")
            ("getSaveFileName", path)  ("getOpenFileName", path)  ("getOpenFileNames", [paths])  ("getExistingDirectory", path)
            ("question", "Yes" | "No" | "Save" | "Discard" | "Cancel")  ("warning" | "information" | "critical", None)
        Use CANCEL as the value to cancel. The dialog's title and text go into steps.md."""
        self._replies.extend(replies)

    def _patch_dialogs(self):
        from PyQt5 import QtWidgets as W
        session = self

        def handler(cls, name):
            def f(*a, **k):
                parent, rest = (a[0], a[1:]) if a else (None, ())
                shown = " | ".join(str(x) for x in rest[:2] if isinstance(x, str))
                if not session._replies:
                    session.step(f"FAIL  unscripted dialog {cls.__name__}.{name}: {shown}")
                    raise JourneyFailed(f"unscripted dialog {cls.__name__}.{name}: {shown}")
                want, value = session._replies.popleft()
                if want != name:
                    session.step(f"FAIL  expected dialog {want}, got {cls.__name__}.{name}: {shown}")
                    raise JourneyFailed(f"expected dialog {want}, got {cls.__name__}.{name}: {shown}")
                session.step(f"dialog {cls.__name__}.{name}: {shown}  -> {'CANCEL' if value is CANCEL else value!r}")
                return session._reply(name, value)
            return staticmethod(f)

        for cls, names in ((W.QInputDialog, ("getText", "getInt", "getDouble", "getItem", "getMultiLineText")),
                           (W.QFileDialog, ("getOpenFileName", "getOpenFileNames", "getSaveFileName", "getExistingDirectory")),
                           (W.QMessageBox, ("question", "warning", "information", "critical"))):
            for n in names:
                setattr(cls, n, handler(cls, n))

    @staticmethod
    def _reply(name, value):
        from PyQt5 import QtWidgets as W
        cancel = value is CANCEL
        if name in ("getText", "getMultiLineText", "getItem"):
            return ("", False) if cancel else (str(value), True)
        if name == "getInt":
            return (0, False) if cancel else (int(value), True)
        if name == "getDouble":
            return (0.0, False) if cancel else (float(value), True)
        if name in ("getOpenFileName", "getSaveFileName"):
            return ("", "") if cancel else (str(value), "")
        if name == "getOpenFileNames":
            return ([], "") if cancel else ([str(v) for v in value], "")
        if name == "getExistingDirectory":
            return "" if cancel else str(value)
        if name == "question":
            return W.QMessageBox.Cancel if cancel else getattr(W.QMessageBox, value)
        return W.QMessageBox.Ok

    def modal(self, trigger, handle=None, timeout_ms: int = 5000):
        """For a dialog that runs its own exec_() (Measurement summary, About, the recovery offer): call trigger(),
        then handle(dialog) once the dialog is on screen, then close it if handle left it open. Returns handle's result."""
        from PyQt5 import QtCore, QtWidgets as W
        before = {id(x) for x in self.app.topLevelWidgets() if x.isVisible()}
        box = {"result": None, "seen": False}
        t0 = time.monotonic()

        def poll():
            dlg = next((x for x in self.app.topLevelWidgets() if isinstance(x, W.QDialog) and x.isVisible()
                        and id(x) not in before), None)
            if dlg is None:
                if time.monotonic() - t0 < timeout_ms / 1000:
                    QtCore.QTimer.singleShot(50, poll)
                return
            box["seen"] = True
            self.step(f"modal {type(dlg).__name__} {dlg.windowTitle()!r} is open")
            try:
                box["result"] = handle(dlg) if handle else None
            finally:
                if dlg.isVisible():
                    dlg.done(0)

        QtCore.QTimer.singleShot(50, poll)
        trigger()
        self.settle()
        if not box["seen"]:
            raise JourneyFailed("the expected dialog never appeared")
        return box["result"]

    # ---- observation
    def shot(self, name: str, widget=None) -> Path:
        """Save a screenshot of the window (or a widget) into the evidence folder."""
        self.settle(100)
        p = self.evidence / f"{self._n + 1:03d}-{name}.png"
        (widget or self.w).grab().save(str(p))
        self.step(f"shot  {p.name}")
        return p

    def status(self) -> str:
        return self.w.statusBar().currentMessage() if self.w else ""

    @staticmethod
    def table(widget) -> list[list[str]]:
        """Rows of a QTableWidget as text, as the user sees them (Markups List: s.w.mk_table, Sheets: s.w.sheets_table,
        Measurements: s.w.meas_table)."""
        return [[(widget.item(r, c).text() if widget.item(r, c) else "") for c in range(widget.columnCount())]
                for r in range(widget.rowCount())]

    def keep(self, path, name: str | None = None) -> Path:
        """Copy an output file (a saved PDF, an exported CSV) into the evidence folder."""
        dst = self.evidence / (name or Path(path).name)
        shutil.copy2(path, dst)
        self.step(f"kept  {Path(path).name} -> evidence/{dst.name}")
        return dst

    def cli(self, *args) -> subprocess.CompletedProcess:
        """Run openrevu-cli in its own process (a second, independent view of saved files). Records the command,
        stdout, stderr and exit code in the evidence folder."""
        cmd = [sys.executable, "-m", "openrevu.cli", *map(str, args)]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=self.work, env=_child_env(self.work))
        p = self.evidence / f"{self._n + 1:03d}-cli-{args[0]}.txt"
        p.write_text(f"$ openrevu-cli {' '.join(map(str, args))}\nexit code: {r.returncode}\n--- stdout\n{r.stdout}"
                     f"--- stderr\n{r.stderr}", encoding="utf-8")
        self.step(f"cli   openrevu-cli {' '.join(map(str, args))}  -> exit {r.returncode}  ({p.name})")
        return r

    def close(self):
        self._log.close()


# ---------------------------------------------------------------- helpers
def _child_env(work: Path) -> dict:
    env = dict(os.environ)
    env["OPENREVU_NO_SETTINGS"] = "1"
    env["OPENREVU_RECOVERY_DIR"] = str(work / "recovery")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    return env


def _git(*args) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, cwd=REPO).stdout.strip()
    except OSError:
        return ""


def _alive(pid: int) -> bool:
    if os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _kill(pid: int):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        os.kill(pid, signal.SIGTERM)


# ---------------------------------------------------------------- commands
def cmd_run(a) -> int:
    journey_path = Path(a.journey).resolve()
    if not journey_path.exists():
        print(f"no such journey: {journey_path}")
        return 2
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{journey_path.stem}-{os.getpid()}"
    work, evidence = WORK_ROOT / run_id, EVIDENCE_ROOT / run_id
    work.mkdir(parents=True)
    evidence.mkdir(parents=True)
    (work / "pid").write_text(str(os.getpid()), encoding="utf-8")

    # isolation: never the user's settings or recovery folder; headless unless asked
    if not a.visible:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["OPENREVU_NO_SETTINGS"] = "1"
    os.environ["OPENREVU_RECOVERY_DIR"] = str(work / "recovery")
    sys.path.insert(0, str(REPO))

    result = {"run_id": run_id, "journey": str(journey_path), "ok": False, "error": None,
              "git": _git("rev-parse", "--short", "HEAD"), "dirty": bool(_git("status", "--porcelain", "--", "openrevu")),
              "platform": platform.platform(), "python": sys.version.split()[0],
              "qpa": os.environ.get("QT_QPA_PLATFORM", "native"), "started": time.strftime("%Y-%m-%dT%H:%M:%S")}

    def finish(code):
        result["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        (evidence / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        if not a.keep_work:
            shutil.rmtree(work, ignore_errors=True)
        print(f"\n{'OK' if result['ok'] else 'FAILED'}: evidence in {evidence}")
        return code

    def watchdog():
        result["error"] = f"timeout after {a.timeout}s"
        with (evidence / "steps.md").open("a", encoding="utf-8") as f:
            f.write(f"FAIL  timeout after {a.timeout}s\n")
        finish(3)
        os._exit(3)

    timer = threading.Timer(a.timeout, watchdog)
    timer.daemon = True
    timer.start()

    import openrevu
    result["version"] = openrevu.__version__
    result["openrevu_from"] = str(Path(openrevu.__file__).parent)
    s = Session(run_id, work, evidence)
    s.step(f"run {run_id}: OpenRevu {openrevu.__version__} from {result['openrevu_from']}, git {result['git']}"
           f"{' (openrevu/ has uncommitted changes)' if result['dirty'] else ''}, QPA {result['qpa']}")
    code = 1
    try:
        spec = importlib.util.spec_from_file_location(f"journey_{journey_path.stem}", journey_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.journey(s)
        if s._replies:
            raise JourneyFailed(f"scripted dialog answers never used: {list(s._replies)}")
        result["ok"] = True
        code = 0
    except Exception as e:  # noqa: BLE001 - every failure must land in the evidence
        result["error"] = f"{type(e).__name__}: {e}"
        s.step(f"FAIL  {result['error']}")
        if not isinstance(e, JourneyFailed):
            s.step(traceback.format_exc())
        try:
            if s.w is not None:
                s.shot("failure")
        except Exception:  # noqa: BLE001
            pass
    finally:
        timer.cancel()
        if s.w is not None:
            for i in range(s.w.tabs.count()):         # close without the unsaved-changes prompt
                cv = s.w.tabs.widget(i)
                if hasattr(cv, "doc"):
                    cv.doc.modified = False
            s.w.close()
            s.app.processEvents()
        s.close()
    return finish(code)


def cmd_doctor(a) -> int:
    ok = True

    def line(good, what, required=True):
        nonlocal ok
        ok = ok and (good or not required)
        print(f"{'ok  ' if good else ('FAIL' if required else 'warn')}  {what}")

    venv = REPO / ".venv"
    line(Path(sys.prefix).resolve() == venv.resolve(), f"python is the repo venv ({sys.executable})")
    sys.path.insert(0, str(REPO))
    try:
        import openrevu
        src = Path(openrevu.__file__).resolve().parent
        line(src == (REPO / "openrevu").resolve(), f"openrevu imports from {src}")
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        line(f'version = "{openrevu.__version__}"' in pyproject, f"openrevu {openrevu.__version__} matches pyproject.toml")
    except Exception as e:  # noqa: BLE001
        line(False, f"import openrevu: {e}")
    for mod in ("PyQt5.QtTest", "pymupdf"):
        try:
            importlib.import_module(mod)
            line(True, f"import {mod}")
        except Exception as e:  # noqa: BLE001
            line(False, f"import {mod}: {e}")
    rev = _git("rev-parse", "--short", "HEAD")
    dirty = _git("status", "--porcelain", "--", "openrevu")
    line(True, f"git {rev}" + (" with uncommitted changes in openrevu/" if dirty else ", openrevu/ clean"))
    env = _child_env(STATE / "doctor")
    r = subprocess.run([sys.executable, "-m", "openrevu", "--selftest"], capture_output=True, text=True, cwd=REPO,
                       env=env, timeout=120)
    line(r.returncode == 0, f"openrevu --selftest: {(r.stdout or r.stderr).strip().splitlines()[-1:] or ['(no output)']}")
    for tool in ("tesseract", "gs", "verapdf"):
        line(shutil.which(tool) is not None, f"{tool} on PATH (OCR / PDF/A journeys need it)", required=False)
    stale = sorted(p.name for p in WORK_ROOT.glob("*")) if WORK_ROOT.exists() else []
    line(not stale, f"no leftover work folders{'' if not stale else ': ' + ', '.join(stale) + ' (run cleanup)'}",
         required=False)
    print("doctor: ready" if ok else "doctor: NOT ready")
    return 0 if ok else 1


def cmd_cleanup(a) -> int:
    """Stop runs this harness started that are still alive, and delete their work folders. Never touches evidence."""
    if not WORK_ROOT.exists():
        print("nothing to clean")
        return 0
    for d in sorted(WORK_ROOT.iterdir()):
        pidfile = d / "pid"
        if pidfile.exists():
            pid = int(pidfile.read_text(encoding="utf-8").strip() or 0)
            if pid and pid != os.getpid() and _alive(pid):
                print(f"stopping run {d.name} (pid {pid})")
                _kill(pid)
                time.sleep(0.5)
        shutil.rmtree(d, ignore_errors=True)
        print(f"removed {d}")
    print(f"evidence kept in {EVIDENCE_ROOT}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="orv", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a journey")
    r.add_argument("journey")
    r.add_argument("--visible", action="store_true", help="show the window (needs a display) instead of offscreen")
    r.add_argument("--timeout", type=float, default=180, help="seconds before the run is stopped (default 180)")
    r.add_argument("--keep-work", action="store_true", help="keep .verify/work/<run-id> for debugging")
    sub.add_parser("doctor", help="read-only readiness check")
    sub.add_parser("cleanup", help="stop stranded runs and delete work folders (keeps evidence)")
    a = p.parse_args(argv)
    return {"run": cmd_run, "doctor": cmd_doctor, "cleanup": cmd_cleanup}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
