"""Start page, Open PDF with a password-protected file, page and zoom navigation, close, reopen from Recent files
(manual test plan T01-T04).

    .venv/bin/python .claude/skills/verify/scripts/orv.py run .claude/skills/verify/journeys/open_navigate.py
"""


def journey(s):
    samples = s.samples()
    s.launch()
    s.check(s.w.stack.currentIndex() == 0 and not s.w.left_dock.isVisible(), "start page shows, side panels hidden")
    s.shot("start-page")

    s.answer(("getOpenFileNames", [samples / "plan_v1.pdf", samples / "protected.pdf"]),
             ("getText", "wrong"), ("getText", s.expected["protected_password"]))
    s.button("Open PDF")
    tabs = [s.w.tabs.tabText(i) for i in range(s.w.tabs.count())]
    s.check(tabs == ["plan_v1.pdf", "protected.pdf"], f"two tabs after a wrong then right password: {tabs}")

    s.w.tabs.setCurrentIndex(0)
    s.settle()
    s.key("PgDown", s.w.cv)
    s.check(s.w.cv.current_page() == 1, f"PgDown goes to page 2: page index {s.w.cv.current_page()}")
    z = s.w.cv.zoom
    s.menu("View > Zoom in")
    s.check(s.w.cv.zoom > z, f"Zoom in raises the zoom: {z:.3f} -> {s.w.cv.zoom:.3f}")
    s.shot("page-2-zoomed")

    s.key("Ctrl+W", s.w.cv)
    s.key("Ctrl+W", s.w.cv)
    s.check(s.w.tabs.count() == 0 and s.w.stack.currentIndex() == 0, "both tabs closed, start page back")
    recent = [s.w.start.recent.item(i).text() for i in range(s.w.start.recent.count())]
    s.check(any("plan_v1.pdf" in r for r in recent), f"Recent files lists plan_v1.pdf: {recent}")
    row = next(i for i, r in enumerate(recent) if "plan_v1.pdf" in r)
    item = s.w.start.recent.item(row)
    s.step(f"dbl-click recent file {item.text()!r}")
    s.dclick(s.w.start.recent.viewport(), s.w.start.recent.visualItemRect(item).center())
    s.settle()
    s.check([s.w.tabs.tabText(i) for i in range(s.w.tabs.count())] == ["plan_v1.pdf"], "double-click reopens it")
