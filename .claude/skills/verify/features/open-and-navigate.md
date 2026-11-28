# Open and navigate

With no file, OpenRevu shows a start page with Open PDF, New from images, and Recent files. Opening PDFs gives one tab per file, fitted to the page width. The user moves between pages, zooms, switches and closes tabs, and reopens files from Recent files. Covers manual test plan T01 to T04.

## Sub-features

- `start-page`: the start page shows with the side panels hidden and the Properties toolbar greyed out.
- `open-dialog`: Open PDF (button, File > Open…, Ctrl+O) opens one or more files, each in its own tab.
- `open-password`: a protected file asks for its password and asks again after a wrong one.
- `open-dedupe`: opening a file that is already open switches to its tab.
- `page-nav`: PgDown / PgUp, View > Next page / Previous page, and the page box in the toolbar.
- `zoom`: View > Zoom in / Zoom out / Fit width / Fit page (Ctrl+= / Ctrl+- / Ctrl+0 / Ctrl+9).
- `close-tab`: the tab ×, File > Close tab, Ctrl+W. The start page comes back after the last tab closes.
- `recent`: closed files are listed under Recent files, and double-clicking one opens it.

## How to get to it (user POV)

- Start `openrevu` with no file. Or start `openrevu a.pdf b.pdf` with files.
- Click **Open PDF…** on the start page, choose **File > Open…**, or press Ctrl+O.
- Drag a PDF onto the window (not drivable with orv).
- Double-click a file under **Recent files**.

## Driving it with orv

Preconditions:

- `s.samples()` has run. `s.expected["protected_password"]` is `test123`.

- **Start page.** `s.launch()` with no file. `s.w.stack.currentIndex() == 0` and `s.w.left_dock.isVisible()` is false. Status is `Open a PDF to begin (Ctrl+O)`.
- **Open two files, one protected.** `s.answer(("getOpenFileNames", [samples / "plan_v1.pdf", samples / "protected.pdf"]), ("getText", "wrong"), ("getText", "test123"))`, then `s.button("Open PDF")`. `steps.md` shows the `Password | Password for protected.pdf:` dialog twice. The tabs read `plan_v1.pdf`, `protected.pdf`.
- **Launch with files.** `s.launch(pdf)` behaves like `openrevu pdf`. The status reads `plan_v1.pdf: 3 page(s). Pick a tool from the toolbar.`
- **Next page.** `s.key("PgDown", s.w.cv)`. `s.w.cv.current_page() == 1`.
- **Zoom.** `s.menu("View > Zoom in")`. `s.w.cv.zoom` goes up by 1.25×.
- **Close tabs.** `s.key("Ctrl+W", s.w.cv)` once per tab. `s.w.tabs.count() == 0` and the start page is back.
- **Recent files.** The rows of `s.w.start.recent` read `plan_v1.pdf    —    <folder>`. `s.dclick(s.w.start.recent.viewport(), s.w.start.recent.visualItemRect(item).center())` reopens the file.
- **Proof.** `s.shot("start-page")` before opening, and `s.shot(...)` after navigating.

## Gotchas

- Recent files starts empty in every run because preferences are in memory. Open and close a file first.
- `QTest.mouseDClick` does not activate list items. Use `s.dclick`, which sends the full press, release, double-click, release sequence.
- Cancelling the password prompt (`("getText", CANCEL)`) leaves no tab and shows no error.
- The first fit-width happens on a zero-delay timer after the tab opens. Read `s.w.cv.zoom` after `s.launch()` or `s.settle()`, never in the same breath as the open.
