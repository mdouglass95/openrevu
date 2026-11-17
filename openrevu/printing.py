"""Print a document's pages onto a QPrinter."""
from __future__ import annotations

from PyQt5 import QtCore, QtGui


def print_document(printer, doc) -> bool:
    """Draw every page of `doc` onto `printer`, one page per sheet."""
    painter = QtGui.QPainter(printer)
    for i in range(doc.page_count):
        if i:
            printer.newPage()
        pix = doc.render(i, printer.resolution() / 72)
        img = QtGui.QImage(pix.samples, pix.width, pix.height, pix.stride, QtGui.QImage.Format_RGB888)
        rect = painter.viewport()
        sz = img.size().scaled(rect.size(), QtCore.Qt.KeepAspectRatio)
        painter.setViewport(rect.x(), rect.y(), sz.width(), sz.height())
        painter.setWindow(img.rect())
        painter.drawImage(0, 0, img)
        painter.setViewport(rect)
        painter.setWindow(rect)
    painter.end()
    return True
