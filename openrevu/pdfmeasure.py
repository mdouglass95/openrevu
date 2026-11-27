"""Standard PDF measurement structures (ISO 32000-1 §12.9), as written by Bluebeam Revu and Acrobat.

A page may carry a /VP array of viewports. Each viewport has a /BBox in PDF user space and a /Measure dictionary.
A rectilinear /Measure (/Subtype /RL) holds number-format arrays; the first entry of /X has the factor /C that turns
PDF points into the unit /U. Measurement annotations carry an /IT intent and, in Revu and Acrobat output, their own
/Measure dictionary.

This module reads and writes these structures as plain (unit, factor) pairs; core.py turns them into Scales.
"""
from __future__ import annotations

import hashlib

import pymupdf as fitz

from .pdfobject import Reader, Ref

# (annotation type, /IT intent) of the standard measurement annotations (ISO 32000-1 §12.5.6; /PolygonCount is Revu's)
INTENTS = {("Line", "LineDimension"): "length", ("PolyLine", "PolyLineDimension"): "length",
           ("Polygon", "PolygonDimension"): "area", ("Polygon", "PolygonCount"): "count"}
INTENT_TYPES = {t for t, _ in INTENTS}
# Revu's own /MeasurementTypes key, as in RevuTest.pdf (128 count, 129 area, 130 length)
_REVU_TYPES = {"length": 130, "area": 129, "count": 128}
# Revu writes feet and inches as ' and ". Other /U strings are taken as OpenRevu unit names; Scale checks them.
_UNITS = {"'": "ft", '"': "in"}
_PDF_UNITS = {v: k for k, v in _UNITS.items()}
# OpenRevu's mark on a page viewport written from the document's default scale (Revu has no document default)
DEFAULT_KEY = "OR_Default"


def parse(text: str):
    """Parse the source text of one PDF object. Raises ValueError or IndexError if it is damaged."""
    return Reader(text.encode("latin-1", "replace"), refs=True).parse()


def resolve(doc: fitz.Document, val):
    """Follow indirect references to the object they point at. None if one is broken or unreadable."""
    for _ in range(32):
        if not isinstance(val, Ref):
            return val
        if not 0 < val.num < doc.xref_length():
            return None
        try:
            val = parse(doc.xref_object(val.num, compressed=True))
        except (ValueError, IndexError):
            return None
    return None


def key(doc: fitz.Document, xref: int, name: str):
    """The parsed, resolved value of one key of an object, or None if it is missing or unreadable."""
    t, v = doc.xref_get_key(xref, name)
    if t == "null":
        return None
    try:
        return resolve(doc, parse(v))
    except (ValueError, IndexError):
        return None


def intent(doc: fitz.Document, xref: int, annot_type: str):
    """The measurement kind ("length", "area", "count") of a standard measurement annotation, else None."""
    if annot_type not in INTENT_TYPES:
        return None
    return INTENTS.get((annot_type, str(key(doc, xref, "IT") or "")))


def measure(doc: fitz.Document, m):
    """(unit, factor) of a /Measure dictionary (or a reference to one), or None if it is not rectilinear or
    has no number format. The unit and factor are not checked: Scale does that."""
    m = resolve(doc, m)
    if not isinstance(m, dict) or m.get("Subtype") != "RL":
        return None
    x = resolve(doc, m.get("X"))
    fmt = resolve(doc, x[0]) if isinstance(x, list) and x else None
    if not isinstance(fmt, dict):
        return None
    c = resolve(doc, fmt.get("C"))
    if type(c) not in (int, float):
        return None
    u = str(fmt.get("U", "")).strip()
    return _UNITS.get(u, u), float(c)


def _viewport(doc: fitz.Document, item):
    """(bbox, (unit, factor), default) of one /VP entry, or None if it has no usable /BBox and /Measure."""
    v = resolve(doc, item)
    if not isinstance(v, dict):
        return None
    bbox, m = v.get("BBox"), measure(doc, v.get("Measure"))
    if m and isinstance(bbox, list) and len(bbox) == 4 and all(type(n) in (int, float) for n in bbox):
        return fitz.Rect(bbox).normalize(), m, v.get(DEFAULT_KEY) is True
    return None


def page_viewports(doc: fitz.Document, pno: int):
    """[(bbox, (unit, factor), default)] for the /VP viewports of a page that have a /Measure. bbox is in PDF user
    space; default is True for a page viewport that OpenRevu wrote from the document's default scale."""
    vp = key(doc, doc.page_xref(pno), "VP")
    return [v for item in (vp if isinstance(vp, list) else []) if (v := _viewport(doc, item))]


def _num(v: float) -> str:
    return f"{v:.10g}"


def measure_source(unit: str, factor: float) -> str:
    """PDF source of a rectilinear /Measure dictionary: `factor` turns PDF points into `unit` (an OpenRevu unit name).
    /D (distance) and /A (area) count in the units of /X, so their factor is 1. Revu shows the ratio text /R as the
    scale; it is written in Revu's form, "<paper inches> in = 1 <unit>"."""
    u = _PDF_UNITS.get(unit, unit)    # unit is one of OpenRevu's names (Scale checks them)
    au = "sf" if unit == "ft" else "sq " + unit
    return (f"<</Type/Measure/Subtype/RL/R({_num(1 / (factor * 72))} in = 1 {unit})"
            f"/X[<</Type/NumberFormat/U({u})/C {_num(factor)}/D 100>>]"
            f"/D[<</Type/NumberFormat/U({u})/C 1/D 100>>]"
            f"/A[<</Type/NumberFormat/U({au})/C 1/D 100>>]>>")


def set_page_viewports(doc: fitz.Document, pno: int, page, details) -> None:
    """Replace the measuring viewports in the /VP of a page with `page` (the page scale, or None) and `details` (the
    viewports of parts of the page), each as page_viewports returns it: (bbox in PDF user space, (unit, factor),
    default). /VP entries that this module cannot read (other kinds of viewport) are kept.

    Revu ignores a page-scale viewport without an /NM id, and a detail viewport with neither /NM nor /Name. Revu's own
    page-scale viewport has no /Name and its detail viewports have /Name (Viewport), so these are written the same way."""
    xref = doc.page_xref(pno)
    vp = key(doc, xref, "VP")
    keep = [f"{i.num} 0 R" for i in (vp if isinstance(vp, list) else []) if isinstance(i, Ref) and _viewport(doc, i) is None]
    new = []
    for i, ((bbox, m, default), named) in enumerate(([(page, False)] if page else []) + [(d, True) for d in details]):
        box = " ".join(_num(c) for c in bbox)
        nm = hashlib.sha1(f"{xref} {i} {box} {m}".encode()).hexdigest()[:16].upper()
        new.append(f"<</Type/Viewport/BBox[{box}]" + ("/Name(Viewport)" if named else "")
                   + f"/Measure {measure_source(*m)}/NM({nm})" + (f"/{DEFAULT_KEY} true" if default else "") + ">>")
    doc.xref_set_key(xref, "VP", f"[{' '.join(new + keep)}]" if new or keep else "null")


def mark_measurement(doc: fitz.Document, xref: int, annot_type: str, kind: str, unit: str, factor: float) -> None:
    """Make a line, polyline or polygon annotation a standard measurement of `kind` ("length", "area"): its /IT intent,
    its own /Measure dictionary (`factor` turns points into `unit`), and the /MeasurementTypes key that Revu writes."""
    it = next(i for (t, i), k in INTENTS.items() if t == annot_type and k == kind)
    doc.xref_set_key(xref, "IT", "/" + it)
    doc.xref_set_key(xref, "Measure", measure_source(unit, factor))
    doc.xref_set_key(xref, "MeasurementTypes", str(_REVU_TYPES[kind]))
