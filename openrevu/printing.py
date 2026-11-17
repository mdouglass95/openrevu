"""Print a document's pages onto a QPrinter."""
from __future__ import annotations

import math

import pymupdf as fitz
from PyQt5 import QtGui

# Pixels for one page are rendered in horizontal bands of at most this many bytes (RGB), so a plotter
# sheet at full printer resolution never needs the whole page in memory at once.
BAND_BYTES = 32 * 2 ** 20


def print_document(printer, doc) -> bool:
    """Draw every page of `doc` onto `printer`, one page per sheet, scaled to fit the printable area."""
    painter = QtGui.QPainter(printer)
    vp = painter.viewport()
    for i in range(doc.page_count):
        if i:
            printer.newPage()
        page = doc.doc[i].rect
        zoom = min(vp.width() / page.width, vp.height() / page.height)
        width, height = math.ceil(page.width * zoom), math.ceil(page.height * zoom)
        rows = max(1, BAND_BYTES // (3 * width))
        for top in range(0, height, rows):
            band = fitz.Rect(0, top / zoom, page.width, min(top + rows, height) / zoom)
            pix = doc.render(i, zoom, clip=band)
            img = QtGui.QImage(pix.samples, pix.width, pix.height, pix.stride, QtGui.QImage.Format_RGB888)
            painter.drawImage(vp.x() + pix.x, vp.y() + pix.y, img)
    painter.end()
    return True
