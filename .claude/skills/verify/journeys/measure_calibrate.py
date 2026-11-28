"""Calibrate on the 20 ft dimension line, measure a length and a room with Fill, open the Measurement summary,
and export the summary CSV.

    .venv/bin/python .claude/skills/verify/scripts/orv.py run .claude/skills/verify/journeys/measure_calibrate.py

Sample sheet facts (docs/testing/make_samples.py): 18 pt = 1 ft; the dimension line runs (100,150)-(460,150);
room 1 is inside (100,200)-(460,470) with a 2 ft column; its net area is about 291 sq ft (accept 282 to 300).
"""
import csv
import re


def journey(s):
    pdf = s.samples() / "plan_v1.pdf"
    s.launch(pdf)

    # calibrate: drag the known dimension, answer the three prompts
    s.menu("Measure > Calibrate scale (drag a known length)")
    s.answer(("getDouble", 20), ("getItem", "ft"), ("getItem", "This page"))
    s.drag(0, (100, 150), (460, 150))
    s.check(s.status().startswith("Scale set: 20.0 ft"), f"status confirms the scale: {s.status()!r}")

    # length along the same line
    s.tool("Length")
    s.click(0, 100, 180)
    s.click(0, 460, 180)
    s.click(0, 460, 180, double=True)
    rows = s.table(s.w.mk_table)
    s.check(len(rows) == 1, f"one measurement in the Markups List: {rows}")
    s.check("20" in rows[0][6] and "ft" in rows[0][6], f"length reads 20 ft: {rows[0][6]!r}")

    # room area with Fill (the column is subtracted)
    s.tool("Fill")
    s.click(0, 150, 420)
    rows = s.table(s.w.mk_table)
    s.check(len(rows) == 2, f"Fill adds a second measurement: {rows}")
    num = re.search(r"[\d,.]+", rows[1][6])
    area = float(num.group().replace(",", "")) if num else -1
    s.check(282 <= area <= 300 and "ft²" in rows[1][6], f"room 1 net area is 282-300 ft²: {rows[1][6]!r}")
    s.menu("View > Measurements panel")
    s.shot("measured")

    # Measurement summary dialog
    s.modal(lambda: s.menu("Measure > Measurement summary…"), lambda dlg: s.shot("summary-dialog", dlg))

    # export the summary CSV
    out = s.work / "summary.csv"
    s.answer(("getSaveFileName", out))
    s.menu("File > Export measurement summary (CSV)…")
    s.check(out.exists(), f"summary CSV written to {out.name}")
    s.keep(out)
    text = out.read_text(encoding="utf-8")
    s.step("summary.csv:\n" + text)
    with open(out, newline="", encoding="utf-8") as f:
        data = list(csv.reader(f))
    s.check(any("ft" in " ".join(r) for r in data[1:]), "the CSV lists the measurements in feet")
