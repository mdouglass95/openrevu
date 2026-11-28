"""Qt-free core: PDF markup, scale calibration, takeoff, undo/redo."""
from __future__ import annotations

import base64
import contextlib
import csv
import getpass
import hashlib
import json
import math
import os
import re
import secrets
import time
from dataclasses import dataclass

import pymupdf as fitz  # PyMuPDF

from . import _journal, pdfmeasure
from ._util import mutates as _mutates, replace_file
from .pages import PageOps
from .sheets import SheetOps

TAG = "OpenRevu"
UNITS = {"mm": 1 / 25.4, "cm": 1 / 2.54, "m": 1 / 0.0254, "in": 1.0, "ft": 1 / 12.0, "yd": 1 / 36.0}
STATUSES = ["", "Accepted", "Rejected", "Cancelled", "Completed", "Reviewed"]
MEASURE_KEY, STATUS_KEY, PARENT_KEY = "OR_Measure", "OR_Status", "OR_Parent"
VERTEX_TYPES = {"Line", "PolyLine", "Polygon", "Ink"}
RECT_TYPES = {"Square", "Circle", "FreeText", "Text", "Stamp"}
TEXT_MARKUP_TYPES = {"Highlight", "Underline", "StrikeOut", "Squiggly"}  # move-only (quad points)
DEPTH_KEY = "OR_Depth"
CUSTOM_KEY = "OR_Custom"
COLUMN_TYPES = ("text", "number", "choice")
BASE_COLUMNS = ("Page", "Subject", "Type", "Author", "Status", "Comment", "Value")
RECREATABLE_KINDS = {"Square", "Circle", "Line", "PolyLine", "Polygon", "Ink", "FreeText", "Text"}  # can be copied / saved as tools
RESIZABLE_MEASURES = {"length", "perimeter", "area", "volume", "diameter", "angle"}
MEASURE_KINDS = RESIZABLE_MEASURES | {"count"}
MEASURE_UNITS = set(UNITS) | {"deg", "ea"}  # deg for angles, ea for counts


@dataclass
class Scale:
    """Maps PDF points to real-world units: real = unit_per_pt * points."""
    unit: str = "ft"
    unit_per_pt: float = 1 / 72 / 12  # 1 pt == 1/72 in, i.e. 1:1 paper

    def __post_init__(self):
        # Scales can come from untrusted PDF metadata, and the unit is written into PDF string literals.
        if self.unit not in UNITS:
            raise ValueError(f"unknown unit {self.unit!r}")
        if not math.isfinite(self.unit_per_pt) or self.unit_per_pt <= 0:
            raise ValueError(f"invalid unit_per_pt {self.unit_per_pt!r}")

    @classmethod
    def from_calibration(cls, p1, p2, real_length: float, unit: str) -> "Scale":
        d = math.dist(p1, p2)
        if d <= 0 or real_length <= 0:
            raise ValueError("calibration needs two distinct points and a positive length")
        return cls(unit, real_length / d)

    @classmethod
    def from_ratio(cls, paper_in: float, real: float, unit: str) -> "Scale":
        """e.g. 1/4 in on paper = 1 ft real: from_ratio(0.25, 1, 'ft')."""
        if paper_in <= 0 or real <= 0:
            raise ValueError("invalid ratio")
        return cls(unit, real / (paper_in * 72))

    def length(self, pts) -> float:
        return sum(math.dist(a, b) for a, b in zip(pts, pts[1:])) * self.unit_per_pt

    def area(self, pts) -> float:
        return polygon_area(pts) * self.unit_per_pt ** 2

    def to_dict(self):
        return {"unit": self.unit, "unit_per_pt": self.unit_per_pt}

    @classmethod
    def from_dict(cls, d) -> "Scale":
        return cls(d["unit"], float(d["unit_per_pt"]))

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, s: str) -> "Scale":
        return cls.from_dict(json.loads(s))


def polygon_area(pts) -> float:
    """Shoelace formula, absolute value."""
    pts = list(pts)
    if len(pts) < 3:
        return 0.0
    s = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def angle_deg(a, b, c) -> float:
    """Angle at vertex b between rays b->a and b->c, degrees in [0, 180]."""
    v1, v2 = (a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1])
    n = math.hypot(*v1) * math.hypot(*v2)
    if n == 0:
        raise ValueError("degenerate angle")
    return math.degrees(math.acos(max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / n))))


def cloud_points(rect: fitz.Rect, arc: float = 14.0, steps: int = 6):
    """Closed polyline of outward scallops around rect (revision cloud)."""
    r = fitz.Rect(rect).normalize()
    corners = [r.tl, r.tr, r.br, r.bl, r.tl]
    pts = []
    for a, b in zip(corners, corners[1:]):
        n = max(1, round(math.dist(a, b) / arc))
        dx, dy = (b.x - a.x) / n, (b.y - a.y) / n
        nx, ny = dy, -dx
        for i in range(n):
            sx, sy = a.x + dx * i, a.y + dy * i
            for k in range(steps):
                t = k / steps
                bulge = math.sin(math.pi * t) * 0.35
                pts.append((sx + dx * t + nx * bulge, sy + dy * t + ny * bulge))
    pts.append(pts[0])
    return pts


STYLES = ("decimal", "feet-inches")
FRACTIONS = (2, 4, 8, 16, 32, 64)


def feet_inches(inches: float, denominator: int = 16) -> str:
    """Architectural length, rounded to the nearest 1/denominator inch: 150.5 -> 12' 6 1/2\"."""
    if denominator not in FRACTIONS:
        raise ValueError(f"the fraction must be one of {FRACTIONS}")
    sign = "-" if inches < 0 else ""
    units = round(abs(inches) * denominator)            # whole number of 1/denominator inches
    whole, frac = divmod(units, denominator)
    feet, inch = divmod(whole, 12)
    if frac:
        g = math.gcd(frac, denominator)
        inch_text = f"{inch} {frac // g}/{denominator // g}" if inch else f"{frac // g}/{denominator // g}"
    else:
        inch_text = str(inch)
    if feet and not (inch or frac):
        return f"{sign}{feet}'"
    return f"{sign}{feet}' {inch_text}\"" if feet else f"{sign}{inch_text}\""


def format_value(kind: str, val: float, unit: str, style: str = "decimal", denominator: int = 16) -> str:
    """Human-readable measurement, e.g. 'area' 12.5 'ft' -> '12.50 ft²'.
    With style='feet-inches', lengths in feet or inches read like 12' 6 1/2\"."""
    suffix = {"area": "²", "volume": "³"}.get(kind, "")
    if kind == "angle":
        return f"{val:.1f}°"
    if kind == "count":
        return f"{val:g} ea"
    if style == "feet-inches" and kind in ("length", "perimeter", "diameter") and unit in ("ft", "in"):
        return feet_inches(val * (12 if unit == "ft" else 1), denominator)
    return f"{val:.2f} {unit}{suffix}"


def _same_rect(a, b, tol=0.5) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _pdf_scale(m):
    """The Scale of a (unit, factor) pair read by pdfmeasure, or None if OpenRevu cannot use it."""
    try:
        return Scale(*m) if m else None
    except ValueError:
        return None


# The relative precision of a number read back from a PDF: MuPDF keeps them as single-precision floats.
_PDF_TOL = 1e-6


def _same_scale(a: Scale, b: Scale) -> bool:
    return a.unit == b.unit and math.isclose(a.unit_per_pt, b.unit_per_pt, rel_tol=_PDF_TOL)


def _same_viewport(a, b) -> bool:
    """True if two (bbox, (unit, factor), default) viewports are the same, within the precision of a PDF."""
    (ra, (ua, fa), da), (rb, (ub, fb), db) = a, b
    return ua == ub and math.isclose(fa, fb, rel_tol=_PDF_TOL) and da == db and _same_rect(ra, rb, tol=0.01)


def _same_viewports(a, b) -> bool:
    return len(a) == len(b) and all(map(_same_viewport, a, b))


def _fingerprint(vps) -> str:
    """The fingerprint of a page's /VP viewports that settings saved before OpenRevu wrote /VP kept (see
    Document._import_legacy_viewports)."""
    return hashlib.sha1(repr([(b, m) for b, m, _ in vps]).encode()).hexdigest()[:16]


def _xget(doc, xref, key):
    t, v = doc.xref_get_key(xref, key)
    return None if t == "null" else v


def _byte_range_intact(byte_range: str, data: bytes) -> bool:
    """True if a signature's /ByteRange [a b c d] still fits the file: the signed ranges lie inside it, and the gap
    between them holds exactly the hex string of the signature's /Contents."""
    try:
        a, b, c, d = (int(n) for n in byte_range.strip("[] ").split())
    except ValueError:
        return False
    if not (a == 0 and 0 < b < c and d >= 0 and c + d <= len(data)):
        return False
    gap = data[b:c]
    return len(gap) >= 2 and gap[:1] == b"<" and gap[-1:] == b">" and bytes(gap[1:-1]).strip(b"0123456789abcdefABCDEF \r\n") == b""


def _refs(doc, kind, value) -> list[int]:
    """The xrefs in a PDF array value from xref_get_key, following one indirect reference to the array."""
    if kind == "xref":
        value = doc.xref_object(int(value.split()[0]), compressed=True)
    elif kind != "array":
        return []
    return [int(n) for n in re.findall(r"(\d+) \d+ R", value)]


class Markup:
    """A PDF annotation plus OpenRevu metadata (measurement, status, replies)."""

    def __init__(self, annot: fitz.Annot, page_no: int, page: fitz.Page, d: "Document"):
        self._annot, self.page_no, self._page, self.d = annot, page_no, page, d  # keep page alive
        self._gen = d.generation
        self._xref = annot.xref

    @property
    def annot(self) -> fitz.Annot:
        if self._gen != self.d.generation:  # underlying document was reopened (undo/redo): avoid a native crash
            raise RuntimeError("stale markup: re-fetch it from Document.markups()")
        return self._annot

    @property
    def xref(self) -> int:
        return self._xref

    @property
    def kind(self) -> str:
        return self.annot.type[1]

    @property
    def subject(self) -> str:
        return self.annot.info.get("subject", "")

    @property
    def comment(self) -> str:
        return self.annot.info.get("content", "")

    @property
    def author(self) -> str:
        return self.annot.info.get("title", "")

    @property
    def date(self) -> str:
        return self.annot.info.get("modDate", "") or self.annot.info.get("creationDate", "")

    @property
    def rect(self) -> fitz.Rect:
        """Bounding box in visual page coordinates."""
        return self.d._rv(self.page_no, self.annot.rect)

    @property
    def status(self) -> str:
        v = _xget(self.d.doc, self.xref, STATUS_KEY)
        return v or ""

    def measurement(self):
        """(kind, value, unit) if this is a measurement markup, else None. A length or area made in OpenRevu also carries
        the standard /IT and /Measure, which OpenRevu keeps up to date. If Revu measured it again (for example after its
        viewport was deleted there), the value from /Measure no longer agrees with OR_Measure, and the /Measure value wins."""
        doc = self.d.doc
        v = _xget(doc, self.xref, MEASURE_KEY)
        kind = pdfmeasure.intent(doc, self.xref, self.kind)
        own = self._own_scale() if kind else None
        if not v:
            return self._pdf_measurement(kind, own)
        stored = self._stored_measurement(v)
        std = self._pdf_measurement(kind, own) if own else None
        if std and not (stored and stored[::2] == std[::2] and math.isclose(stored[1], std[1], rel_tol=_PDF_TOL)):
            return std
        return stored

    @staticmethod
    def _stored_measurement(v):
        """(kind, value, unit) of an OR_Measure string, or None if it is malformed."""
        try:  # OR_Measure comes from the PDF, so a hand-edited or foreign file can hold anything
            kind, val, unit = v.split(":", 2)
            val = float(val)
        except ValueError:
            return None
        if kind not in MEASURE_KINDS or unit not in MEASURE_UNITS or not math.isfinite(val):
            return None
        return kind, val, unit

    def _pdf_measurement(self, kind, own):
        """A standard measurement (made in Revu or Acrobat, or a length or area made in OpenRevu): its /IT intent gives
        the `kind`, its own /Measure dictionary the scale (`own`, else the viewport or page scale at its position), and
        the value is computed from its geometry."""
        doc = self.d.doc
        if kind is None:
            return None
        if kind == "count":   # Revu groups the marks of one count: the others are /RT /Group replies to this one
            marks = sum(1 for a in self._page.annots() or []
                        if _xget(doc, a.xref, "IRT") == f"{self.xref} 0 R" and _xget(doc, a.xref, "RT") == "/Group")
            return "count", float(1 + marks), "ea"
        pts = self.points()
        if len(pts) < 2:
            return None
        sc = own or self.d.scale_at(self.page_no, pts)
        return kind, (sc.area(pts) if kind == "area" else sc.length(pts)), sc.unit

    def _own_scale(self):
        """The Scale of this markup's own /Measure dictionary, or None."""
        doc = self.d.doc
        return _pdf_scale(pdfmeasure.measure(doc, pdfmeasure.key(doc, self.xref, "Measure")))

    def custom(self) -> dict:
        """Values of the custom columns that are set on this markup."""
        v = _xget(self.d.doc, self.xref, CUSTOM_KEY)
        if not v:
            return {}
        try:
            return json.loads(base64.b64decode(v).decode("utf-8"))
        except (ValueError, TypeError):
            return {}

    def set_custom(self, name: str, value) -> None:
        """Set a custom column value. An empty value clears it. Numbers and choices are checked."""
        col = next((c for c in self.d.columns if c["name"] == name), None)
        if col is None:
            raise KeyError(f"no custom column named {name!r}")
        if value in (None, ""):
            value = None
        elif col["type"] == "number":
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{name!r} needs a number, not {value!r}") from None
        elif col["type"] == "choice":
            if value not in col["choices"]:
                raise ValueError(f"{value!r} is not one of {col['choices']}")
        else:
            value = str(value)
        vals = {k: v for k, v in self.custom().items() if k != name}
        if value is not None:
            vals[name] = value
        enc = base64.b64encode(json.dumps(vals, ensure_ascii=False).encode("utf-8")).decode("ascii")
        with self._edit():
            self.d.doc.xref_set_key(self.xref, CUSTOM_KEY, f"({enc})" if vals else "null")

    def replies(self):
        out = []
        for a in self._page.annots() or []:
            if _xget(self.d.doc, a.xref, "IRT") == f"{self.xref} 0 R":
                out.append((a.info.get("title", ""), a.info.get("content", "")))
        return out

    # --- editing (each is one undo step) ---
    def _edit(self, merge=None):
        return self.d._operation("edit markup", merge)

    def set_colors(self, stroke=None, fill=None):
        kw = {}
        if stroke is not None:
            kw["stroke"] = stroke
        if fill is not None:
            kw["fill"] = fill
        with self._edit():
            self.annot.set_colors(**kw)
            self.annot.update()

    def set_width(self, w):
        """Consecutive width changes of one markup are one undo step (a spin box sends one per click)."""
        with self._edit(merge=("width", self.xref)):
            self.annot.set_border(width=w)
            self.annot.update()

    def set_opacity(self, o):
        """Consecutive opacity changes of one markup are one undo step."""
        with self._edit(merge=("opacity", self.xref)):
            self.annot.set_opacity(o)
            self.annot.update()

    def set_subject(self, s):
        with self._edit():
            self.annot.set_info(subject=s)
            self.annot.update()

    def set_comment(self, c):
        with self._edit():
            self.annot.set_info(content=c)
            self.annot.update()

    def set_status(self, s):
        if s not in STATUSES:
            raise ValueError(f"unknown status {s!r}")
        with self._edit():
            self.d.doc.xref_set_key(self.xref, STATUS_KEY, f"({s})" if s else "null")

    def _children(self):
        return [a for a in self._page.annots() or []
                if _xget(self.d.doc, a.xref, PARENT_KEY) == str(self.xref)]

    def points(self):
        """Geometry in visual coordinates: list of points; for Ink, list of strokes."""
        v, pn = self.annot.vertices, self.page_no
        if self.kind == "Ink":
            return [[tuple(self.d._tv(pn, p)) for p in st] for st in v]
        return [tuple(self.d._tv(pn, p)) for p in v or []]

    def _check_transformable(self, resize=False):
        k = self.kind
        if k in TEXT_MARKUP_TYPES:
            if resize:
                raise ValueError("text markups follow their text; move them instead of resizing")
        elif k not in VERTEX_TYPES and k not in RECT_TYPES:
            raise ValueError(f"cannot move or resize {k} markups")

    def move(self, dx, dy):
        """Move by (dx, dy) in visual coordinates."""
        self._check_transformable()
        new = self.rect + (dx, dy, dx, dy)
        with self._edit():
            self._apply(self.d._ru(self.page_no, new))

    def resize(self, new_rect):
        """Resize to new_rect (visual coordinates). Measurements are re-measured."""
        self._check_transformable(resize=True)
        m = self.measurement()
        if m and m[0] not in RESIZABLE_MEASURES:
            raise ValueError("a count marker has a fixed size; move it instead" if m[0] == "count"
                             else "this measurement cannot be resized; delete it and measure again")
        old = self.annot.rect
        if old.width == 0 or old.height == 0:
            raise ValueError("degenerate markup")
        with self._edit():
            self._apply(self.d._ru(self.page_no, new_rect))
            if m:
                self._remeasure(m[0])

    def _children_markups(self):
        return [Markup(c, self.page_no, self._page, self.d) for c in self._children()]

    def _rd(self):
        rd = _xget(self.d.doc, self.xref, "RD")
        return tuple(float(v) for v in rd.strip("[]").split()) if rd else (0.0, 0.0, 0.0, 0.0)

    def _label_center(self, kind):
        """Where the measurement label goes (visual coordinates)."""
        if self.kind == "Circle":
            vr = self.rect
            return ((vr.x0 + vr.x1) / 2, (vr.y0 + vr.y1) / 2)
        pts = self.points()
        if kind == "angle":
            return pts[1]
        if kind == "length" and len(pts) > 2:
            return pts[len(pts) // 2]
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    def _redraw_label(self, kind, val, unit):
        """Replace the label of this measurement. Does nothing if it has no label."""
        labels = [c for c in self._children() if c.info.get("subject") == "Label"]
        if not labels:
            return
        for c in labels:
            self._page.delete_annot(c)
        text = self.d.fmt(kind, val, unit)
        color = tuple(self.annot.colors.get("stroke") or (0, 0.4, 1))   # the label has the colour of its markup
        self.d._label(self.page_no, self._label_center(kind), "Ø " + text if kind == "diameter" else text, color, self)

    def _relabel(self, kind):
        """Redraw the label from the stored value. The value itself is never changed."""
        k, val, unit = self.measurement()
        self._redraw_label(k, val, unit)

    def _remeasure(self, kind):
        """Compute the value again from the geometry (after a resize), with the scale in force at its points, store it
        with the unit of that scale, and redraw the label. A measurement made in Revu or Acrobat (no OR_Measure) stores
        nothing: its value is always computed from its geometry and its own /Measure, as Revu does."""
        d = self.d
        if not _xget(d.doc, self.xref, MEASURE_KEY):
            return
        if self.kind == "Circle":
            l, t, r_, b_ = self._rd()
            vr = self.rect
            w, h = vr.width - l - r_, vr.height - t - b_
            sc = d.scale_at(self.page_no, [vr.tl, vr.br])
            val = sc.unit_per_pt * w if kind == "diameter" else 3.141592653589793 / 4 * w * h * sc.unit_per_pt ** 2
        else:
            pts = self.points()
            sc = d.scale_at(self.page_no, pts)
            if kind == "length":
                val = sc.length(pts)
            elif kind == "perimeter":
                val = sc.length(pts + pts[:1])
            elif kind == "angle":
                val = angle_deg(*pts)
            else:
                cut = sum(sc.area(c.points()) for c in self._children_markups() if c.subject == "Cutout")
                val = sc.area(pts) - cut
                if kind == "volume":
                    val *= float(_xget(d.doc, self.xref, DEPTH_KEY) or 1)
        unit = "deg" if kind == "angle" else sc.unit
        d.doc.xref_set_key(self.xref, MEASURE_KEY, f"({kind}:{val:.10g}:{unit})")
        if pdfmeasure.intent(d.doc, self.xref, self.kind):   # so Revu measures it at the same scale
            d.doc.xref_set_key(self.xref, "Measure", pdfmeasure.measure_source(sc.unit, sc.unit_per_pt))
        self._redraw_label(kind, val, unit)

    def _apply(self, new_u: fitz.Rect, children=True):
        """Map the annotation's current (API-space) bbox onto new_u, scaling vertices accordingly."""
        a, page, doc = self.annot, self._page, self.d.doc
        old, outer_old = a.rect, a.rect
        if self.kind in TEXT_MARKUP_TYPES:
            rawq = _xget(doc, self.xref, "QuadPoints")
            nums = [float(v) for v in rawq.strip("[]").split()]
            ddx, ddy = new_u.x0 - old.x0, -(new_u.y0 - old.y0)  # PDF y axis points up
            moved = [v + (ddx if i % 2 == 0 else ddy) for i, v in enumerate(nums)]
            doc.xref_set_key(self.xref, "QuadPoints", "[" + " ".join(f"{v:g}" for v in moved) + "]")
            a.update()
            return
        if self.kind in VERTEX_TYPES:
            # a.rect is the vertex bbox plus stroke padding: scale the vertices' own bbox, keep the padding
            pts = [p for st in a.vertices for p in st] if self.kind == "Ink" else a.vertices
            vb = fitz.Rect(min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
            pad = (vb.x0 - old.x0, vb.y0 - old.y0, old.x1 - vb.x1, old.y1 - vb.y1)
            new_vb = fitz.Rect(new_u.x0 + pad[0], new_u.y0 + pad[1], new_u.x1 - pad[2], new_u.y1 - pad[3])
            old, new_u = vb, new_vb
        sx = new_u.width / old.width if old.width else 1.0
        sy = new_u.height / old.height if old.height else 1.0
        f = lambda p: (new_u.x0 + (p[0] - old.x0) * sx, new_u.y0 + (p[1] - old.y0) * sy)  # noqa: E731
        raw = lambda pts: " ".join(f"{x + page.cropbox.x0:g} {page.mediabox.height - (y + page.cropbox.y0):g}"  # noqa: E731
                                   for x, y in pts)
        if self.kind in VERTEX_TYPES:
            self._write_vertices(f)
        else:
            if self.kind in ("Square", "Circle"):  # annot.rect includes the /RD border padding; set_rect wants the inner rect
                l, t, r_, b_ = self._rd()
                new_u = fitz.Rect(new_u.x0 + l, new_u.y0 + t, new_u.x1 - r_, new_u.y1 - b_)
            a.set_rect(new_u)
        a.update()
        if children:
            dxu, dyu = new_u.x0 - old.x0, new_u.y0 - old.y0  # translation is identical for padded and inner boxes
            for c in self._children():
                m = Markup(c, self.page_no, page, self.d)
                if m.kind == "Polygon" and m.subject == "Cutout":
                    m._write_vertices(f)          # a cut-out follows its parent: same move and same scaling
                elif (sx, sy) == (1.0, 1.0):
                    m._apply(fitz.Rect(c.rect.x0 + dxu, c.rect.y0 + dyu, c.rect.x1 + dxu, c.rect.y1 + dyu), children=False)

    def _write_vertices(self, f):
        """Rewrite the geometry of a line, polyline, polygon, or pen markup with every point mapped through f
        (f works in the annotation API space; see Document._tu)."""
        a, page, doc = self.annot, self._page, self.d.doc
        raw = lambda pts: " ".join(f"{x + page.cropbox.x0:g} {page.mediabox.height - (y + page.cropbox.y0):g}"  # noqa: E731
                                   for x, y in pts)
        v = a.vertices
        if self.kind == "Ink":
            doc.xref_set_key(self.xref, "InkList", "[" + "".join("[" + raw([f(p) for p in st]) + "]" for st in v) + "]")
        elif self.kind == "Line":
            doc.xref_set_key(self.xref, "L", "[" + raw([f(p) for p in v]) + "]")
        else:
            doc.xref_set_key(self.xref, "Vertices", "[" + raw([f(p) for p in v]) + "]")
        a.update()

    @property
    def can_copy(self) -> bool:
        """True if this markup can be copied or saved as a tool (stamps, text markups and measurements cannot)."""
        return self.kind in RECREATABLE_KINDS and not self.measurement()

    def to_dict(self):
        """Serialisable description (for tool chest / copy-paste)."""
        a = self.annot
        c = a.colors
        pts = self.points()
        return {
            "kind": self.kind, "subject": self.subject, "comment": self.comment,
            "stroke": c.get("stroke"), "fill": c.get("fill"), "width": a.border.get("width", 1),
            "opacity": a.opacity, "rect": list(self.rect),
            "vertices": [[list(p) for p in st] for st in pts] if self.kind == "Ink"
            else ([list(p) for p in pts] if pts else None),
            "arrow": bool(a.line_ends and a.line_ends[1] == fitz.PDF_ANNOT_LE_CLOSED_ARROW)
            if self.kind == "Line" else False,
        }


class FileChangedError(OSError):
    """An in-place save found that another program changed the file since this document opened or last saved it."""


class Document(SheetOps, PageOps):

    def __init__(self, path: str | None = None, password: str | None = None):
        # Stat before reading: a change between the two then shows as a change at save, never goes unnoticed.
        self._disk = _disk_state(path)
        data = self._read(path) if path else None
        self.doc = fitz.open("pdf", data) if path else fitz.open()
        encrypted = bool(self.doc.needs_pass)
        p = self.doc.xref_get_key(-1, "Encrypt/P")
        # The file's own permission bits, re-applied on save; None if the file is not encrypted.
        self._permissions = int(p[1]) if p[0] == "int" else None
        auth = self.doc.authenticate(password) if password and self._permissions is not None else 0
        if encrypted and not auth:
            raise PermissionError("password required or incorrect")
        self.path = path
        self._password = password if encrypted else None  # re-applied on save so protection is never silently dropped
        is_owner = bool(auth & 4)                # authenticate() sets bit 4 for the owner password
        # What this user may do: everything for an unencrypted file or the owner, else what the file allows.
        self._allowed = -1 if self._permissions is None or is_owner else self._permissions
        self._owner_pw = password if is_owner else secrets.token_urlsafe(24)   # used only if the file is encrypted
        self.save_as_required = self.restricted   # for UIs: do not overwrite a restricted file as issued
        self.author = getpass.getuser()
        self._depth = 0
        self.generation = 0
        self._pcache: dict[int, fitz.Page] = {}
        self.revision = 0            # counts changes, so caches know when to rebuild
        self._link_doc, self._link_rev = None, -1
        self.viewports: list = []
        self.unit_style, self.fraction = "decimal", 16
        self.columns: list = []   # custom Markups List columns: {"name", "type", "choices"}
        self._joined: set[int] = set()   # journal steps that undo together with the step before them
        self._merge = None               # (key, journal position, time) of the last step that a next one may join
        self.modified = False
        self.default_scale, self.page_scales = self._load_scales()
        # A full rewrite (all save() can do) invalidates digital signatures, so callers warn before saving over the
        # file. Set once at open: the signatures in the file on disk are what a save would break.
        self.signed = data is not None and self._has_signatures(data)

    def _has_signatures(self, data: bytes) -> bool:
        """True if a signature field holds a signature whose signed bytes are still those of the file `data`.
        A rewrite (any save that is not incremental) moves every object, so it leaves the /V values but breaks them."""
        # Walk the AcroForm field tree by xref, without loading pages: a signature field (/FT inherits down /Kids)
        # with a value is signed.
        d = self.doc
        todo = [(x, None) for x in _refs(d, *d.xref_get_key(d.pdf_catalog(), "AcroForm/Fields"))]
        seen = set()
        while todo:
            xref, ft = todo.pop()
            if xref in seen:   # a malformed tree can loop
                continue
            seen.add(xref)
            ft = _xget(d, xref, "FT") or ft
            if ft == "/Sig" and _xget(d, xref, "V") and _byte_range_intact(d.xref_get_key(xref, "V/ByteRange")[1], data):
                return True
            todo += [(k, ft) for k in _refs(d, *d.xref_get_key(xref, "Kids"))]
        return False

    @staticmethod
    def _read(path: str) -> bytes:
        """The file's bytes. The document is opened from memory so the file is never locked (Windows refuses to
        replace or overwrite open files)."""
        with open(path, "rb") as f:
            return f.read()

    @staticmethod
    def _open(path: str) -> fitz.Document:
        return fitz.open("pdf", Document._read(path))

    def _page(self, pno: int) -> fitz.Page:
        """Page object kept alive (annotations returned by add_* are only valid while their page is)."""
        pg = self._pcache.get(pno)
        if pg is None:
            pg = self._pcache[pno] = self.doc[pno]
        return pg

    def _invalidate_pages(self):
        """Call after anything that renumbers/replaces pages or reopens the document."""
        self._pcache = {}
        self.revision += 1
        self.generation += 1
        self._vp_state = None    # pages may be new (without a /VP) or moved: check every /VP at the next sync

    # --- coordinate spaces ---
    # PyMuPDF annotation/text APIs use unrotated, cropbox-origin, y-down coordinates; the UI and the public
    # OpenRevu API use *visual* coordinates (what is drawn). These convert at the boundary.
    def _tu(self, pno, p):
        return fitz.Point(p) * self._page(pno).derotation_matrix

    def _tv(self, pno, p):
        return fitz.Point(p) * self._page(pno).rotation_matrix

    def _tus(self, pno, pts):
        return [tuple(self._tu(pno, p)) for p in pts]

    def _ru(self, pno, r):
        return fitz.Rect(r).normalize() * self._page(pno).derotation_matrix

    def _rv(self, pno, r):
        return fitz.Rect(r) * self._page(pno).rotation_matrix

    # --- undo / redo ---
    MERGE_SECONDS = 2.0      # merged steps (spin box clicks): how long after one change the next may still join it
    # MuPDF's journal records the old version of every object an operation changes, so an undo step costs memory in
    # proportion to the change, not to the file. The journal starts with the first operation; after that, every change
    # to self.doc must happen inside _operation, because MuPDF refuses changes outside one. Copy pages out of the
    # document from _copy(), not from self.doc (see _journal.py).

    @contextlib.contextmanager
    def _operation(self, name="edit", merge=None):
        """One undo step. Nested operations are part of the outer one. If the body raises, the document goes back to
        exactly how it was, and markups fetched before are stale. A step with a `merge` key joins the step before it
        when that step had the same key, came less than MERGE_SECONDS earlier, and nothing happened in between (one
        undo then undoes both)."""
        if self._depth:
            yield
            return
        before, modified = self.doc.journal_position(), self.modified
        self.revision += 1
        self._depth = 1
        try:
            with _journal.operation(self.doc, name):
                yield
                self._store_scales()
        except BaseException:
            self._journal_moved()
            self.modified = modified
            raise
        finally:
            self._depth = 0
        self.modified = True
        after = self.doc.journal_position()
        if after != before:                                  # the operation changed something: it is a new step
            self._joined = {s for s in self._joined if s <= before[0]}     # MuPDF dropped the steps after `before`
            now = time.monotonic()
            if merge is not None and self._merge and self._merge[:2] == (merge, before) \
                    and now - self._merge[2] < self.MERGE_SECONDS:
                self._joined.add(after[0])
            self._merge = (merge, after, now) if merge is not None else None

    def _journal_moved(self):
        """After the journal undid or redid changes: page objects, PyMuPDF's metadata cache and the settings read
        from the metadata are out of date."""
        self._invalidate_pages()
        self.doc.init_doc()
        self.default_scale, self.page_scales = self._load_scales()
        self._merge = None

    @property
    def undo_steps(self) -> int:
        """How many times undo() can go back."""
        pos = self.doc.journal_position()[0]
        return pos - sum(1 for s in self._joined if s <= pos)

    @property
    def redo_steps(self) -> int:
        """How many times redo() can go forward."""
        pos, total = self.doc.journal_position()
        return total - pos - sum(1 for s in self._joined if s > pos)

    def undo(self) -> bool:
        pos = self.doc.journal_position()[0]
        if not pos:
            return False
        self.doc.journal_undo()
        while pos in self._joined:          # this step was joined to the one before it
            self.doc.journal_undo()
            pos -= 1
        self._journal_moved()
        self.modified = True
        return True

    def redo(self) -> bool:
        pos, total = self.doc.journal_position()
        if pos == total:
            return False
        self.doc.journal_redo()
        while pos + 2 in self._joined:      # the next step is joined to the one just redone
            self.doc.journal_redo()
            pos += 1
        self._journal_moved()
        self.modified = True
        return True

    def _copy(self) -> fitz.Document:
        """A copy of the document to copy pages from (see _journal.py, item 4). Close it after use, for example with
        `with self._copy() as copy:`."""
        return _journal.copy(self.doc)

    def _snapshot(self) -> bytes:
        self._store_scales()
        return self._dump()

    @property
    def restricted(self) -> bool:
        """True if the PDF forbids markups or changes and was not opened with its owner password."""
        need = fitz.PDF_PERM_ANNOTATE | fitz.PDF_PERM_MODIFY
        return self._allowed & need != need

    def _encryption_kwargs(self) -> dict:
        """Arguments that protect a file written from this document the way the opened file was protected: the same
        user password and permissions, and the owner password if the user gave it (else a random one).
        Every file that OpenRevu derives from a protected document (copies, extracts, reports) uses them."""
        if self._permissions is None:
            return {}
        return dict(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=self._password or "", owner_pw=self._owner_pw,
                    permissions=self._permissions)

    def _dump(self) -> bytes:
        """Serialize for the undo history and autosave. Protected documents never leave plaintext copies in memory,
        and a snapshot of an encrypted file stays encrypted so a recovered copy keeps its permissions."""
        return self.doc.tobytes(**self._encryption_kwargs())

    # --- scales (default + per page), persisted in PDF keywords ---
    def _stored_settings(self) -> dict:
        """The settings blob in the keywords, parsed; {} if there is none or it is damaged. "legacy" holds the page
        scales, viewports and /VP fingerprints of a file saved before OpenRevu wrote /VP (see _import_legacy_viewports)."""
        kw = (self.doc.metadata or {}).get("keywords") or ""
        part = next((p for p in kw.split(";") if p.startswith(TAG + "-scales=")), None)
        if part is None:
            return {}
        try:
            d = json.loads(part.split("=", 1)[1])
            out = {"default": Scale.from_dict(d["default"]), "style": d.get("style", "decimal"),
                   "fraction": int(d.get("fraction", 16)), "columns": list(d.get("columns", []))}
            if "pages" in d:
                out["legacy"] = ({int(k): Scale.from_dict(v) for k, v in d["pages"].items()},
                                 [(int(v["page"]), fitz.Rect(v["rect"]), Scale.from_dict(v["scale"]))
                                  for v in d.get("viewports", [])],
                                 set(d["vp"]) if isinstance(d.get("vp"), list) else None)
            return out
        except (ValueError, KeyError, TypeError, AttributeError):
            return {}

    def _load_scales(self):
        """Read the document settings. The page scales and viewports come from each page's /VP (written by OpenRevu,
        Revu or Acrobat); the settings blob in the keywords holds the default scale, unit style and custom columns."""
        st = self._stored_settings()      # undo can go back to before the settings were first stored
        default = st.get("default", Scale())
        self.unit_style, self.fraction, self.columns = st.get("style", "decimal"), st.get("fraction", 16), st.get("columns", [])
        legacy = st.get("legacy")
        pages, self.viewports, seen = legacy if legacy else ({}, [], None)
        self._vp_file = {}   # page xref -> the /VP viewports in the file, as pdfmeasure.page_viewports returns them
        for pno in range(len(self.doc)):
            vps = self._vp_file[self.doc.page_xref(pno)] = pdfmeasure.page_viewports(self.doc, pno)
            if not vps:
                continue
            if legacy:
                self._import_legacy_viewports(pno, vps, pages, seen)
                continue
            page_scale, marked, rects = self._pdf_viewports(pno, vps)
            if page_scale and not (marked and _same_scale(page_scale, default)):
                pages[pno] = page_scale
            self.viewports += [(pno, r, sc) for r, sc in rects]
        self._vp_state = None   # the scales at the last _sync_viewports
        return default, pages

    def _import_legacy_viewports(self, pno: int, vps, pages: dict, seen):
        """Merge the /VP viewports of one page into settings saved before OpenRevu wrote /VP itself. Those settings keep
        a fingerprint of each /VP they had taken in (`seen`): a known /VP is skipped, so a scale the user changed in
        OpenRevu stays. A /VP that is new or changed (calibrated in Revu after OpenRevu saved the file) wins. For
        settings older than the fingerprints (`seen` is None), the stored settings win."""
        if seen is not None and _fingerprint(vps) in seen:
            return
        newer = seen is not None
        page_scale, _, rects = self._pdf_viewports(pno, vps)
        if page_scale and (newer or pno not in pages):
            pages[pno] = page_scale
        for r, sc in rects:
            i = next((i for i, (p, r2, _) in enumerate(self.viewports) if p == pno and _same_rect(r, r2)), None)
            if i is None:
                self.viewports.append((pno, r, sc))
            elif newer:
                self.viewports[i] = (pno, r, sc)

    def _pdf_viewports(self, pno: int, vps):
        """(page scale or None, whether it is marked as the default scale, [(visual rect, scale)]) of the /VP
        viewports `vps` of one page."""
        page_scale, marked, rects = None, False, []
        page = self._page(pno)
        for bbox, m, default in vps:
            sc = _pdf_scale(m)
            if sc is None:
                continue
            r = self._visual_rect(pno, bbox)
            # Revu marks the page scale with a viewport the size of the visual page at the origin
            if r.contains(page.rect) or _same_rect(bbox, page.rect):
                if page_scale is None:
                    page_scale, marked = sc, default
            else:
                rects.append((r, sc))
        return page_scale, marked, rects

    # PDF user space (y up) <-> PyMuPDF unrotated space (y down, CropBox origin) <-> visual space
    def _visual_rect(self, pno: int, bbox) -> fitz.Rect:
        """A rectangle in PDF user space, in visual page coordinates."""
        page = self._page(pno)
        cb, mh = page.cropbox, page.mediabox.height
        u = fitz.Rect(bbox.x0 - cb.x0, mh - bbox.y1 - cb.y0, bbox.x1 - cb.x0, mh - bbox.y0 - cb.y0)
        return self._rv(pno, u).normalize()

    def _pdf_rect(self, pno: int, r) -> fitz.Rect:
        """A rectangle in visual page coordinates, in PDF user space (the inverse of _visual_rect)."""
        page = self._page(pno)
        cb, mh = page.cropbox, page.mediabox.height
        u = self._ru(pno, r)
        return fitz.Rect(u.x0 + cb.x0, mh - u.y1 - cb.y0, u.x1 + cb.x0, mh - u.y0 - cb.y0)

    def _wanted_viewports(self, pno: int, rects):
        """(page viewport or None, detail viewports) that the /VP of page `pno` should hold, each viewport as
        pdfmeasure.page_viewports returns it. `rects` are the [(visual rect, scale)] viewports on the page. The page
        scale is a viewport the size of the visual page at the origin, as Revu writes it. A page on the default scale
        gets one too, marked as the default, unless the default was never calibrated."""
        sc, default = self.page_scales.get(pno), False
        if sc is None and self.default_scale != Scale():
            sc, default = self.default_scale, True
        page = (fitz.Rect(self._page(pno).rect), (sc.unit, sc.unit_per_pt), default) if sc else None
        return page, [(self._pdf_rect(pno, r), (vs.unit, vs.unit_per_pt), False) for r, vs in rects]

    def _rects_by_page(self) -> dict:
        out: dict = {}
        for p, r, sc in self.viewports:
            out.setdefault(p, []).append((r, sc))
        return out

    def _page_geometry_changed(self):
        """Call after rotating or cropping a page: the page-scale /VP has the size of the visual page."""
        self._vp_state = None

    def _sync_viewports(self):
        """Rewrite the /VP of every page whose scales differ from what its /VP says. A /VP that already says the same
        is left as it is, so the /VP that Revu wrote stays untouched until the user changes a scale on that page.
        Skipped while the scales and the number of pages are as they were at the last sync; anything else that changes
        what a page's /VP should say (page geometry, replacing pages, undo) clears _vp_state."""
        state = repr((self.default_scale, sorted(self.page_scales.items()), self.viewports, len(self.doc)))
        if state == self._vp_state:
            return
        rects = self._rects_by_page()
        for pno in range(len(self.doc)):
            page, details = self._wanted_viewports(pno, rects.get(pno, []))
            want, xref = ([page] if page else []) + details, self.doc.page_xref(pno)
            have = self._vp_file.get(xref)
            if have is None:
                have = pdfmeasure.page_viewports(self.doc, pno)
            if not _same_viewports(want, have):
                pdfmeasure.set_page_viewports(self.doc, pno, page, details)
                have = want
            self._vp_file[xref] = have
        self._vp_state = state

    def _settings_blob(self) -> dict:
        """The document settings stored in the keywords. Page scales and viewports are in each page's /VP instead."""
        return {"default": self.default_scale.to_dict(), "style": self.unit_style, "fraction": self.fraction,
                "columns": self.columns}

    def _write_settings(self, target: fitz.Document, pages=None):
        """Store the settings in `target`: this document, or a new file made from some of its pages (`pages`, the
        indexes of the pages that became pages 0, 1, 2... of `target`)."""
        md = dict(target.metadata or {})
        kw = [p for p in (md.get("keywords") or "").split(";") if p and not p.startswith(TAG + "-scale")]
        kw.append(f"{TAG}-scales={json.dumps(self._settings_blob())}")
        md["keywords"] = ";".join(kw)
        target.set_metadata(md)
        rects = self._rects_by_page()
        for i, p in enumerate(pages or []):
            pdfmeasure.set_page_viewports(target, i, *self._wanted_viewports(p, rects.get(p, [])))

    def _store_scales(self):
        """Write the settings into the document's keywords. Every operation ends with this, so undo restores them with
        the rest, and outside an operation they are already stored."""
        if not self._depth:
            return
        self._sync_viewports()
        keywords = (self.doc.metadata or {}).get("keywords") or ""
        stored = [p for p in keywords.split(";") if p.startswith(TAG + "-scale")]
        if stored != [f"{TAG}-scales={json.dumps(self._settings_blob())}"]:   # an edit that changes nothing adds no step
            self._write_settings(self.doc)

    @property
    def scale(self) -> Scale:
        return self.default_scale

    def scale_for(self, pno: int) -> Scale:
        return self.page_scales.get(pno, self.default_scale)

    def fmt(self, kind: str, val: float, unit: str) -> str:
        """Format a measurement with this document's unit style."""
        return format_value(kind, val, unit, self.unit_style, self.fraction)

    @_mutates
    def set_unit_style(self, style: str = "decimal", fraction: int = 16) -> int:
        """Choose how lengths in feet or inches are shown. Existing labels are redrawn. Returns the number redrawn."""
        if style not in STYLES:
            raise ValueError(f"style must be one of {STYLES}")
        if fraction not in FRACTIONS:
            raise ValueError(f"the fraction must be one of {FRACTIONS}")
        self.unit_style, self.fraction = style, fraction
        n = 0
        for m in self.markups():
            r = m.measurement()
            if r and r[0] != "count" and any(c.info.get("subject") == "Label" for c in m._children()):
                m._relabel(r[0])          # only the text changes: a takeoff value is never recomputed by a display setting
                n += 1
        return n

    @_mutates
    def add_column(self, name: str, type: str = "text", choices=()):
        """Add a custom column to the Markups List (text, number, or choice)."""
        name = (name or "").strip()
        if not name:
            raise ValueError("the column needs a name")
        if name in BASE_COLUMNS or any(c["name"] == name for c in self.columns):
            raise ValueError(f"a column named {name!r} already exists")
        if type not in COLUMN_TYPES:
            raise ValueError(f"the column type must be one of {COLUMN_TYPES}")
        choices = [c.strip() for c in choices if c.strip()]
        if type == "choice" and not choices:
            raise ValueError("a choice column needs at least one choice")
        self.columns.append({"name": name, "type": type, "choices": choices if type == "choice" else []})

    @_mutates
    def remove_column(self, name: str):
        """Remove a custom column and the values stored in it."""
        if not any(c["name"] == name for c in self.columns):
            raise KeyError(name)
        self.columns = [c for c in self.columns if c["name"] != name]
        for m in self.markups():
            vals = m.custom()
            if name in vals:
                del vals[name]
                enc = base64.b64encode(json.dumps(vals, ensure_ascii=False).encode("utf-8")).decode("ascii")
                self.doc.xref_set_key(m.xref, CUSTOM_KEY, f"({enc})" if vals else "null")

    def column_totals(self, name: str) -> dict:
        """Sum of a number column, per subject: {subject: total}."""
        col = next((c for c in self.columns if c["name"] == name), None)
        if col is None or col["type"] != "number":
            raise ValueError(f"{name!r} is not a number column")
        out: dict = {}
        for m in self.markups():
            v = m.custom().get(name)
            if v is not None:
                out[m.subject] = out.get(m.subject, 0.0) + v
        return out

    def scale_at(self, pno: int, pts) -> Scale:
        """Scale that applies at the centroid of pts: the innermost-last viewport containing it, else the page scale."""
        pts = list(pts)
        c = fitz.Point(sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        for p, r, sc in reversed(self.viewports):
            if p == pno and r.contains(c):
                return sc
        return self.scale_for(pno)

    def add_viewport(self, pno: int, rect, scale: Scale):
        """Give a region of a page its own scale (e.g. a detail drawn at a different scale)."""
        self._check([pno])
        r = fitz.Rect(rect).normalize()
        if r.is_empty:
            raise ValueError("viewport must have an area")
        with self._operation("add viewport"):
            self.viewports.append((pno, r, scale))

    def remove_viewport(self, index: int):
        with self._operation("remove viewport"):
            del self.viewports[index]

    def set_scale(self, scale: Scale, page: int | None = None, pages=None, reset_pages: bool = False):
        """Set the default scale, or the scale of one page / a list of pages.
        reset_pages=True also clears every per-page override (apply to all pages)."""
        with self._operation("set scale"):
            if reset_pages:
                self.page_scales = {}
            if pages is not None or page is not None:
                for p in ([page] if pages is None else pages):
                    self.page_scales[p] = scale
            else:
                self.default_scale = scale

    @property
    def page_count(self) -> int:
        return len(self.doc)

    # --- markup creation ---
    def _finish(self, a, color, width, subject, fill=None, opacity=1.0, measure=None, content=""):
        a.set_colors(stroke=color, fill=fill)
        a.set_border(width=width)
        a.set_opacity(opacity)
        a.set_info(title=self.author, subject=subject, content=content)
        a.update()
        if measure:
            self.doc.xref_set_key(a.xref, MEASURE_KEY, f"({measure})")
        return a

    @_mutates
    def add_rect(self, pno, rect, color=(1, 0, 0), width=1.5, fill=None, opacity=1.0):
        return self._finish(self._page(pno).add_rect_annot(self._ru(pno, rect)), color, width, "Rectangle", fill=fill, opacity=opacity)

    @_mutates
    def add_ellipse(self, pno, rect, color=(1, 0, 0), width=1.5, fill=None, opacity=1.0):
        return self._finish(self._page(pno).add_circle_annot(self._ru(pno, rect)), color, width, "Ellipse", fill=fill, opacity=opacity)

    @_mutates
    def add_line(self, pno, p1, p2, color=(1, 0, 0), width=1.5, arrow=False):
        a = self._page(pno).add_line_annot(self._tu(pno, p1), self._tu(pno, p2))
        if arrow:
            a.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
        return self._finish(a, color, width, "Arrow" if arrow else "Line")

    @_mutates
    def add_polyline(self, pno, pts, color=(1, 0, 0), width=1.5):
        return self._finish(self._page(pno).add_polyline_annot(self._tus(pno, pts)), color, width, "Polyline")

    @_mutates
    def add_polygon(self, pno, pts, color=(1, 0, 0), width=1.5, fill=None, opacity=1.0):
        return self._finish(self._page(pno).add_polygon_annot(self._tus(pno, pts)), color, width, "Polygon", fill=fill, opacity=opacity)

    @_mutates
    def add_freehand(self, pno, pts, color=(1, 0, 0), width=2):
        return self._finish(self._page(pno).add_ink_annot([self._tus(pno, pts)]), color, width, "Pen")

    @_mutates
    def add_cloud(self, pno, rect, color=(1, 0, 0), width=1.5):
        return self._finish(self._page(pno).add_polyline_annot(self._tus(pno, cloud_points(rect))), color, width, "Cloud")

    def _markup_text(self, pno, rect, kind, color):
        page, ru = self._page(pno), self._ru(pno, rect)
        words = [w for w in page.get_text("words") if fitz.Rect(w[:4]).intersects(ru)]
        quads = [fitz.Rect(w[:4]).quad for w in words] or [ru.quad]
        a = getattr(page, f"add_{kind}_annot")(quads=quads)
        a.set_colors(stroke=color)
        a.set_info(title=self.author, subject=kind.title())
        a.update()
        return a

    @_mutates
    def add_highlight(self, pno, rect, color=(1, 1, 0)):
        return self._markup_text(pno, rect, "highlight", color)

    @_mutates
    def add_underline(self, pno, rect, color=(0, 0.6, 0)):
        return self._markup_text(pno, rect, "underline", color)

    @_mutates
    def add_strikeout(self, pno, rect, color=(1, 0, 0)):
        return self._markup_text(pno, rect, "strikeout", color)

    @_mutates
    def add_squiggly(self, pno, rect, color=(1, 0, 0)):
        return self._markup_text(pno, rect, "squiggly", color)

    @_mutates
    def add_text(self, pno, rect, text, color=(1, 0, 0), fontsize=12, fill=(1, 1, 1)):
        a = self._page(pno).add_freetext_annot(self._ru(pno, rect), text, fontsize=fontsize, text_color=color, fill_color=fill)
        a.set_info(title=self.author, subject="Text")
        a.update()
        return a

    @_mutates
    def add_callout(self, pno, rect, text, tip, color=(1, 0, 0), fontsize=11):
        """Text box with a leader line to `tip` (a callout)."""
        a = self._page(pno).add_freetext_annot(self._ru(pno, rect), text, fontsize=fontsize, text_color=color, fill_color=(1, 1, 1))
        a.set_info(title=self.author, subject="Callout")
        a.update()
        r = fitz.Rect(rect)
        c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        self.add_line(pno, c, tip, color, 1.0, arrow=True)
        return a

    @_mutates
    def add_note(self, pno, point, text):
        a = self._page(pno).add_text_annot(self._tu(pno, point), text)
        a.set_info(title=self.author, subject="Note", content=text)
        a.update()
        return a

    @_mutates
    def add_reply(self, markup: Markup, text):
        pg = markup._page
        r = markup.annot.rect
        a = pg.add_text_annot((r.x1, r.y0), text)
        a.set_info(title=self.author, subject="Reply", content=text)
        a.update()
        self.doc.xref_set_key(a.xref, "IRT", f"{markup.xref} 0 R")
        return a

    @_mutates
    def add_stamp(self, pno, rect, text="APPROVED", color=(0, 0.5, 0), date=True):
        """Custom text stamp rendered to an image (name + date line like Revu dynamic stamps)."""
        r = fitz.Rect(rect)
        lines = [text] + ([f"{self.author}  {_today()}"] if date else [])
        src = fitz.open()
        w, h = 400, 120 if date else 80
        sp = src.new_page(width=w, height=h)
        sp.draw_rect(fitz.Rect(3, 3, w - 3, h - 3), color=color, width=4)
        sp.insert_textbox(fitz.Rect(8, 6, w - 8, h * (0.62 if date else 1)), lines[0],
                          fontsize=38 if date else 40, fontname="hebo", color=color, align=1)
        if date:
            sp.insert_textbox(fitz.Rect(8, h * 0.66, w - 8, h - 4), lines[1], fontsize=18, color=color, align=1)
        sp.set_rotation((360 - self._page(pno).rotation) % 360)  # pre-rotate so it displays upright on rotated pages
        a = self._page(pno).add_stamp_annot(self._ru(pno, r), stamp=sp.get_pixmap(dpi=144))
        a.set_info(title=self.author, subject="Stamp", content=text)
        a.update()
        return a

    @_mutates
    def add_image_stamp(self, pno, rect, image_path, subject="Image"):
        a = self._page(pno).add_stamp_annot(self._ru(pno, rect), stamp=image_path)
        a.set_info(title=self.author, subject=subject)
        a.update()
        return a

    @_mutates
    def add_from_dict(self, pno, d, offset=(0, 0)):
        """Recreate a markup from Markup.to_dict() at an offset (paste / tool chest)."""
        dx, dy = offset
        k, page = d["kind"], self._page(pno)
        col = tuple(d["stroke"]) if d.get("stroke") else None
        fill = tuple(d["fill"]) if d.get("fill") else None
        r = self._ru(pno, fitz.Rect(d["rect"]) + (dx, dy, dx, dy))
        sh = lambda pts: self._tus(pno, [(p[0] + dx, p[1] + dy) for p in pts])  # noqa: E731
        if k == "Square":
            a = page.add_rect_annot(r)
        elif k == "Circle":
            a = page.add_circle_annot(r)
        elif k == "Line":
            a = page.add_line_annot(*sh(d["vertices"]))
            if d.get("arrow"):
                a.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
        elif k == "PolyLine":
            a = page.add_polyline_annot(sh(d["vertices"]))
        elif k == "Polygon":
            a = page.add_polygon_annot(sh(d["vertices"]))
        elif k == "Ink":
            a = page.add_ink_annot([sh(st) for st in d["vertices"]])
        elif k == "FreeText":
            a = page.add_freetext_annot(r, d["comment"], fontsize=12, text_color=col or (1, 0, 0), fill_color=fill or (1, 1, 1))
        elif k == "Text":
            a = page.add_text_annot(r.tl, d["comment"])
        else:
            raise ValueError(f"cannot recreate {k} markups")
        if k not in ("FreeText", "Text"):
            a.set_colors(stroke=col, fill=fill)
            a.set_border(width=d.get("width", 1))
            a.set_opacity(d.get("opacity", 1))
        a.set_info(title=self.author, subject=d.get("subject", ""), content=d.get("comment", ""))
        a.update()
        return a

    # --- measurements (takeoff) ---
    def _label(self, pno, center, text, color, parent):
        w = 6.2 * len(text) + 8
        r = fitz.Rect(center[0] - w / 2, center[1] - 8, center[0] + w / 2, center[1] + 8)
        a = self._page(pno).add_freetext_annot(self._ru(pno, r), text, fontsize=10, text_color=color, fill_color=(1, 1, 1))
        a.set_info(title=self.author, subject="Label")
        a.update()
        self.doc.xref_set_key(a.xref, PARENT_KEY, str(parent.xref))

    @staticmethod
    def _centroid(pts):
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    @_mutates
    def add_length(self, pno, pts, color=(0, 0.4, 1), label=True, subject="Length"):
        pts = [tuple(p) for p in pts]
        sc = self.scale_at(pno, pts)
        v = sc.length(pts)
        pg = self._page(pno)
        up = self._tus(pno, pts)
        a = pg.add_polyline_annot(up) if len(pts) > 2 else pg.add_line_annot(*up)
        self._finish(a, color, 1.5, subject, measure=f"length:{v:.10g}:{sc.unit}")
        pdfmeasure.mark_measurement(self.doc, a.xref, a.type[1], "length", sc.unit, sc.unit_per_pt)
        if label:
            mid = pts[len(pts) // 2] if len(pts) > 2 else self._centroid(pts)
            self._label(pno, mid, self.fmt("length", v, sc.unit), color, a)
        return a, v

    @_mutates
    def add_perimeter(self, pno, pts, color=(0, 0.4, 1), label=True, subject="Perimeter"):
        pts = [tuple(p) for p in pts]
        sc = self.scale_at(pno, pts)
        v = sc.length(pts + pts[:1])
        a = self._page(pno).add_polygon_annot(self._tus(pno, pts))
        self._finish(a, color, 1.5, subject, measure=f"perimeter:{v:.10g}:{sc.unit}")
        if label:
            self._label(pno, self._centroid(pts), self.fmt("perimeter", v, sc.unit), color, a)
        return a, v

    @_mutates
    def add_area(self, pno, pts, color=(0, 0.6, 0.2), label=True, subject="Area", cutouts=(), depth=None):
        """Polygon area minus cutouts. If depth is given (real units) a volume is recorded instead."""
        pts = [tuple(p) for p in pts]
        sc = self.scale_at(pno, pts)
        v = sc.area(pts) - sum(sc.area(c) for c in cutouts)
        if v < 0:
            raise ValueError("cutouts exceed the area")
        kind, unit, txt = "area", sc.unit, f"{v:.2f} {sc.unit}²"
        if depth is not None:
            if depth <= 0:
                raise ValueError("depth must be positive")
            v, kind, txt = v * depth, "volume", f"{v * depth:.2f} {sc.unit}³"
        a = self._page(pno).add_polygon_annot(self._tus(pno, pts))
        self._finish(a, color, 1.5, subject, fill=color, opacity=0.25, measure=f"{kind}:{v:.10g}:{unit}")
        if depth is not None:
            self.doc.xref_set_key(a.xref, DEPTH_KEY, repr(float(depth)))
        elif not cutouts:   # Revu would show the polygon's whole area, without the cut-outs: keep those OpenRevu-only
            pdfmeasure.mark_measurement(self.doc, a.xref, "Polygon", "area", sc.unit, sc.unit_per_pt)
        for c in cutouts:
            ca = self._page(pno).add_polygon_annot(self._tus(pno, c))
            self._finish(ca, (0.5, 0.5, 0.5), 1, "Cutout", fill=(1, 1, 1), opacity=0.8)
            self.doc.xref_set_key(ca.xref, PARENT_KEY, str(a.xref))
        if label:
            self._label(pno, self._centroid(pts), txt, color, a)
        return a, v

    def region_polygon(self, pno, point, max_px=4000, threshold=200):
        """Dynamic-Fill-style: boundary (and obstacles) of the enclosed open area around `point`.
        Returns (outer_polygon, [hole_polygons]) in visual page coordinates. Needs numpy + scipy."""
        try:
            import numpy as np
            import scipy.ndimage  # noqa: F401  (checked here so a missing scipy becomes a clear error)
            from .fill import find_region
        except ImportError as e:
            raise RuntimeError("region fill needs numpy and scipy: pip install openrevu[fill]") from e
        pg = self._page(pno)
        z = min(4.0, max_px / max(pg.rect.width, pg.rect.height))
        pix = pg.get_pixmap(matrix=fitz.Matrix(z, z), colorspace=fitz.csGRAY, annots=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        outer, holes = find_region(arr > threshold, (int(point[0] * z), int(point[1] * z)), min_hole=max(30, int(z * z * 4)),
                                   tol=max(1.0, z * 0.5))
        conv = lambda ring: [((x + 0.5) / z, (y + 0.5) / z) for x, y in ring]  # noqa: E731
        return conv(outer), [conv(h) for h in holes]

    @_mutates
    def add_fill_area(self, pno, point, **kw):
        """Measure the room around `point`: area of the enclosed region minus obstacles (columns etc.)."""
        outer, holes = self.region_polygon(pno, point)
        return self.add_area(pno, outer, cutouts=holes, subject=kw.pop("subject", "Fill Area"), **kw)

    @_mutates
    def add_rect_area(self, pno, rect, **kw):
        r = fitz.Rect(rect).normalize()
        return self.add_area(pno, [tuple(r.tl), tuple(r.tr), tuple(r.br), tuple(r.bl)], **kw)

    @_mutates
    def add_ellipse_area(self, pno, rect, color=(0, 0.6, 0.2), label=True, subject="Area"):
        r = fitz.Rect(rect).normalize()
        sc = self.scale_at(pno, [tuple(r.tl), tuple(r.br)])
        v = math.pi * (r.width / 2) * (r.height / 2) * sc.unit_per_pt ** 2
        a = self._page(pno).add_circle_annot(self._ru(pno, r))
        self._finish(a, color, 1.5, subject, fill=color, opacity=0.25, measure=f"area:{v:.10g}:{sc.unit}")
        if label:
            self._label(pno, self._centroid([tuple(r.tl), tuple(r.br)]), f"{v:.2f} {sc.unit}²", color, a)
        return a, v

    @_mutates
    def add_diameter(self, pno, p1, p2, color=(0, 0.4, 1), label=True, subject="Diameter"):
        """Circle through the diameter p1-p2; records the diameter length."""
        c = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
        rad = math.dist(p1, p2) / 2
        sc = self.scale_at(pno, [p1, p2])
        v = math.dist(p1, p2) * sc.unit_per_pt
        a = self._page(pno).add_circle_annot(self._ru(pno, fitz.Rect(c[0] - rad, c[1] - rad, c[0] + rad, c[1] + rad)))
        self._finish(a, color, 1.5, subject, measure=f"diameter:{v:.10g}:{sc.unit}")
        if label:
            self._label(pno, c, "Ø " + self.fmt("diameter", v, sc.unit), color, a)
        return a, v

    @_mutates
    def add_angle(self, pno, a_, b_, c_, color=(0, 0.4, 1), label=True, subject="Angle"):
        deg = angle_deg(a_, b_, c_)
        a = self._page(pno).add_polyline_annot(self._tus(pno, [a_, b_, c_]))
        self._finish(a, color, 1.5, subject, measure=f"angle:{deg:.10g}:deg")
        if label:
            self._label(pno, b_, f"{deg:.1f}°", color, a)
        return a, deg

    @_mutates
    def add_count(self, pno, point, color=(0.8, 0, 0.8), group="Count"):
        r = fitz.Rect(point[0] - 5, point[1] - 5, point[0] + 5, point[1] + 5)
        a = self._page(pno).add_circle_annot(self._ru(pno, r))
        self._finish(a, color, 1.5, group, fill=color, measure="count:1:ea")
        return a

    # --- querying / editing ---
    def markups(self, include_children=False, page=None):
        """Top-level markups of every page, or only of page number `page` (none if there is no such page)."""
        out = []
        if page is None:
            pages = enumerate(self.doc)
        else:
            pages = [(page, self.doc[page])] if 0 <= page < self.doc.page_count else []
        for pno, pg in pages:
            for a in pg.annots() or []:
                if a.type[1] in ("Popup", "Link", "Widget"):
                    continue
                if not include_children and (_xget(self.doc, a.xref, PARENT_KEY) or _xget(self.doc, a.xref, "IRT")):
                    continue
                out.append(Markup(a, pno, pg, self))
        return out

    def markup_at(self, pno, point, tol=4):
        """Topmost markup on page whose bounds contain the point (for selection)."""
        pt = fitz.Point(point)
        hits = [m for m in self.markups(page=pno) if (m.rect + (-tol, -tol, tol, tol)).contains(pt)]
        return hits[-1] if hits else None

    @_mutates
    def delete(self, markup: Markup):
        for c in markup._children():
            markup._page.delete_annot(c)
        for a in markup._page.annots() or []:
            if _xget(self.doc, a.xref, "IRT") == f"{markup.xref} 0 R":
                markup._page.delete_annot(a)
        markup._page.delete_annot(markup.annot)

    def measured(self):
        """[(markup, measurement or None)] for every markup. measurement() can be costly (a Revu markup is parsed
        from its /Measure dictionary each time), so callers that need both lists and totals compute it once here."""
        return [(m, m.measurement()) for m in self.markups()]

    def takeoff(self, measured=None):
        """{(kind, unit): total} over all measurement markups. `measured`: a result of measured(), to reuse."""
        totals: dict = {}
        for m, r in self.measured() if measured is None else measured:
            if r:
                totals[(r[0], r[2])] = totals.get((r[0], r[2]), 0.0) + r[1]
        return totals

    def takeoff_by_subject(self, measured=None):
        """{(subject, kind, unit): (count_of_items, total)} — a measurement summary. `measured`: as in takeoff()."""
        out: dict = {}
        for m, r in self.measured() if measured is None else measured:
            if r:
                n, t = out.get((m.subject, r[0], r[2]), (0, 0.0))
                out[(m.subject, r[0], r[2])] = (n + 1, t + r[1])
        return out

    def export_csv(self, path):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["page", "type", "subject", "author", "status", "comment", "replies", "measure", "value", "unit"]
                       + [c["name"] for c in self.columns])
            for m in self.markups():
                r = m.measurement()
                cv = m.custom()
                w.writerow([m.page_no + 1, m.kind, m.subject, m.author, m.status, m.comment,
                            " | ".join(f"{a}: {t}" for a, t in m.replies()),
                            r[0] if r else "", f"{r[1]:.4f}" if r else "", r[2] if r else ""]
                           + [cv.get(c["name"], "") for c in self.columns])

    def export_summary_csv(self, path):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["subject", "measure", "items", "total", "unit"])
            for (s, k, u), (n, t) in sorted(self.takeoff_by_subject().items()):
                w.writerow([s, k, n, f"{t:.4f}", u])

    def render(self, pno, zoom=1.0, clip=None) -> fitz.Pixmap:
        return self._page(pno).get_pixmap(matrix=fitz.Matrix(zoom, zoom), annots=True, clip=clip)

    def save(self, path=None, force=False, **opts):
        """Save (to `path`, or in place). A document opened with a password stays encrypted with it.
        An in-place save raises FileChangedError if another program changed the file since it was opened or last
        saved; `force=True` overwrites it anyway. A file deleted meanwhile is simply written again."""
        path = path or self.path
        if path is None:
            raise ValueError("no path")
        in_place = self.path is not None and same_file(path, self.path)
        if in_place and not force:
            now = _disk_state(path)
            if now is not None and self._disk is not None and now != self._disk:
                raise FileChangedError(f"The file was changed by another program since you opened it: {path}")
        self._store_scales()
        kw = {"garbage": 3, "deflate": True, **opts}
        if "encryption" not in kw:
            kw.update(self._encryption_kwargs())
        if in_place:
            # self.doc is memory-backed (see _open), so `path` can be replaced while it stays open.
            replace_file(path, lambda tmp: self.doc.save(tmp, **kw))
        else:
            self.doc.save(path, **kw)
            self.path = path
            self.save_as_required = False   # the new file is the user's own copy
        self._disk = _disk_state(path)
        self.modified = False


def same_file(a, b) -> bool:
    """True if `a` and `b` name one file, however they are spelled (relative, `./`, case on Windows)."""
    try:
        return os.path.samefile(a, b)
    except OSError:   # one of them does not exist: compare the spellings
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _disk_state(path) -> tuple[int, int] | None:
    """(modification time in ns, size) of the file at `path`, or None if there is no file there."""
    if not path:
        return None
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return st.st_mtime_ns, st.st_size


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()

