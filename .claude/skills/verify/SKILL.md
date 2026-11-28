---
name: verify
description: Drive the real OpenRevu window (PyQt5 desktop app) and openrevu-cli the way a user does, and keep screenshots, transcripts and output files as proof. Use to prove a GUI or CLI change works in the real app, to reproduce a user-reported GUI bug, or to check a manual test plan step.
---

# Verify OpenRevu in the real app

The harness is one script, `.claude/skills/verify/scripts/orv.py`. It starts the real `openrevu.gui.Main` window in its own process, headless (`QT_QPA_PLATFORM=offscreen`). A **journey** (a Python file with `def journey(s):`) then drives that window with real input: menu actions, toolbar tools, mouse events on the page, and keystrokes. Every run is isolated: preferences stay in memory (`OPENREVU_NO_SETTINGS=1`) and the recovery folder is private to the run, so runs can go side by side and never touch the user's own OpenRevu.

All commands run from the repo root with the repo venv (see `CLAUDE.md` for how to create it).

## 1. Doctor

```
.venv/bin/python .claude/skills/verify/scripts/orv.py doctor
```

Read-only. Ready means it prints `doctor: ready`: the venv python, `openrevu` imported from this checkout's `openrevu/` (not `build/` or a site-packages copy), the version matches `pyproject.toml`, and `openrevu --selftest` passes. It also reports the git revision and whether `openrevu/` has uncommitted changes, `warn`s when `tesseract`, `gs` or `verapdf` is missing (only OCR and PDF/A journeys need them), and lists leftover work folders. Run it first, and again whenever a run behaves strangely.

## 2. Launch and drive: run a journey

```
.venv/bin/python .claude/skills/verify/scripts/orv.py run JOURNEY.py [--visible] [--timeout 180] [--keep-work]
```

A run starts the app, runs the journey, closes the app, and exits `0` (all checks passed), `1` (a check or the app failed) or `3` (timeout). Ready is built in: `s.launch()` returns only after the window is shown and the recovery offer has had its turn. There is no server to keep alive and no separate teardown: the process ends with the run.

Shipped journeys, one per proven path in the feature map:

| Journey | Proves |
|---|---|
| `journeys/markup_roundtrip.py` | draw, select, edit properties, delete, undo, save, read back with `openrevu-cli csv` |
| `journeys/measure_calibrate.py` | calibrate, Length, Fill room area, Measurement summary, summary CSV export |
| `journeys/sheets_revision.py` | Sheets panel, multi-select, Revision form, sheet list CSV. Fails until issue #65 is fixed |
| `journeys/open_navigate.py` | start page, Open PDF with a password, PgDown, zoom, Ctrl+W, reopen from Recent files |
| `journeys/crash_recovery.py` | autosave, a simulated crash, the recovery offer, saving the recovered copy |
| `journeys/cli_batch.py` | `openrevu-cli` csv, compare, protected input, error exit codes (no window) |

For a new change, write a journey that walks the feature's user path from [`features/`](features/README.md). Put throwaway journeys in your scratchpad and keep a journey in `journeys/` once it proves a feature-map path. Minimal shape:

```python
def journey(s):
    pdf = s.samples() / "plan_v1.pdf"        # known-content samples; s.expected holds their right answers
    s.launch(pdf)
    s.tool("Rectangle")
    s.drag(0, (520, 260), (640, 340))        # page index 0, points in PDF coordinates
    s.check(len(s.table(s.w.mk_table)) == 1, "Markups List shows the new rectangle")
    s.shot("drawn")
    s.menu("File > Save")
    s.check(s.status() == "Saved", f"status bar: {s.status()!r}")
```

The `Session` methods (full docstrings in `orv.py`):

- **Input**: `menu("File > Save")`, `tool("Length")`, `panel("Sheets")`, `button("Revision", within=dlg)`, `cell(table, row, col, ctrl=, double=)`, `click(page, x, y, double=, right=)`, `drag(page, a, b)`, `dclick(widget, pos)`, `key("Ctrl+Z", widget)`, `type(widget, text, enter=)`.
- **Dialogs**: `answer(("getDouble", 20), ("getItem", "ft"), ...)` queues answers for the static `QInputDialog` / `QFileDialog` / `QMessageBox` calls, in order, before the action that opens them. `CANCEL` cancels. `modal(trigger, handle)` handles a dialog that runs its own `exec_()` (Measurement summary, form dialogs like Add revision, the recovery offer): `handle(dlg)` runs while it is open, then the dialog is closed.
- **Observe**: `status()` (status bar text), `table(widget)` (rows as the user sees them: `s.w.mk_table`, `s.w.sheets_table`, `s.w.meas_table`), `shot(name, widget=)`, `cli(*args)` (runs `openrevu-cli` in a separate process), `keep(path)` (copies an output file into the evidence).
- **Record**: `check(cond, what)` writes `PASS`/`FAIL` and stops the journey on the first failure. `step(text)` writes a note.

Stable handles: menu and item texts from `_build_menus` in `openrevu/gui.py`, tool names from `TOOL_GROUPS` and `SHORTCUTS` there, panel names from `_add_panel` calls, and dialog titles and labels from each `QInputDialog`/`QFileDialog` call. Address the page in PDF points. The harness maps them to the screen and scrolls the point into view first, the way a user would.

## 3. Evidence

Every run writes `.verify/evidence/<run-id>/`, which is git-ignored and kept after cleanup:

- `steps.md`: every action, dialog (with its title and text) and check, numbered. Its first line has the OpenRevu version, git revision, dirty flag and QPA platform.
- `result.json`: `ok`, `error`, versions, platform, start and finish times.
- `NNN-<name>.png` screenshots, `NNN-cli-<cmd>.txt` CLI transcripts (command, exit code, stdout, stderr), and files kept with `keep()`.
- `NNN-failure.png`: the window at the moment a run failed.

What counts as proof:

- **Drive the user path.** Use menus, tools, mouse and keys. Calling `s.w.doc.add_*` or other model methods sets up state a user could not reach and proves nothing about the GUI. Read model state only to observe.
- **Capture the action and its result.** Take a screenshot after each meaningful step, not just at the end, and `check` what the user would see: table rows, status bar, tab title (`*` means unsaved).
- **Check side effects from outside.** After a save or export, read the file from a second view: `s.cli("csv", pdf, out)`, `s.cli("summary", ...)`, or the exported file itself. Then `keep()` it.
- **Fake only the dialog widget.** `answer()` replaces the native dialog with the value a user would type. The code before and after the dialog is real. Proof that the dialog widget itself works needs `modal()` or a human (`docs/testing/MANUAL_TEST_PLAN.md`).
- **Report a failing journey as a failure.** When a precondition is missing or the app is wrong, keep the failing check and record the reason in the feature's Gotchas. Weakening the check to get a pass hides the bug.

Report a run as its evidence folder path plus the `PASS`/`FAIL` lines from `steps.md`.

## 4. Cleanup

```
.venv/bin/python .claude/skills/verify/scripts/orv.py cleanup
```

A finished run deletes its own `.verify/work/<run-id>/` scratch folder (sample PDFs, saved copies, the recovery folder) unless you passed `--keep-work`. `cleanup` handles the rest: it stops runs whose PID it recorded and that are still alive (it kills by recorded PID, never by process name), then deletes every work folder. It never touches `.verify/evidence/`. Run it after a timeout, a crash, a `--keep-work` debugging session, or whenever `doctor` lists leftover work folders.

## Gotchas

- `.verify/` lives at the repo root, so concurrent worktrees each have their own. Two runs in one checkout are safe because each has its own run id.
- `--visible` needs a display (`DISPLAY` or Wayland on Linux). Headless is the default and the only mode CI-like agents can use. Screenshots look the same in both.
- `This plugin does not support propagateSizeHints()` on stderr is noise from the offscreen platform.
- `s.samples()` runs `docs/testing/make_samples.py`, which needs `pyhanko` for `signed.pdf` (from `.[test]`).
- An exception inside a Qt handler fails the run at the next `settle()` with the traceback in `steps.md`. That is the app's error, not the harness's.
- Keyboard shortcuts (R, Ctrl+Z, Ctrl+S, Ctrl+W, PgDown) work offscreen when the key goes to a widget in the window: `key("Ctrl+S", s.w.cv)`. `key` activates that widget's window first, because a closed dialog leaves no active window and shortcuts then do nothing.
- Double-click with `click(..., double=True)`, `cell(..., double=True)` or `dclick`. `QTest.mouseDClick` leaves out the final release, so item views never emit `activated`.
- Visual quality (layout, icons, feel) is outside what checks can prove. Look at the screenshots, and leave section H of the manual test plan to a human.
