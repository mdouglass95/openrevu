"""Standard PDF measurement structures (ISO 32000-1 §12.9), as written by Bluebeam Revu and Acrobat.

A page may carry a /VP array of viewports. Each viewport has a /BBox in PDF user space and a /Measure dictionary.
A rectilinear /Measure (/Subtype /RL) holds number-format arrays; the first entry of /X has the factor /C that turns
PDF points into the unit /U. Measurement annotations carry an /IT intent and, in Revu and Acrobat output, their own
/Measure dictionary.

PyMuPDF returns PDF objects as source text, so this module includes a small parser for that text.
"""
from __future__ import annotations

import re

import pymupdf as fitz

# The unit strings Revu and Acrobat write in /U, mapped to OpenRevu units.
PDF_UNITS = {"'": "ft", "ft": "ft", '"': "in", "in": "in", "yd": "yd", "m": "m", "cm": "cm", "mm": "mm"}


class Name(str):
    """A PDF name, without the leading slash."""


class Ref(int):
    """An indirect reference (the object number)."""


_TOKEN = re.compile(rb"""
    (?P<ws>\s+|%[^\r\n]*)
  | (?P<dict_open><<) | (?P<dict_close>>>)
  | (?P<arr_open>\[) | (?P<arr_close>\])
  | (?P<ref>\d+\s+\d+\s+R(?![A-Za-z]))
  | (?P<num>[+-]?(?:\d+\.?\d*|\.\d+))
  | (?P<name>/[^\s/\[\]()<>{}%]*)
  | (?P<hex><[0-9A-Fa-f\s]*>)
  | (?P<lit>\()
  | (?P<kw>[A-Za-z]+)
""", re.X)


def _literal(src: bytes, i: int) -> tuple[str, int]:
    """Read a literal string whose opening parenthesis is at src[i - 1]. Returns (text, index after it)."""
    out, depth = bytearray(), 1
    esc = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b", ord("f"): b"\f"}
    while i < len(src):
        c = src[i]
        if c == 0x5C:  # backslash
            i += 1
            if i >= len(src):
                break
            c = src[i]
            if c in esc:
                out += esc[c]
            elif 0x30 <= c <= 0x37:
                m = re.match(rb"[0-7]{1,3}", src[i:i + 3])
                out.append(int(m.group(), 8) & 0xFF)
                i += len(m.group()) - 1
            elif c in (0x0A, 0x0D):  # line continuation
                pass
            else:
                out.append(c)
        elif c == 0x28:
            depth += 1
            out.append(c)
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return out.decode("latin-1"), i + 1
            out.append(c)
        else:
            out.append(c)
        i += 1
    raise ValueError("unterminated string")


def parse(text: str):
    """Parse the source text of one PDF object into dict / list / float / str / Name / Ref / bool / None."""
    src = text.encode("latin-1", "replace")
    stack: list = [[]]
    i = 0
    while i < len(src):
        m = _TOKEN.match(src, i)
        if not m:
            raise ValueError(f"cannot parse PDF object at {i}")
        kind, tok = m.lastgroup, m.group()
        i = m.end()
        if kind == "ws":
            continue
        if kind in ("dict_open", "arr_open"):
            stack.append(["<<" if kind == "dict_open" else "["])
            continue
        if kind in ("dict_close", "arr_close"):
            items = stack.pop()
            if len(stack) == 0 or items[0] != ("<<" if kind == "dict_close" else "["):
                raise ValueError("unbalanced PDF object")
            items = items[1:]
            if kind == "dict_close":
                if len(items) % 2:
                    raise ValueError("odd dictionary")
                val = {str(k): v for k, v in zip(items[::2], items[1::2])}
            else:
                val = items
        elif kind == "ref":
            val = Ref(int(tok.split()[0]))
        elif kind == "num":
            val = float(tok)
        elif kind == "name":
            val = Name(re.sub(rb"#([0-9A-Fa-f]{2})", lambda h: bytes([int(h.group(1), 16)]), tok[1:]).decode("latin-1"))
        elif kind == "hex":
            digits = re.sub(rb"\s", b"", tok[1:-1])
            val = bytes.fromhex((digits + b"0" * (len(digits) % 2)).decode()).decode("latin-1")
        elif kind == "lit":
            val, i = _literal(src, i)
        else:
            val = {b"true": True, b"false": False, b"null": None}.get(tok)
        stack[-1].append(val)
    if len(stack) != 1 or len(stack[0]) != 1:
        raise ValueError("not a single PDF object")
    return stack[0][0]


def resolve(doc: fitz.Document, val, depth: int = 0):
    """Follow an indirect reference to the object it points at."""
    while isinstance(val, Ref):
        if depth > 32 or not 0 < val < doc.xref_length():
            return None
        val = parse(doc.xref_object(val, compressed=True))
        depth += 1
    return val


def key(doc: fitz.Document, xref: int, name: str):
    """The parsed, resolved value of one key of an object, or None if it is missing or unreadable."""
    t, v = doc.xref_get_key(xref, name)
    if t == "null":
        return None
    try:
        return resolve(doc, parse(v))
    except ValueError:
        return None


def measure_scale(doc: fitz.Document, measure):
    """The OpenRevu Scale of a /Measure dictionary (or a reference to one), or None if it is not one OpenRevu can use."""
    from .core import Scale
    try:
        m = resolve(doc, measure)
        if not isinstance(m, dict) or m.get("Subtype") != "RL":
            return None
        x = resolve(doc, m.get("X"))
        fmt = resolve(doc, x[0]) if isinstance(x, list) and x else None
        if not isinstance(fmt, dict):
            return None
        unit = PDF_UNITS.get(str(fmt.get("U", "")).strip())
        return Scale(unit, float(fmt["C"])) if unit else None
    except (ValueError, KeyError, TypeError):
        return None


def page_viewports(doc: fitz.Document, pno: int):
    """[(bbox, scale)] for the /VP viewports of a page that have a usable scale. bbox is in PDF user space."""
    vp = key(doc, doc.page_xref(pno), "VP")
    out = []
    for item in vp if isinstance(vp, list) else []:
        v = resolve(doc, item)
        if not isinstance(v, dict):
            continue
        bbox, sc = v.get("BBox"), measure_scale(doc, v.get("Measure"))
        if sc and isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(n, float) for n in bbox):
            out.append((fitz.Rect(bbox).normalize(), sc))
    return out
