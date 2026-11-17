"""Print a document's pages onto a QPrinter."""
from __future__ import annotations

from PyQt5 import QtGui


def print_document(printer, doc) -> bool:
    """Draw every page of `doc` onto `printer`, one page per sheet, scaled to fit the printable area."""
    painter = QtGui.QPainter(printer)
    vp = painter.viewport()
    painter.setWindow(vp)   # draw in device pixels
    for i in range(doc.page_count):
        if i:
            printer.newPage()
        page = doc.doc[i].rect
        zoom = min(vp.width() / page.width, vp.height() / page.height)
        pix = doc.render(i, zoom)
        img = QtGui.QImage(pix.samples, pix.width, pix.height, pix.stride, QtGui.QImage.Format_RGB888)
        painter.drawImage(vp.x() + pix.x, vp.y() + pix.y, img)
    painter.end()
    return True
