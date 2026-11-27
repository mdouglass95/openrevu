"""Print a document's pages onto a QPrinter."""
from __future__ import annotations

import math
from typing import Callable

import pymupdf as fitz
from PyQt5 import QtGui, QtPrintSupport

# Pixels for one page are rendered in horizontal bands of at most this many bytes (RGB), so a plotter
# sheet at full printer resolution never needs the whole page in memory at once.
BAND_BYTES = 32 * 2 ** 20


def _bands(page: fitz.Rect, zoom: float) -> list[fitz.Rect]:
    """Split `page` into horizontal strips (in page points) whose pixmaps at `zoom` stay under BAND_BYTES."""
    width, height = math.ceil(page.width * zoom), math.ceil(page.height * zoom)
    rows = max(1, BAND_BYTES // (3 * width))
    return [fitz.Rect(0, top / zoom, page.width, min(top + rows, height) / zoom) for top in range(0, height, rows)]


def chosen_pages(printer, page_count: int, current: int) -> list[int]:
    """The 0-based page numbers the user chose in the print dialog: all pages, a range, or the current page."""
    P = QtPrintSupport.QPrinter
    rng = printer.printRange()
    if rng == P.CurrentPage:
        return [current]
    if rng == P.PageRange and printer.fromPage() > 0:
        first, last = max(1, printer.fromPage()), min(page_count, printer.toPage() or page_count)
        return list(range(first - 1, last))
    return list(range(page_count))


def print_document(printer, doc, progress: Callable[[int, int], bool] | None = None, pages=None) -> bool:
    """Draw the `pages` of `doc` (0-based page numbers; default: every page) onto `printer`, one page per sheet,
    scaled to fit the printable area at the printer's resolution.

    `progress(done, total)` is called before the first band and after each one; it returns False to cancel.
    Returns False if cancelled (the print job is aborted), True otherwise."""
    painter = QtGui.QPainter(printer)
    vp = painter.viewport()
    plan = []
    for i in range(doc.page_count) if pages is None else pages:
        page = doc.doc[i].rect
        zoom = min(vp.width() / page.width, vp.height() / page.height)
        plan.append((i, zoom, _bands(page, zoom)))
    total = sum(len(b) for _, _, b in plan)

    def bands_drawn():
        """Draw the bands one by one. Yield how many are drawn so far: 0 at first, then after each band."""
        done = 0
        yield done
        for k, (i, zoom, bands) in enumerate(plan):
            if k:
                printer.newPage()
            for band in bands:
                pix = doc.render(i, zoom, clip=band)
                # samples_mv shares the pixmap's memory, so the band is not copied
                img = QtGui.QImage(pix.samples_mv, pix.width, pix.height, pix.stride, QtGui.QImage.Format_RGB888)
                painter.drawImage(vp.x() + pix.x, vp.y() + pix.y, img)
                done += 1
                yield done

    try:
        for done in bands_drawn():
            if progress and not progress(done, total):
                printer.abort()
                return False
        return True
    finally:
        painter.end()
