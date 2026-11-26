"""Standard PDF measurement structures (ISO 32000-1 §12.9), as written by Bluebeam Revu and Acrobat.

A page may carry a /VP array of viewports. Each viewport has a /BBox in PDF user space and a /Measure dictionary.
A rectilinear /Measure (/Subtype /RL) holds number-format arrays; the first entry of /X has the factor /C that turns
PDF points into the unit /U. Measurement annotations carry an /IT intent and, in Revu and Acrobat output, their own
/Measure dictionary.

This module only reads the PDF. It returns plain (unit, factor) pairs; core.py turns them into Scales.
"""
from __future__ import annotations

import pymupdf as fitz

from .pdfobject import Reader, Ref

# (annotation type, /IT intent) of the standard measurement annotations (ISO 32000-1 §12.5.6; /PolygonCount is Revu's)
INTENTS = {("Line", "LineDimension"): "length", ("PolyLine", "PolyLineDimension"): "length",
           ("Polygon", "PolygonDimension"): "area", ("Polygon", "PolygonCount"): "count"}
INTENT_TYPES = {t for t, _ in INTENTS}
# Revu writes feet and inches as ' and ". Other /U strings are taken as OpenRevu unit names; Scale checks them.
_UNITS = {"'": "ft", '"': "in"}


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


def page_viewports(doc: fitz.Document, pno: int):
    """[(bbox, (unit, factor))] for the /VP viewports of a page that have a /Measure. bbox is in PDF user space."""
    vp = key(doc, doc.page_xref(pno), "VP")
    out = []
    for item in vp if isinstance(vp, list) else []:
        v = resolve(doc, item)
        if not isinstance(v, dict):
            continue
        bbox, m = v.get("BBox"), measure(doc, v.get("Measure"))
        if m and isinstance(bbox, list) and len(bbox) == 4 and all(type(n) in (int, float) for n in bbox):
            out.append((fitz.Rect(bbox).normalize(), m))
    return out
