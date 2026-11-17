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


def record_renders(doc, monkeypatch):
    """Spy on doc.render: returns the list of (width, height, bytes) of every pixmap made while printing."""
    made = []
    real = doc.render

    def render(*a, **k):
        pix = real(*a, **k)
        made.append((pix.width, pix.height, pix.stride * pix.height))
        return pix
    monkeypatch.setattr(doc, "render", render)
    return made


def test_renders_at_the_paper_resolution_not_the_page_size_times_dpi(app, tmp_path, monkeypatch):
    # An ARCH E sheet on letter paper at 300 dpi needs at most 2550 x 3300 device pixels.
    # Rendering the 36 in wide page at 300 dpi would make it 10800 pixels wide.
    doc = make_doc(tmp_path, n=1, size=ARCH_E)
    made = record_renders(doc, monkeypatch)
    out = tmp_path / "out.pdf"
    print_document(pdf_printer(out, dpi=300), doc)
    assert made
    assert max(w for w, h, b in made) <= 2550 + 2
    assert sum(h for w, h, b in made) >= 3300 * 0.95   # still fills the paper (a 3:4 sheet on 8.5 x 11 in is height-bound)
    assert is_red(colour_at(fitz.open(out)[0], 0.2, 0.2))


def test_large_paper_is_rendered_in_bands_of_bounded_size(app, tmp_path, monkeypatch):
    # ARCH E paper at 300 dpi is 10800 x 14400 device pixels: 466 MB as one RGB pixmap.
    doc = make_doc(tmp_path, n=1, size=ARCH_E)
    made = record_renders(doc, monkeypatch)
    out = tmp_path / "out.pdf"
    print_document(pdf_printer(out, dpi=300, paper_in=(36, 48)), doc)
    assert max(b for w, h, b in made) <= 64 * 2 ** 20
    assert sum(h for w, h, b in made) >= 14400
    # The bands join without gaps: the whole red quarter is red, the whole blue quarter is blue.
    pix = fitz.open(out)[0].get_pixmap(matrix=fitz.Matrix(0.25, 0.25))
    for y in range(int(pix.height * 0.02), int(pix.height * 0.48)):
        assert is_red(pix.pixel(pix.width // 4, y)[:3]), y
    for y in range(int(pix.height * 0.52), int(pix.height * 0.98)):
        assert is_blue(pix.pixel(pix.width * 3 // 4, y)[:3]), y


def test_reports_progress_and_stops_when_cancelled(app, tmp_path, monkeypatch):
    doc = make_doc(tmp_path, n=3)
    made = record_renders(doc, monkeypatch)
    seen = []
    assert print_document(pdf_printer(tmp_path / "all.pdf"), doc, progress=lambda d, t: seen.append((d, t)) or True)
    total = seen[-1][1]
    assert seen[-1] == (total, total) and total >= 3
    assert [d for d, t in seen] == sorted(d for d, t in seen)

    made.clear()
    assert print_document(pdf_printer(tmp_path / "cancelled.pdf"), doc, progress=lambda d, t: d == 0) is False
    assert len(made) == 1   # nothing rendered after the cancel


# Runs in a fresh process, so the peak resident memory it reports belongs to this print job alone.
PRINT_ARCH_E_AT_1200_DPI = r'''
import json, sys
import pymupdf as fitz
from PyQt5 import QtWidgets
sys.path.insert(0, sys.argv[1])
from test_printing import make_doc, pdf_printer
from openrevu.printing import print_document
from pathlib import Path


def peak_rss_mb():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                               "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                               "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        c = Counters(); c.cb = ctypes.sizeof(c)
        psapi, kernel32 = ctypes.WinDLL("psapi"), ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return None
        return c.PeakWorkingSetSize / 2 ** 20
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 2 ** 20 if sys.platform == "darwin" else peak / 2 ** 10   # bytes on macOS, KiB on Linux


app = QtWidgets.QApplication([])
tmp = Path(sys.argv[2])
doc = make_doc(tmp, n=1, size=(36 * 72, 48 * 72))
before = peak_rss_mb()
print_document(pdf_printer(tmp / "out.pdf", dpi=1200, paper_in=(36, 48)), doc)
print(json.dumps({"before": before, "peak": peak_rss_mb(), "pages": fitz.open(tmp / "out.pdf").page_count}))
'''


def test_printing_arch_e_at_1200_dpi_keeps_peak_memory_low(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    r = subprocess.run([sys.executable, "-c", PRINT_ARCH_E_AT_1200_DPI, str(Path(__file__).parent), str(tmp_path)],
                       capture_output=True, text=True, env=env, timeout=600)
    assert r.returncode == 0, r.stderr
    res = json.loads(r.stdout.strip().splitlines()[-1])
    if res["peak"] is None:
        pytest.skip("no way to read peak memory on this platform")
    print(f"peak RSS {res['peak']:.0f} MB (before printing {res['before']:.0f} MB)")
    assert res["pages"] == 1
    assert res["peak"] < 500
