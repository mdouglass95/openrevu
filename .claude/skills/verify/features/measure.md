# Measure

The user sets a page scale by dragging across a dimension they know, or by ratio. They then measure lengths, areas, room fills, counts and other quantities. Each measurement shows its value on the page and in the Markups List, and totals show in the Measurements panel, the Measurement summary dialog, and the summary CSV. Covers manual test plan section D.

## Sub-features

- `calibrate`: Measure > Calibrate scale (drag a known length), the Calibrate tool, or the Calibrate button in the Measurements panel.
- `scale-ratio`: Measure > Set scale by ratio…, or the Ratio… button.
- `viewport`: Measure > Add viewport scale (drag a region): a region of the page with its own scale.
- `length`: the Length (M), Perimeter, and Polyline tools.
- `area`: the Area, Rectangle area, Ellipse area, and Volume tools.
- `fill`: Fill (room area) (F): click inside a closed room, and columns are subtracted.
- `count`: Count (K), which stays active, with a group name in the toolbar.
- `summary`: the Measurements panel, Measure > Measurement summary…, and the CSV export (from the File menu or the panel's Export summary (CSV)… button).
- `units`: Measure > Show feet and inches toggles decimal and feet-inches display.

## How to get to it (user POV)

- The **Measure** menu and the **Measure** tool group on the toolbar.
- The Measurements panel (right side, ruler icon, View > Measurements panel, Alt+7).

## Driving it with orv

Preconditions:

- `s.launch(s.samples() / "plan_v1.pdf")`. No markups. The default scale is 1 in = 0.08333 ft.

- **Calibrate.** `s.menu("Measure > Calibrate scale (drag a known length)")`, then `s.answer(("getDouble", 20), ("getItem", "ft"), ("getItem", "This page"))`, then `s.drag(0, (100, 150), (460, 150))`. The status starts with `Scale set: 20.0 ft`.
- **Length.** `s.tool("Length")`, then `s.click(0, 100, 180)`, `s.click(0, 460, 180)`, `s.click(0, 460, 180, double=True)`. The Markups List Value column reads `20.00 ft`.
- **Fill a room.** `s.tool("Fill")`, then `s.click(0, 150, 420)`. A `Fill Area` row reads 282 to 300 `ft²` (290-ish: room 1 minus its 2 ft column).
- **Panel totals.** `s.menu("View > Measurements panel")`. `s.table(s.w.meas_table)` lists `Fill Area` and `Length` with their totals. The Markups List header reads `Totals — Area: … ft²; Length: 20.00 ft`.
- **Summary dialog.** `s.modal(lambda: s.menu("Measure > Measurement summary…"), lambda dlg: s.shot("summary-dialog", dlg))`. The dialog titled `Measurement summary` has columns Subject, Measure, Items, Total, Unit.
- **Summary CSV.** `s.answer(("getSaveFileName", out))`, then `s.menu("File > Export measurement summary (CSV)…")`. The file has a `subject,measure,items,total,unit` header and one row per subject. `s.keep(out)`.
- **Proof.** `s.shot("measured")` with the panel open, the summary-dialog shot, and the kept CSV.

## Gotchas

- Calibration asks three questions in a fixed order: length (`getDouble`), unit (`getItem` from mm, cm, m, in, ft, yd), and scope (`getItem` "This page" / "All pages"). Queue all three before the drag.
- The Length tool needs a double-click on the last point to finish. Two single clicks leave the line open.
- A drag lands within about half a point of its target after screen rounding (the calibration reads `360.4 pt`). Assert measured values with a tolerance.
- The Markups List shows areas in `ft²`. The summary dialog and the summary CSV show the same area with unit `ft` (issue #66). Assert what each view shows, and expect the summary views to change when #66 is fixed.
- Expected numbers for the sample sheets are in `s.expected["room_area_sq_ft"]` and `s.expected["drawing_scale"]`.
