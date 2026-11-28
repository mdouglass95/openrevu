"""Writing scales and measurements in the standard structures that Bluebeam Revu reads (issue #12, phase 2).

The expected PDF layout comes from RevuTest.pdf, a file calibrated and measured in Revu: its page scale is the /VP
viewport 50 0 R with /BBox [0 0 2621.288 2052.61] (the size of the rotated page), and its two detail viewports are
123 0 R and 125 0 R. The page is rotated 90 degrees and has an offset CropBox, so coordinate mistakes show.

OpenRevuRoundTrip_revu.pdf is a file written by OpenRevu, opened in Revu, its page-1 viewport deleted there, and saved
by Revu. OpenRevuRoundTrip_revu.csv is Revu's Markups List of the same file before the viewport was deleted.
"""
import csv
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

import pymupdf as fitz
import pytest

from openrevu import pdfmeasure
from openrevu.core import Document, Scale
from tests.test_revu import EIGHTH_INCH, QUARTER_INCH, SAMPLE, _set_revu_page_scale, _without_vp

ROUND_TRIP = Path(__file__).parent / "data" / "revu" / "OpenRevuRoundTrip_revu.pdf"
REVU_PAGE_BBOX = [0, 0, 2621.288, 2052.61]                 # /BBox of Revu's page-scale viewport, 50 0 R
REVU_DETAIL_BBOX = [1479.665, 2004.186, 2041.528, 2722.826]  # /BBox of Revu's detail viewport 123 0 R


def _saved(doc, tmp_path, name="out.pdf") -> Path:
    out = tmp_path / name
    doc.save(str(out))
    return out


def _viewports(path, pno=0):
    with fitz.open(path) as f:
        return pdfmeasure.page_viewports(f, pno)


def test_page_scale_is_written_as_a_revu_page_viewport(tmp_path):
    doc = Document(str(_without_vp(tmp_path)))
    doc.set_scale(Scale.from_ratio(0.25, 1, "ft"), page=0)
    (vp,) = _viewports(_saved(doc, tmp_path))
    bbox, (unit, factor) = vp[0], vp[1]
    assert list(bbox) == pytest.approx(REVU_PAGE_BBOX, abs=0.01)
    assert (unit, factor) == ("ft", pytest.approx(QUARTER_INCH))


def test_viewport_is_written_in_pdf_user_space_like_revu(tmp_path):
    # the visual rectangle OpenRevu reads from Revu's detail viewport 123 0 R must be written back with Revu's /BBox
    _, rect, _ = Document(str(SAMPLE)).viewports[0]
    doc = Document(str(_without_vp(tmp_path)))
    doc.add_viewport(0, rect, Scale.from_ratio(0.25, 1, "ft"))
    (vp,) = _viewports(_saved(doc, tmp_path))
    assert list(vp[0]) == pytest.approx(REVU_DETAIL_BBOX, abs=0.01)
    assert vp[1] == ("ft", pytest.approx(QUARTER_INCH))


def _two_pages(tmp_path) -> Path:
    out = tmp_path / "two.pdf"
    with fitz.open() as d:
        d.new_page(width=612, height=792)
        d.new_page(width=792, height=612)
        d.save(out)
    return out


def test_pages_on_the_default_scale_keep_following_it(tmp_path):
    doc = Document(str(_two_pages(tmp_path)))
    doc.set_scale(Scale.from_ratio(0.25, 1, "ft"))
    doc.set_scale(Scale.from_ratio(0.125, 1, "ft"), page=1)
    out = _saved(doc, tmp_path)
    (p0,), (p1,) = _viewports(out, 0), _viewports(out, 1)
    assert list(p0[0]) == pytest.approx([0, 0, 612, 792], abs=0.01) and p0[1] == ("ft", pytest.approx(QUARTER_INCH))
    assert p1[1] == ("ft", pytest.approx(EIGHTH_INCH))      # Revu sees both pages calibrated
    again = Document(str(out))
    again.set_scale(Scale.from_ratio(1, 10, "ft"))
    assert again.scale_for(0).unit_per_pt == pytest.approx(10 / 72)     # page 0 follows the new default
    assert again.scale_for(1).unit_per_pt == pytest.approx(EIGHTH_INCH)  # page 1 keeps its own scale
    (p0,) = _viewports(_saved(again, tmp_path, "again.pdf"), 0)
    assert p0[1] == ("ft", pytest.approx(10 / 72))


def test_viewport_deleted_in_revu_after_an_openrevu_save_stays_deleted(tmp_path):
    doc = Document(str(_two_pages(tmp_path)))
    doc.set_scale(Scale.from_ratio(0.25, 1, "ft"), page=0)
    doc.add_viewport(0, (100, 100, 300, 300), Scale.from_ratio(1, 10, "ft"))
    out = _saved(doc, tmp_path)
    _set_revu_page_scale(out, QUARTER_INCH)   # Revu deletes the viewport: the page keeps only its page-scale viewport
    again = Document(str(out))
    assert again.viewports == []
    assert again.scale_for(0).unit_per_pt == pytest.approx(QUARTER_INCH)


def _legacy_file(tmp_path) -> Path:
    """A file saved before OpenRevu wrote /VP: page scale and viewport only in the settings blob in the keywords."""
    out = tmp_path / "legacy.pdf"
    blob = {"default": Scale().to_dict(), "pages": {"0": Scale.from_ratio(0.25, 1, "ft").to_dict()},
            "viewports": [{"page": 0, "rect": [100, 100, 300, 300], "scale": Scale.from_ratio(1, 10, "ft").to_dict()}],
            "style": "decimal", "fraction": 16, "columns": [], "vp": []}
    with fitz.open(_two_pages(tmp_path)) as d:
        d.set_metadata({"keywords": "OpenRevu-scales=" + json.dumps(blob)})
        d.save(out)
    return out


def test_scales_of_a_file_saved_before_vp_move_into_vp_on_the_next_edit(tmp_path):
    doc = Document(str(_legacy_file(tmp_path)))
    assert doc.scale_for(0).unit_per_pt == pytest.approx(QUARTER_INCH)
    assert [(p, list(r)) for p, r, _ in doc.viewports] == [(0, [100, 100, 300, 300])]
    doc.add_rect(1, (10, 10, 50, 50))
    out = _saved(doc, tmp_path)
    page, detail = _viewports(out, 0)
    assert page[1] == ("ft", pytest.approx(QUARTER_INCH))
    assert list(detail[0]) == pytest.approx([100, 492, 300, 692], abs=0.01)   # y flipped: page is 792 pt high
    again = Document(str(out))
    assert again.scale_for(0).unit_per_pt == pytest.approx(QUARTER_INCH)
    assert [(p, list(r)) for p, r, _ in again.viewports] == [(0, pytest.approx([100, 100, 300, 300], abs=0.01))]


def test_viewports_move_with_their_page_when_pages_are_deleted(tmp_path):
    doc = Document(str(_two_pages(tmp_path)))
    doc.insert_blank(0)
    doc.add_viewport(2, (100, 100, 300, 300), Scale.from_ratio(1, 10, "ft"))
    doc.delete_pages([0])
    assert [p for p, _, _ in doc.viewports] == [1]
    again = Document(str(_saved(doc, tmp_path)))
    assert [p for p, _, _ in again.viewports] == [1]
    assert again.scale_at(1, [(200, 200)]).unit_per_pt == pytest.approx(10 / 72)


def test_extracted_pages_keep_their_scales_in_vp(tmp_path):
    doc = Document(str(_two_pages(tmp_path)))
    doc.set_scale(Scale.from_ratio(0.125, 1, "ft"), page=1)
    doc.add_viewport(1, (100, 100, 300, 300), Scale.from_ratio(1, 10, "ft"))
    out = tmp_path / "page2.pdf"
    doc.extract_pages([1], str(out))
    page, detail = _viewports(out, 0)
    assert page[1] == ("ft", pytest.approx(EIGHTH_INCH)) and detail[1] == ("ft", pytest.approx(10 / 72))
    assert Document(str(out)).scale_for(0).unit_per_pt == pytest.approx(EIGHTH_INCH)


def test_revu_vp_stays_untouched_until_a_scale_on_its_page_changes(tmp_path):
    doc = Document(str(SAMPLE))
    doc.add_rect(0, (10, 10, 50, 50))
    doc.set_scale(Scale.from_ratio(1, 10, "ft"), page=0)
    doc.undo()
    assert doc.doc.xref_get_key(doc.doc.page_xref(0), "VP") == ("xref", "49 0 R")   # Revu's own /VP array


def test_changing_the_page_scale_of_a_revu_sheet_keeps_its_viewports(tmp_path):
    doc = Document(str(SAMPLE))
    doc.set_scale(Scale.from_ratio(0.25, 1, "ft"), page=0)
    page, *details = _viewports(_saved(doc, tmp_path))
    assert page[1] == ("ft", pytest.approx(QUARTER_INCH))
    assert len(details) == 2 and list(details[0][0]) == pytest.approx(REVU_DETAIL_BBOX, abs=0.01)
    assert all(d[1] == ("ft", pytest.approx(QUARTER_INCH, rel=1e-6)) for d in details)
    doc.undo()
    page, *details = pdfmeasure.page_viewports(doc.doc, 0)
    assert page[1] == ("ft", pytest.approx(EIGHTH_INCH, rel=1e-6)) and len(details) == 2


def test_openrevu_lengths_and_areas_are_standard_measurements(tmp_path):
    doc = Document(str(_two_pages(tmp_path)))
    doc.set_scale(Scale.from_ratio(0.125, 1, "ft"))
    doc.add_length(0, [(100, 100), (172, 100)])                         # 1 in on paper = 8 ft
    doc.add_length(0, [(100, 200), (172, 200), (172, 272)])             # 2 in = 16 ft
    doc.add_area(0, [(300, 300), (372, 300), (372, 372), (300, 372)])   # 1 sq in = 64 sq ft
    expected = {"Line": ("length", 130, 8), "PolyLine": ("length", 130, 16), "Polygon": ("area", 129, 64)}
    stripped = tmp_path / "standard-only.pdf"
    with fitz.open(_saved(doc, tmp_path)) as f:
        marks = {a.type[1]: a.xref for a in f[0].annots() if a.type[1] in expected}
        assert set(marks) == set(expected)
        for subtype, xref in marks.items():
            kind, types, _ = expected[subtype]
            assert pdfmeasure.intent(f, xref, subtype) == kind
            assert pdfmeasure.measure(f, pdfmeasure.key(f, xref, "Measure")) == ("ft", pytest.approx(EIGHTH_INCH))
            assert f.xref_get_key(xref, "MeasurementTypes") == ("int", str(types))   # Revu's own key, as in RevuTest
            f.xref_set_key(xref, "OR_Measure", "null")    # what Revu and Acrobat read: no OpenRevu keys
        f.save(stripped)
    values = {m.kind: m.measurement() for m in Document(str(stripped)).markups()}
    for subtype, (kind, _, value) in expected.items():
        assert values[subtype] == (kind, pytest.approx(value), "ft")


def test_measurements_that_revu_would_value_differently_get_no_standard_intent(tmp_path):
    # Revu counts a polygon with /PolygonDimension as an area: a perimeter, a volume, or an area minus cut-outs
    # would show a different value there
    doc = Document(str(_two_pages(tmp_path)))
    sq = [(300, 300), (372, 300), (372, 372), (300, 372)]
    marks = [doc.add_perimeter(0, sq)[0], doc.add_area(0, sq, depth=2)[0],
             doc.add_area(0, sq, cutouts=[[(310, 310), (320, 310), (320, 320)]])[0]]
    for a in marks:
        assert doc.doc.xref_get_key(a.xref, "IT") == ("null", "null")
        assert doc.doc.xref_get_key(a.xref, "Measure") == ("null", "null")


def test_resized_openrevu_length_tells_revu_the_scale_it_was_measured_at(tmp_path):
    doc = Document(str(_two_pages(tmp_path)))
    doc.set_scale(Scale.from_ratio(0.125, 1, "ft"))
    doc.add_length(0, [(100, 100), (172, 100)], label=False)    # 8 ft at 1/8"
    doc.set_scale(Scale.from_ratio(0.25, 1, "ft"))              # recalibrated
    (m,) = doc.markups()
    r = m.rect
    m.resize((r.x0, r.y0, r.x1 + 72, r.y1))                     # now 2 in on paper: 8 ft at 1/4" (the scale in force)
    (m,) = doc.markups()
    assert m.measurement() == ("length", pytest.approx(8), "ft")
    assert pdfmeasure.measure(doc.doc, pdfmeasure.key(doc.doc, m.xref, "Measure")) == ("ft", pytest.approx(QUARTER_INCH))


def test_revu_length_keeps_its_own_scale_through_two_resizes():
    # the Revu 60'-0" length (xref 52) carries its own 1/8" /Measure; the page scale is changed in OpenRevu to 1:10 ft
    revu = Document(str(SAMPLE))
    revu.set_scale(Scale.from_ratio(1, 10, "ft"), reset_pages=True)
    for _ in range(2):
        line = next(m for m in revu.markups() if m.xref == 52)
        r = line.rect
        line.resize((r.x0, r.y0, r.x1 + 9, r.y1))   # 9 pt longer, along the line: 1 ft at 1/8"
    assert next(m for m in revu.markups() if m.xref == 52).measurement() == ("length", pytest.approx(62, rel=1e-3), "ft")


def test_rotating_a_page_rewrites_its_page_viewport_to_the_rotated_size(tmp_path):
    doc = Document(str(_two_pages(tmp_path)))
    doc.set_scale(Scale.from_ratio(0.25, 1, "ft"), page=0)
    doc.add_rect(0, (10, 10, 50, 50))
    doc.rotate_pages([0], 90)
    (vp,) = _viewports(_saved(doc, tmp_path))
    assert list(vp[0]) == pytest.approx([0, 0, 792, 612], abs=0.01)   # Revu: the visual size of the rotated page


def test_cropping_a_page_rewrites_its_page_viewport_to_the_cropped_size(tmp_path):
    doc = Document(str(_two_pages(tmp_path)))
    doc.set_scale(Scale.from_ratio(0.25, 1, "ft"), page=0)
    doc.crop_page(0, (0, 0, 300, 400))
    (vp,) = _viewports(_saved(doc, tmp_path))
    assert list(vp[0]) == pytest.approx([0, 0, 300, 400], abs=0.01)


# A viewport at visual (100, 100, 300, 300) on an upright 612 x 792 pt page has this /BBox in PDF user space (y up).
DETAIL_BBOX = [100, 492, 300, 692]
DETAIL = Scale.from_ratio(1, 10, "ft")


def _visual_viewports(doc):
    return [(p, pytest.approx(list(r), abs=0.01)) for p, r, _ in doc.viewports]


def test_rotating_a_page_keeps_its_viewport_on_the_same_part_of_the_drawing(tmp_path):
    doc = Document(str(_two_pages(tmp_path)))
    doc.add_viewport(0, (100, 100, 300, 300), DETAIL)
    doc.rotate_pages([0], 90)
    # turned 90 degrees clockwise, the drawing under visual (100, 100, 300, 300) is now at x 792-300..792-100
    assert _visual_viewports(doc) == [(0, [492, 100, 692, 300])]
    assert doc.scale_at(0, [(592, 200)]).unit_per_pt == pytest.approx(DETAIL.unit_per_pt)
    assert doc.scale_at(0, [(200, 200)]) == doc.scale_for(0)      # where the viewport was before the turn
    out = _saved(doc, tmp_path)
    assert [list(b) for b, _, _ in _viewports(out)] == [pytest.approx(DETAIL_BBOX, abs=0.01)]   # Revu sees no move
    assert _visual_viewports(Document(str(out))) == [(0, [492, 100, 692, 300])]
    doc.undo()
    assert _visual_viewports(doc) == [(0, [100, 100, 300, 300])]


def test_cropping_a_page_keeps_its_viewports_on_the_same_part_of_the_drawing(tmp_path):
    doc = Document(str(_two_pages(tmp_path)))
    doc.add_viewport(0, (100, 100, 300, 300), DETAIL)
    doc.crop_page(0, (50, 50, 500, 700))
    assert _visual_viewports(doc) == [(0, [50, 50, 250, 250])]
    assert doc.scale_at(0, [(60, 60)]).unit_per_pt == pytest.approx(DETAIL.unit_per_pt)
    assert [list(b) for b, _, _ in _viewports(_saved(doc, tmp_path))] == [pytest.approx(DETAIL_BBOX, abs=0.01)]


def test_a_crop_that_cuts_a_viewport_off_the_page_keeps_the_whole_viewport(tmp_path):
    doc = Document(str(_two_pages(tmp_path)))
    doc.add_viewport(0, (100, 100, 300, 300), DETAIL)
    doc.crop_page(0, (200, 200, 612, 792))                        # cuts the viewport in part: it is not clipped
    assert _visual_viewports(doc) == [(0, [-100, -100, 100, 100])]
    assert doc.scale_at(0, [(50, 50)]).unit_per_pt == pytest.approx(DETAIL.unit_per_pt)
    doc.crop_page(0, (300, 300, 412, 592))                        # the viewport is now wholly off the page
    assert _visual_viewports(doc) == [(0, [-400, -400, -200, -200])]
    out = _saved(doc, tmp_path)
    assert [list(b) for b, _, _ in _viewports(out)] == [pytest.approx(DETAIL_BBOX, abs=0.01)]
    doc.undo(); doc.undo()                                        # undoing the crops shows it where it was set
    assert _visual_viewports(doc) == [(0, [100, 100, 300, 300])]


def test_a_slip_sheeted_page_keeps_its_page_scale(tmp_path):
    doc = Document(str(_two_pages(tmp_path)))
    doc.set_scale(Scale.from_ratio(0.25, 1, "ft"), page=0)
    doc.add_rect(0, (10, 10, 50, 50))
    doc.slip_sheet(str(_two_pages(tmp_path)), match="page")     # new pages, no /VP
    again = Document(str(_saved(doc, tmp_path)))
    assert again.scale_for(0).unit_per_pt == pytest.approx(QUARTER_INCH)


def test_viewports_carry_the_ids_and_ratio_text_revu_needs(tmp_path):
    # Revu ignored a page viewport without /NM and a detail viewport with neither /NM nor /Name (round trip, VPVariants
    # pages C and F), and shows /R as the scale, written as "<paper> in = 1 <unit>" (RevuTest object 51)
    doc = Document(str(_two_pages(tmp_path)))
    doc.set_scale(Scale.from_ratio(0.125, 1, "ft"), page=0)
    doc.add_viewport(0, (100, 100, 300, 300), Scale.from_ratio(0.25, 1, "ft"))
    with fitz.open(_saved(doc, tmp_path)) as f:
        page, detail = pdfmeasure.key(f, f.page_xref(0), "VP")
        page, detail = pdfmeasure.resolve(f, page), pdfmeasure.resolve(f, detail)
        ids = page.get("NM"), detail.get("NM")
        assert all(ids) and ids[0] != ids[1]
        assert "Name" not in page and detail.get("Name") == "Viewport"
        assert pdfmeasure.resolve(f, page["Measure"])["R"] == "0.125 in = 1 ft"
        assert pdfmeasure.resolve(f, detail["Measure"])["R"] == "0.25 in = 1 ft"


def test_openrevu_file_saved_by_revu_keeps_its_scales_and_values():
    doc = Document(str(ROUND_TRIP))
    assert [round(doc.scale_for(p).unit_per_pt * 72, 6) for p in range(3)] == [8, 4, 8]   # ft per inch on paper
    assert doc.viewports == []                                    # deleted in Revu
    with open(ROUND_TRIP.with_suffix(".csv"), encoding="utf-8-sig") as fh:
        revu = {r["Subject"]: float(r["Measurement"]) for r in csv.DictReader(fh)
                if r["ID"] and r["Measurement Unit"] in ("sf", "ft' in\"")}
    # deleting the viewport made Revu measure VL1 again at the page scale: it saved VL1 with /Contents (8'-0")
    revu["VL1 10 ft"] = 8
    ours = {m.subject: m.measurement()[1] for m in doc.markups() if m.measurement()}
    assert set(ours) == set(revu) | {"P1 80 ft (plain in Revu)"}   # the perimeter is a plain polygon in Revu
    for subject, value in revu.items():
        assert ours[subject] == pytest.approx(value, rel=1e-3), subject
    doc.set_scale(Scale.from_ratio(1, 10, "ft"))                  # Revu kept /OR_Default: pages 1 and 3 follow it
    assert [round(doc.scale_for(p).unit_per_pt * 72, 6) for p in range(3)] == [10, 4, 10]
