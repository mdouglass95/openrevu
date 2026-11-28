# Sheets and revisions

The Sheets panel lists every page as a drawing sheet with its number, title, discipline and latest revision, detected from the page text or typed by the user. The user adds revisions to selected sheets, inserts a sheet index page, slips in a new version of sheets while keeping the markups, and exports the sheet list. Compare overlays two versions. Covers manual test plan section E.

## Sub-features

- `detect`: sheet numbers and titles are read from the page text when the panel opens, or with the Detect button.
- `edit`: double-click a Sheet, Title or Discipline cell to edit it. A duplicate sheet number is refused.
- `revision`: the Revision button adds a revision (name, description, date) to the selected sheets.
- `index`: the Index button inserts a "Sheet Index" page with links.
- `slip`: the Slip button replaces sheets with a newer PDF, pairing pages by sheet number or by page position, and keeps the markups.
- `export`: the Export sheet list (CSV)… button.
- `compare`: Document > Compare with another PDF… gives a result table, an overlay PDF and a CSV report.

## How to get to it (user POV)

- The **Sheets** tab, the fifth icon in the left panel. It has no View menu entry and no shortcut.
- **Document > Compare with another PDF…**, **Document > Auto-bookmark sheets**.

## Driving it with orv

Preconditions:

- `s.launch(s.samples() / "plan_v1.pdf")`. The left dock is visible (it is whenever a document is open).

- **Open the panel.** `s.panel("Sheets")`. The Sheet column of `s.table(s.w.sheets_table)` equals `s.expected["plan_v1"]` (`A-101`, `A-102`, `S-201`), and the titles are `Ground floor plan`, `First floor plan`, `Roof framing plan`.
- **Select two sheets.** `s.cell(s.w.sheets_table, 0, 0)`, then `s.cell(s.w.sheets_table, 1, 0, ctrl=True)`.
- **Add a revision.** `s.modal(lambda: s.button("Revision"), fill)`, where `fill(dlg)` types `A`, a description and `2026-01-15` into `dlg.findChildren(QLineEdit)` in order and then calls `s.button("OK", within=dlg)`. The Rev column reads `A`, `A`, blank. The titles must be unchanged. Today they are blanked (issue #65), so this check fails.
- **Export.** `s.answer(("getSaveFileName", out))`, then `s.button("Export sheet list (CSV)")`. The header is `page,number,title,discipline,revision,revision date,revision count`. `s.keep(out)`.
- **Compare (not yet scripted).** First save, since Compare reads the saved file. Then `s.answer(("getOpenFileName", samples / "plan_v2.pdf"), ("getItem", "By sheet number"), ("getSaveFileName", overlay))` and `s.menu("Document > Compare with another PDF…")`. The result dialog runs its own `exec_()`, so wrap the menu call in `s.modal`. Expected results are in `s.expected["compare_v1_to_v2_by_sheet"]`.
- **Proof.** `s.shot` of the revision form (`dlg`) and of the panel afterwards, plus the kept CSV.

## Gotchas

- The table fills only while the Sheets tab is the current left tab and the dock is visible. Click the tab with `s.panel("Sheets")` before reading the rows.
- The Revision button only shows a status message (`Select a sheet in the Sheets panel first.`) when no row is selected. Select first.
- The Add revision form is a custom dialog, not a static `QInputDialog`, so `s.answer` does not reach it. Use `s.modal`.
- `journeys/sheets_revision.py` fails at the title check until #65 is fixed. That failure is the journey working.
