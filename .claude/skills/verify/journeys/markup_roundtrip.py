"""Draw a rectangle, edit its properties, delete and undo, save, and read the saved file back with openrevu-cli.

    .venv/bin/python .claude/skills/verify/scripts/orv.py run .claude/skills/verify/journeys/markup_roundtrip.py
"""
import csv


def journey(s):
    pdf = s.samples() / "plan_v1.pdf"
    s.launch(pdf)
    s.check(s.w.tabs.count() == 1 and s.w.tabs.tabText(0) == "plan_v1.pdf", f"one tab: {s.w.tabs.tabText(0)!r}")

    # draw
    s.tool("Rectangle")
    s.drag(0, (520, 260), (640, 340))
    rows = s.table(s.w.mk_table)
    s.check(len(rows) == 1 and rows[0][1] == "Rectangle", f"Markups List has one Rectangle row (subject Rectangle): {rows}")
    s.check(s.w.tabs.tabText(0).startswith("*"), f"tab shows unsaved changes: {s.w.tabs.tabText(0)!r}")
    s.shot("rectangle-drawn")

    # select it and edit the Properties panel
    s.tool("Select")
    s.click(0, 580, 300)
    s.check(s.w.cv.selected is not None, "clicking the rectangle selects it")
    s.type(s.w.p_subject, "Wall check")
    s.w.p_status.setFocus()
    s.key("Down", s.w.p_status)                        # (none) -> Accepted, as a user arrowing through the list
    s.type(s.w.p_reply, "Moved per RFI 12")
    rows = s.table(s.w.mk_table)
    s.check(rows[0][1] == "Wall check" and rows[0][4] == "Accepted", f"Markups List shows subject and status: {rows[0]}")
    s.check(s.w.p_replies.count() == 1, "the reply is listed under the markup")
    s.shot("properties-edited")

    # delete with the keyboard, undo from the menu
    s.w.cv.setFocus()
    s.key("Delete", s.w.cv)
    s.check(s.table(s.w.mk_table) == [], "Delete removes the markup")
    s.menu("Edit > Undo")
    rows = s.table(s.w.mk_table)
    s.check(len(rows) == 1 and rows[0][1] == "Wall check", f"Undo brings it back with its properties: {rows}")

    # save, then read the file from a separate process
    s.menu("File > Save")
    s.check(s.status() == "Saved", f"status bar says Saved: {s.status()!r}")
    s.check(not s.w.tabs.tabText(0).startswith("*"), f"tab no longer shows unsaved changes: {s.w.tabs.tabText(0)!r}")
    s.shot("saved")
    s.keep(pdf, "plan_v1-saved.pdf")
    out = s.work / "markups.csv"
    r = s.cli("csv", pdf, out)
    s.check(r.returncode == 0, "openrevu-cli csv exits 0")
    s.keep(out)
    with open(out, newline="", encoding="utf-8") as f:
        data = list(csv.DictReader(f))
    s.step(f"csv rows: {data}")
    s.check(len(data) == 1, "the saved file holds exactly one markup")
    row = {k.lower(): v for k, v in data[0].items()}
    s.check(row.get("subject") == "Wall check" and row.get("status") == "Accepted" and "Moved per RFI 12" in row.get("replies", ""),
            f"the saved markup keeps subject, status and reply: {row}")
