import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pymupdf as fitz
import pytest

pytest.importorskip("PyQt5")
from PyQt5 import QtCore, QtGui, QtPrintSupport, QtWidgets

from openrevu.core import Document
from openrevu.printing import print_document

ARCH_E = (36 * 72, 48 * 72)   # 36 x 48 in, in points


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pdf_printer(path, dpi=150, paper_in=(8.5, 11)):
    """A QPrinter that writes a PDF, with no margins, so a test can open what was 'printed'."""
    pr = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.HighResolution)
    pr.setOutputFormat(QtPrintSupport.QPrinter.PdfFormat)
    pr.setOutputFileName(str(path))
    pr.setResolution(dpi)
    pr.setPageSize(QtGui.QPageSize(QtCore.QSizeF(*paper_in), QtGui.QPageSize.Inch))
    pr.setFullPage(True)
    return pr


def make_doc(tmp_path, n=2, size=(612, 792)):
    """n pages; page i has a red box filling its top-left quarter, and a blue box on the bottom-right quarter of the last page."""
    d = fitz.open()
    w, h = size
    for i in range(n):
        p = d.new_page(width=w, height=h)
        p.draw_rect(fitz.Rect(0, 0, w / 2, h / 2), color=(1, 0, 0), fill=(1, 0, 0))
    d[-1].draw_rect(fitz.Rect(w / 2, h / 2, w, h), color=(0, 0, 1), fill=(0, 0, 1))
    path = tmp_path / "in.pdf"
    d.save(path)
    return Document(str(path))


def colour_at(page, fx, fy):
    """RGB of the printed page at a fraction (fx, fy) of its width and height."""
    pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
    return pix.pixel(int(fx * (pix.width - 1)), int(fy * (pix.height - 1)))[:3]


def is_red(c):
    return c[0] > 200 and c[1] < 60 and c[2] < 60


def is_blue(c):
    return c[2] > 200 and c[0] < 60 and c[1] < 60


def test_prints_every_page_with_content_in_place(app, tmp_path):
    doc = make_doc(tmp_path, n=2)
    out = tmp_path / "out.pdf"
    assert print_document(pdf_printer(out), doc) is True
    printed = fitz.open(out)
    assert printed.page_count == 2
    for p in printed:
        assert is_red(colour_at(p, 0.2, 0.2))
        assert not is_red(colour_at(p, 0.8, 0.2))
    assert is_blue(colour_at(printed[1], 0.8, 0.8))
    assert not is_blue(colour_at(printed[0], 0.8, 0.8))
