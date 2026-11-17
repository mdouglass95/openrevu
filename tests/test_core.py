import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import math

import fitz
import pytest

from openrevu.core import Document, Scale, cloud_points, polygon_area


@pytest.fixture
def pdf(tmp_path):
    d = fitz.open()
    p = d.new_page(width=612, height=792)
    p.insert_text((72, 100), "Hello plan sheet")
    d.new_page()
    path = tmp_path / "t.pdf"
    d.save(path)
    return str(path)


def test_polygon_area():
    assert polygon_area([(0, 0), (10, 0), (10, 5), (0, 5)]) == 50
    assert polygon_area([(0, 0), (1, 1)]) == 0


def test_scale_calibration():
    s = Scale.from_calibration((0, 0), (100, 0), 20, "ft")  # 100pt == 20ft
    assert s.length([(0, 0), (50, 0)]) == pytest.approx(10)
    assert s.area([(0, 0), (100, 0), (100, 100), (0, 100)]) == pytest.approx(400)
    with pytest.raises(ValueError):
        Scale.from_calibration((0, 0), (0, 0), 5, "ft")
    with pytest.raises(ValueError):
        Scale.from_calibration((0, 0), (1, 0), 5, "furlong")
    assert Scale.from_json(s.to_json()) == s


def test_cloud_is_closed_polyline():
    pts = cloud_points(fitz.Rect(10, 10, 110, 60))
    assert pts[0] == pts[-1] and len(pts) > 20


def test_markups_roundtrip_and_takeoff(pdf, tmp_path):
    doc = Document(pdf)
    doc.set_scale(Scale.from_calibration((0, 0), (100, 0), 10, "m"))
    doc.add_rect(0, fitz.Rect(50, 50, 150, 100))
    doc.add_ellipse(0, fitz.Rect(50, 150, 150, 200))
    doc.add_line(0, (10, 10), (100, 100), arrow=True)
    doc.add_freehand(0, [(10, 10), (20, 30), (40, 20)])
    doc.add_cloud(0, fitz.Rect(200, 200, 300, 260))
    doc.add_highlight(0, fitz.Rect(60, 85, 200, 105))
    doc.add_text(0, fitz.Rect(300, 300, 420, 330), "check this")
    doc.add_note(1, (50, 50), "a note")
    _, ln = doc.add_length(0, [(0, 0), (100, 0), (100, 50)])
    _, ar = doc.add_area(0, [(0, 0), (100, 0), (100, 100), (0, 100)])
    doc.add_count(0, (300, 400)); doc.add_count(0, (320, 400))
    assert ln == pytest.approx(15) and ar == pytest.approx(100)
    tot = doc.takeoff()
    assert tot[("length", "m")] == pytest.approx(15)
    assert tot[("area", "m")] == pytest.approx(100)
    assert tot[("count", "ea")] == 2

    out = str(tmp_path / "out.pdf")
    doc.save(out)
    re = Document(out)
    assert re.scale.unit == "m" and re.scale.unit_per_pt == pytest.approx(0.1)
    assert re.takeoff() == pytest.approx(tot)
    assert len(re.markups()) == len(doc.markups())
    assert any(m.subject == "Cloud" for m in re.markups())


def test_save_in_place_and_delete(pdf):
    doc = Document(pdf)
    doc.add_rect(0, fitz.Rect(10, 10, 50, 50))
    doc.save()
    doc2 = Document(pdf)
    ms = doc2.markups()
    assert len(ms) == 1
    doc2.delete(ms[0])
    doc2.save()
    assert Document(pdf).markups() == []


def test_failed_in_place_save_keeps_the_document_usable(pdf, monkeypatch):
    doc = Document(pdf)
    doc.add_rect(0, fitz.Rect(10, 10, 50, 50))

    def locked(src, dst):
        raise PermissionError(13, "The process cannot access the file", dst)
    monkeypatch.setattr(os, "replace", locked)

    with pytest.raises(OSError, match="open in another program"):
        doc.save()
    assert not doc.doc.is_closed
    assert len(doc.markups()) == 1
    assert doc.modified
    assert not os.path.exists(pdf + ".tmp")

    monkeypatch.undo()
    doc.save()
    assert len(Document(pdf).markups()) == 1


def test_opening_a_signed_pdf_sets_the_signed_flag(signed_pdf, pdf):
    assert Document(signed_pdf).signed
    assert not Document(pdf).signed
    assert not Document().signed


def test_a_blank_signature_field_is_not_signed(pdf, tmp_path):
    pytest.importorskip("pyhanko")
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields
    out = tmp_path / "blank-field.pdf"
    with open(pdf, "rb") as f:
        w = IncrementalPdfFileWriter(f)
        fields.append_signature_field(w, fields.SigFieldSpec("SealHere", on_page=0, box=(40, 40, 240, 100)))
        with open(out, "wb") as o:
            w.write(o)
    assert not Document(str(out)).signed


def _without_sigflags(src, dst):
    d = fitz.open(src)
    d.xref_set_key(d.pdf_catalog(), "AcroForm/SigFlags", "null")
    d.save(dst)
    assert fitz.open(dst).get_sigflags() <= 0
    return str(dst)


def test_a_signed_pdf_without_sigflags_is_still_signed(signed_pdf, tmp_path):
    assert Document(_without_sigflags(signed_pdf, tmp_path / "noflags.pdf")).signed


def test_a_signed_field_nested_in_the_field_tree_counts(pdf, tmp_path):
    # Approvals (no /FT) > Seal (/FT /Sig) > widget with the /V; the type is inherited from Seal
    d = fitz.open(pdf)
    sig, group, field, kid = (d.get_new_xref() for _ in range(4))
    d.update_object(sig, "<</Type/Sig/Filter/Adobe.PPKLite/SubFilter/adbe.pkcs7.detached/ByteRange[0 0 0 0]/Contents<00>>>")
    d.update_object(group, f"<</T(Approvals)/Kids[{field} 0 R]>>")
    d.update_object(field, f"<</FT/Sig/T(Seal)/Parent {group} 0 R/Kids[{kid} 0 R]>>")
    d.update_object(kid, f"<</Type/Annot/Subtype/Widget/Rect[40 40 240 100]/Parent {field} 0 R/P {d[0].xref} 0 R"
                         f"/V {sig} 0 R>>")
    d.xref_set_key(d[0].xref, "Annots", f"[{kid} 0 R]")
    d.xref_set_key(d.pdf_catalog(), "AcroForm", f"<</Fields[{group} 0 R]>>")
    out = tmp_path / "nested.pdf"
    d.save(out)
    assert Document(str(out)).signed


def test_csv_and_render(pdf, tmp_path):
    doc = Document(pdf)
    doc.add_length(0, [(0, 0), (72, 0)], label=False)
    doc.add_note(0, (5, 5), "hi")
    csvp = tmp_path / "m.csv"
    doc.export_csv(csvp)
    rows = csvp.read_text().splitlines()
    assert len(rows) == 3 and "Length" in rows[1]
    pix = doc.render(0, 1.5)
    assert pix.width == pytest.approx(612 * 1.5, abs=1)
