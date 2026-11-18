"""Reading scales and measurements made in Bluebeam Revu (issue #12).

RevuTest.pdf has one page (Rotate 90, offset CropBox) calibrated in Revu at 1/8" = 1'-0", with two viewports at
1/4" = 1'-0". The expected values come from RevuTest.csv, the Markups List that Revu exported for the same file.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

import pytest

from openrevu.core import Document, Scale

SAMPLE = Path(__file__).parent / "data" / "revu" / "RevuTest.pdf"
EIGHTH_INCH = 1 / 9     # ft per point at 1/8" = 1'-0": 1 pt = 1/72 in on paper = (1/72) / (1/8) ft
QUARTER_INCH = 1 / 18   # ft per point at 1/4" = 1'-0"


@pytest.fixture
def revu():
    return Document(str(SAMPLE))


def test_revu_page_scale_is_read(revu):
    sc = revu.scale_for(0)
    assert sc.unit == "ft"
    assert sc.unit_per_pt == pytest.approx(EIGHTH_INCH, rel=1e-3)


def test_revu_viewports_are_read(revu):
    assert len(revu.viewports) == 2
    for pno, rect, sc in revu.viewports:
        assert pno == 0
        assert sc.unit == "ft"
        assert sc.unit_per_pt == pytest.approx(QUARTER_INCH, rel=1e-3)
        assert revu._page(0).rect.contains(rect)
    # the Revu 1'-8" length (xref 127) lies inside one of the viewports, so a new measurement there uses 1/4"
    line = next(m for m in revu.markups() if m.xref == 127)
    assert revu.scale_at(0, line.points()).unit_per_pt == pytest.approx(QUARTER_INCH, rel=1e-3)
    # the Revu 60'-0" length (xref 52) lies outside both, so it uses the page scale
    line = next(m for m in revu.markups() if m.xref == 52)
    assert revu.scale_at(0, line.points()).unit_per_pt == pytest.approx(EIGHTH_INCH, rel=1e-3)


# (xref, kind, value in ft / ft² / ea) from RevuTest.csv: 60'-0", 30'-0", 4'-0", 1'-8", 108'-3 1/2", 36'-1",
# 471.48 sf and a count of 7
REVU_VALUES = [
    (52, "length", 60.0),
    (58, "length", 30.0),
    (63, "length", 4.0),
    (127, "length", 1 + 8 / 12),             # inside a 1/4" viewport
    (93, "length", 108 + 3.5 / 12),          # polylength
    (132, "length", 36 + 1 / 12),            # polylength inside a 1/4" viewport
    (89, "area", 471.48),
    (97, "count", 7),
]


@pytest.mark.parametrize("xref, kind, value", REVU_VALUES)
def test_revu_measurement_matches_revu(revu, xref, kind, value):
    m = next(m for m in revu.markups() if m.xref == xref)
    got_kind, got_value, unit = m.measurement()
    assert (got_kind, unit) == (kind, "ea" if kind == "count" else "ft")
    assert got_value == pytest.approx(value, rel=1e-3)   # issue #12: within 0.1% of Revu


def test_revu_plain_markups_are_not_measurements(revu):
    # a PolyLine, a Polygon, an Arrow and a callout have no measurement, even though some carry an /IT intent
    for xref in (68, 86, 82, 74):
        assert next(m for m in revu.markups() if m.xref == xref).measurement() is None


def test_revu_scales_survive_save_and_undo_without_duplicates(revu, tmp_path):
    out = tmp_path / "saved.pdf"
    revu.save(str(out))
    again = Document(str(out))
    assert len(again.viewports) == 2
    assert again.scale_for(0).unit_per_pt == pytest.approx(EIGHTH_INCH, rel=1e-3)
    again.add_length(0, [(10, 10), (100, 10)])
    assert again.undo() and again.redo()
    assert len(again.viewports) == 2


def test_revu_scales_changed_in_openrevu_are_kept(revu, tmp_path):
    # the user removes a Revu viewport and replaces the Revu page scale: the /VP array, which is still in the file,
    # must not bring them back on the next open
    revu.remove_viewport(0)
    revu.set_scale(Scale.from_ratio(1, 10, "ft"), reset_pages=True)
    out = tmp_path / "changed.pdf"
    revu.save(str(out))
    again = Document(str(out))
    assert len(again.viewports) == 1
    assert again.scale_for(0).unit_per_pt == pytest.approx(10 / 72)


def test_revu_markups_appear_in_takeoff(revu):
    t = revu.takeoff_by_subject()
    n, total = t[("Count Measurement", "count", "ea")]
    assert (n, total) == (1, 7)                  # one count of 7 marks, not 7 counts of 7
    n, total = t[("Length Measurement", "length", "ft")]
    assert n == 4 and total == pytest.approx(60 + 30 + 4 + 1 + 8 / 12, rel=1e-3)
    n, total = t[("Polylength Measurement", "length", "ft")]
    assert n == 2 and total == pytest.approx(108 + 3.5 / 12 + 36 + 1 / 12, rel=1e-3)
    assert t[("Area Measurement", "area", "ft")][1] == pytest.approx(471.48, rel=1e-3)


def test_broken_revu_structures_are_ignored(tmp_path):
    import pymupdf as fitz
    d = fitz.open(SAMPLE)
    d.xref_set_key(52, "Measure", "<</Subtype/RL/X[<</U(furlong)/C -1>>]>>")   # unknown unit, negative factor
    d.xref_set_key(127, "Measure", "(not a dictionary)")
    d.update_object(49, "[ 50 0 R (junk) 999999 0 R <</BBox[1 2]/Measure 51 0 R>> ]")  # junk around the page scale
    d.xref_set_key(123, "Measure", "<</Subtype/RL/X 5>>")
    out = tmp_path / "broken.pdf"
    d.save(out)
    doc = Document(str(out))
    assert doc.scale_for(0).unit_per_pt == pytest.approx(EIGHTH_INCH, rel=1e-3)
    assert doc.viewports == []
    ms = {m.xref: m for m in doc.markups()}
    assert ms[52].measurement()[1] == pytest.approx(60, rel=1e-3)        # falls back to the page scale
    assert ms[127].measurement()[1] == pytest.approx(10 / 3, rel=1e-3)   # same 30 pt line at 1/8" instead of 1/4"
