# OpenRevu verification map

This folder is the maintained list of what a user can do in OpenRevu and how to prove each thing with the `verify` harness (`.claude/skills/verify/scripts/orv.py`). Read this index, then use the matching feature file as the recipe. `docs/testing/MANUAL_TEST_PLAN.md` is the human version of the same ground; feature files cite its test numbers (T01, T48, ...).

## Baseline preconditions

- `orv.py doctor` prints `doctor: ready`.
- Each journey starts from `s.samples()`: a fresh copy of the manual-test-plan files in the run's own work folder, with their right answers in `s.expected` (from `expected.json`). The main file is `plan_v1.pdf`: sheets A-101, A-102 and S-201, drawn at 18 pt = 1 ft, with a 20 ft dimension line from (100,150) to (460,150) and room 1 inside (100,200)-(460,470).
- Preferences live in memory and the recovery folder is private to the run. The Recent files list therefore starts empty, and a "crash" only exists if the journey makes one (see [save-and-recovery](save-and-recovery.md)).

## Driving conventions

- Reach every feature through a menu (`s.menu`), a tool (`s.tool`), a panel tab (`s.panel`), a button (`s.button`), a key (`s.key`), or the mouse on the page (`s.click`, `s.drag`, in PDF points). Use the exact menu and button texts written here, including the `…`.
- Queue dialog answers with `s.answer(...)` before the action that opens the dialog. For a dialog with its own `exec_()`, use `s.modal(trigger, handle)`.
- Prove a saved or exported file from a second view: `s.cli(...)` or the exported file itself, then `s.keep()` it.

## Proof and skip reporting

- A proof is the run's evidence folder: `steps.md` with every `PASS` line, plus the screenshots after each meaningful step.
- Name the entry point you drove (menu, key, toolbar, panel button). Proving one entry point does not prove the others listed for that feature.
- When a path cannot be driven (it needs a display, a missing tool such as `verapdf`, or a native dialog), report it as unverified with the reason. Proving it through a different path and calling it verified is wrong.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph on what the user sees. It then has four H2 sections in this order:

1. `Sub-features`: short IDs, one line each.
2. `How to get to it (user POV)`: every entry point a user has.
3. `Driving it with orv`: `Preconditions:`, then labelled bullets pairing each user action with the exact harness call and the observable result.
4. `Gotchas`: traps that waste a run or make it prove the wrong thing.

## Features

- [Open and navigate](open-and-navigate.md): start page, Open PDF, passwords, pages, zoom, tabs, Recent files. Journey `open_navigate.py`.
- [Markup](markup.md): draw, select, edit properties, replies, delete, undo, save. Journey `markup_roundtrip.py`.
- [Measure](measure.md): calibrate, Length, Fill room area, Measurement summary, CSV export. Journey `measure_calibrate.py`.
- [Sheets and revisions](sheets.md): Sheets panel, revisions, sheet list CSV. Journey `sheets_revision.py`.
- [Save and recovery](save-and-recovery.md): save, unsaved-changes prompt, autosave, crash recovery. Journey `crash_recovery.py`.
- [Batch CLI](cli.md): `openrevu-cli` commands on saved files.
