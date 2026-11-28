# Save and recovery

The user saves in place or to a new name. They are asked before closing a document with unsaved changes. After a crash they can recover work from automatic snapshots. Signed and editing-restricted PDFs get extra warnings before an overwrite. Covers manual test plan T05 and section G.

## Sub-features

- `save`: File > Save (Ctrl+S) writes in place, shows `Saved`, and clears the `*` from the tab.
- `save-as`: File > Save As… (Ctrl+Shift+S) writes a new file, and the tab takes its name.
- `unsaved-close`: closing a modified tab asks **Save / Discard / Cancel**.
- `autosave`: unsaved documents are written to the recovery folder every `OPENREVU_AUTOSAVE_SECONDS` (default 60).
- `recover`: on the next start, the "Recover unsaved work" box offers **Recover / Discard / Ask me later**. A recovered tab is named `<file> (recovered)`, and saving it asks for a file name.
- `signed-save`: saving a signed PDF warns with **Save As…**, **Save anyway**, and **Cancel** (T47b).
- `restricted-save`: saving a PDF that restricts editing saves a copy instead (T51).

## How to get to it (user POV)

- The **File** menu, Ctrl+S, Ctrl+Shift+S, and the save icon on the toolbar.
- The tab ×, Ctrl+W, or File > Close tab on a modified document.
- Starting OpenRevu after it closed without saving.

## Driving it with orv

Preconditions:

- `s.samples()` has run. For recovery, set `os.environ["OPENREVU_AUTOSAVE_SECONDS"] = "1"` before the first `s.launch(...)`. The window reads it at start.

- **Save.** Make a markup, then `s.menu("File > Save")` or `s.key("Ctrl+S", s.w.cv)`. The status is `Saved` and the tab text has no `*`. Read the file back with `s.cli("csv", pdf, out)`.
- **Unsaved close.** Make a markup, then `s.answer(("question", "Cancel"))` and `s.key("Ctrl+W", s.w.cv)`. The tab stays. Repeat with `"Discard"` (the tab closes, and the file on disk is unchanged) and with `"Save"` (the file gets the markup).
- **Autosave.** After a markup, `s.settle(2500)`. `s.work / "recovery"` holds a `<id>.pdf` and a `<id>.json` snapshot.
- **Crash.** `s.w.autosave_timer.stop()`, `s.w.hide()`, and keep a reference to the old window. Do not close it: closing deletes the snapshot, as a clean exit does.
- **Recover.** `s.modal(lambda: s.launch(), lambda box: s.button("Recover", within=box))`. The box text starts with `OpenRevu closed unexpectedly last time.` The only tab is `*plan_v1.pdf (recovered)` and it has the markups.
- **Save a recovered document.** `s.answer(("getSaveFileName", out))`, then `s.key("Ctrl+S", s.w.cv)`. It asks for a name (pre-filled with the original path), writes `out`, and leaves the original unchanged.
- **Proof.** `s.shot("recovery-offer", box)`, `s.shot("recovered")`, `s.keep(out)`, and `steps.md` showing the save dialog.

## Gotchas

- After a dialog closes, no window is active and window shortcuts are dead. `s.key` activates the target's window first, the way a user's click would. Code that sends keys some other way must do the same.
- The recovery offer is a `QMessageBox` instance run with `exec_()`, not a static call, so `s.answer` does not reach it. Use `s.modal`.
- The signed-PDF save warning is a custom box too. Script it with `s.modal(..., lambda box: s.button("Save anyway", within=box))` (or `Save As…`, `Cancel`) on `signed.pdf`. A restricted PDF needs no box: Save goes straight to `getSaveFileName` titled `Save a copy (this PDF restricts editing)`, so `s.answer(("getSaveFileName", copy))` covers it.
- A real `kill -9` of the app is outside what orv does: the harness and the app share one process. The abandoned-window crash covers the snapshot and recovery code. T48 in the manual plan covers the real kill.
