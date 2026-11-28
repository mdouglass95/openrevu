"""Open the Sheets panel, check the detected sheets, add revision A to two sheets, and export the sheet list.

    .venv/bin/python .claude/skills/verify/scripts/orv.py run .claude/skills/verify/journeys/sheets_revision.py
"""
from PyQt5 import QtWidgets as W


def journey(s):
    pdf = s.samples() / "plan_v1.pdf"
    s.launch(pdf)
    s.panel("Sheets")
    rows = s.table(s.w.sheets_table)
    s.check([r[1] for r in rows] == s.expected["plan_v1"], f"Sheets lists {s.expected['plan_v1']}: {rows}")

    s.cell(s.w.sheets_table, 0, 0)
    s.cell(s.w.sheets_table, 1, 0, ctrl=True)

    def fill(dlg):
        edits = dlg.findChildren(W.QLineEdit)
        for e, text in zip(edits, ("A", "Issued for review", "2026-01-15")):
            s.type(e, text, enter=False)
        s.shot("revision-form", dlg)
        s.button("OK", within=dlg)

    s.modal(lambda: s.button("Revision"), fill)
    rows = s.table(s.w.sheets_table)
    s.check([r[4] for r in rows] == ["A", "A", ""], f"Rev column is A, A, blank: {rows}")
    s.check([r[2] for r in rows] == ["Ground floor plan", "First floor plan", "Roof framing plan"],
            f"titles survive the revision: {[r[2] for r in rows]}")
    s.shot("revisions")

    out = s.work / "sheets.csv"
    s.answer(("getSaveFileName", out))
    s.button("Export sheet list (CSV)")
    s.check(out.exists(), "sheet list CSV written")
    s.keep(out)
    s.step("sheets.csv:\n" + out.read_text(encoding="utf-8"))
