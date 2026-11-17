"""Print a document's pages onto a QPrinter."""
from __future__ import annotations

import math
from typing import Callable

import pymupdf as fitz
from PyQt5 import QtGui

# Pixels for one page are rendered in horizontal bands of at most this many bytes (RGB), so a plotter
# sheet at full printer resolution never needs the whole page in memory at once.
BAND_BYTES = 32 * 2 ** 20


def _bands(page: fitz.Rect, zoom: float) -> list[fitz.Rect]:
    """Split `page` into horizontal strips (in page points) whose pixmaps at `zoom` stay under BAND_BYTES."""
    width, height = math.ceil(page.width * zoom), math.ceil(page.height * zoom)
    rows = max(1, BAND_BYTES // (3 * width))
    return [fitz.Rect(0, top / zoom, page.width, min(top + rows, height) / zoom) for top in range(0, height, rows)]


def print_document(printer, doc, progress: Callable[[int, int], bool] | None = None) -> bool:
    """Draw every page of `doc` onto `printer`, one page per sheet, scaled to fit the printable area
    at the printer's resolution.

    `progress(done, total)` is called before the first band and after each one; it returns False to cancel.
    Returns False if cancelled (the print job is aborted), True otherwise."""
    painter = QtGui.QPainter(printer)
    vp = painter.viewport()
    plan = []
    for i in range(doc.page_count):
        page = doc.doc[i].rect
        zoom = min(vp.width() / page.width, vp.height() / page.height)
        plan.append((i, zoom, _bands(page, zoom)))
    total = sum(len(b) for _, _, b in plan)

    def bands_drawn():
        """Draw the bands one by one. Yield how many are drawn so far: 0 at first, then after each band."""
        done = 0
        yield done
        for i, zoom, bands in plan:
            if i:
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
