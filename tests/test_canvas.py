"""Canvas rendering and zoom: memory at high zoom, zoom anchoring and debounced wheel zoom."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import fitz
import pytest

pytest.importorskip("PyQt5")
from PyQt5 import QtCore, QtGui, QtTest, QtWidgets

from openrevu.canvas import Canvas
from openrevu.core import Document

E_SIZE = (2592, 1728)   # a 36 x 24 in sheet, in points


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def make_canvas(tmp_path, pages=(E_SIZE,), draw=None):
    d = fitz.open()
    for w, h in pages:
        p = d.new_page(width=w, height=h)
        if draw:
            draw(p)
    path = tmp_path / "sheet.pdf"; d.save(path)
    cv = Canvas(Document(str(path)))
    cv.resize(1300, 900); cv.show()
    QtWidgets.QApplication.processEvents()
    return cv


def pixmap_bytes(cv):
    total = 0
    for it in cv.scene().items():
        if isinstance(it, QtWidgets.QGraphicsPixmapItem):
            pm = it.pixmap()
            total += pm.width() * pm.height() * pm.depth() // 8
    return total


def test_zooming_to_800_percent_on_a_large_sheet_keeps_pixmaps_small(app, tmp_path, monkeypatch):
    monkeypatch.setattr(Canvas, "devicePixelRatioF", lambda self: 2.0)
    cv = make_canvas(tmp_path)
    cv.set_zoom(8.0)
    assert cv.zoom == 8.0
    assert pixmap_bytes(cv) < 200 * 1024 ** 2


def ink_at(cv, pno, pt):
    """Darkness (0 = white, 255 = black) of the screen pixel showing PDF point pt on page pno."""
    QtWidgets.QApplication.processEvents()
    pos = cv.mapFromScene(cv._to_scene(pno, pt))
    assert cv.viewport().rect().contains(pos), pos
    return 255 - cv.viewport().grab().toImage().pixelColor(pos).lightness()


def grid(page):
    """1 pt black lines: horizontal at y = 300, 1400; vertical at x = 200, 2400."""
    w, h = page.rect.width, page.rect.height
    for y in (300, 1400):
        page.draw_line((0, y), (w, y), width=1)
    for x in (200, 2400):
        page.draw_line((x, 0), (x, h), width=1)


@pytest.mark.parametrize("rotation", [0, 90])
def test_at_high_zoom_what_is_on_screen_is_sharp_also_after_scrolling(app, tmp_path, rotation):
    def draw(p):
        grid(p); p.set_rotation(rotation)
    cv = make_canvas(tmp_path, draw=draw)
    cv.set_zoom(8.0)
    # PDF points on the lines, in the page's displayed (rotated) coordinates
    pts = [fitz.Point(200, 300), fitz.Point(2400, 300), fitz.Point(2400, 1400)]   # then across, then down
    page = cv.doc.doc[0]
    for unrot in pts:
        pt = unrot * page.rotation_matrix
        cv.centerOn(cv._to_scene(0, (pt.x, pt.y)))
        assert ink_at(cv, 0, (pt.x, pt.y)) > 200        # a crisp black 8 px line, not a blurred grey one


def pdf_under(cv, pos):
    """(page, PDF point) at viewport position pos"""
    sp = cv.mapToScene(pos)
    pno = cv.page_at(sp)
    return pno, cv._to_pdf(pno, sp)


def moved(cv, pos, where):
    """How far, in screen pixels, the PDF point `where` (from pdf_under) now is from viewport position pos."""
    at = cv.viewportTransform().map(cv._to_scene(*where))
    return max(abs(at.x() - pos.x()), abs(at.y() - pos.y()))


@pytest.mark.parametrize("start, end", [(1.0, 3.0), (3.0, 8.0), (4.0, 1.5)])
def test_zoom_keeps_the_point_under_the_anchor_in_place(app, tmp_path, start, end):
    cv = make_canvas(tmp_path)
    cv.set_zoom(start)
    cv.centerOn(cv._to_scene(0, (1300, 860)))
    anchor = QtCore.QPoint(400, 300)
    pt = pdf_under(cv, anchor)
    cv.set_zoom(end, anchor=anchor)
    assert moved(cv, anchor, pt) < 2


def test_zoom_without_an_anchor_keeps_the_viewport_centre_in_place(app, tmp_path):
    cv = make_canvas(tmp_path)
    cv.set_zoom(2.0)
    cv.centerOn(cv._to_scene(0, (1800, 1000)))
    centre = cv.viewport().rect().center()
    pt = pdf_under(cv, centre)
    cv.set_zoom(5.0)
    assert moved(cv, centre, pt) < 2


def ctrl_wheel(cv, pos):
    ev = QtGui.QWheelEvent(QtCore.QPointF(pos), QtCore.QPointF(cv.viewport().mapToGlobal(pos)), QtCore.QPoint(0, 0),
                           QtCore.QPoint(0, 120), QtCore.Qt.NoButton, QtCore.Qt.ControlModifier,
                           QtCore.Qt.NoScrollPhase, False)
    QtWidgets.QApplication.sendEvent(cv.viewport(), ev)


def test_rapid_ctrl_wheel_zooms_at_the_cursor_with_a_single_full_render(app, tmp_path, monkeypatch):
    cv = make_canvas(tmp_path, pages=[E_SIZE] * 3)   # wider than the viewport, so it can scroll sideways
    cv.goto_page(1)
    layouts, renders = [], []
    real_relayout, real_render = Canvas.relayout, cv.doc.render
    monkeypatch.setattr(Canvas, "relayout", lambda self: layouts.append(1) or real_relayout(self))
    monkeypatch.setattr(cv.doc, "render", lambda *a, **k: renders.append(a[0]) or real_render(*a, **k))
    anchor = QtCore.QPoint(500, 300)
    pt = pdf_under(cv, anchor)
    for _ in range(10):
        ctrl_wheel(cv, anchor)
        QtWidgets.QApplication.processEvents()
    assert layouts == [] and renders == []                # the existing picture is only scaled meanwhile
    assert moved(cv, anchor, pt) < 2
    QtTest.QTest.qWait(400)
    assert len(layouts) == 1
    shown = [it for it in cv.scene().items() if isinstance(it, QtWidgets.QGraphicsPixmapItem)]
    assert len(renders) == len(shown)                     # every render is on screen: none was thrown away
    assert cv.zoom == pytest.approx(1.25 * 1.15 ** 10)
    assert cv.transform().isIdentity()
    assert moved(cv, anchor, pt) < 2


def test_fit_page_shows_the_whole_current_page(app, tmp_path):
    cv = make_canvas(tmp_path, pages=[(612, 792)] * 3)
    cv.set_zoom(3.0)
    cv.goto_page(1, y_offset=300)
    cv.fit_page()
    assert cv.current_page() == 1
    shown = cv.mapFromScene(cv._rects[1]).boundingRect()
    assert cv.viewport().rect().contains(shown)
