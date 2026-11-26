"""A small reader for the source text of PDF objects (dictionaries, arrays, names, numbers, strings, references).

It reads the raw annotation data of Bluebeam tool sets, and the PDF objects that PyMuPDF returns as text."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

MAX_DEPTH = 40              # nesting of arrays and dictionaries
_WS = b" \t\r\n\x0c\x00"
_DELIM = b"()<>[]{}/%"


@dataclass(frozen=True)
class Ref:
    """An indirect reference to object number `num`."""
    num: int


class Reader:
    """A small reader for PDF object syntax: dictionaries, arrays, names, numbers, strings.
    With refs=True an indirect reference ("12 0 R") is read as a Ref, else as None."""

    def __init__(self, data: bytes, refs: bool = False):
        self.d, self.i, self.depth, self.refs = data, 0, 0, refs

    def _skip(self):
        d = self.d
        while self.i < len(d):
            if d[self.i] in _WS:
                self.i += 1
            elif d[self.i:self.i + 1] == b"%":
                while self.i < len(d) and d[self.i] not in b"\r\n":
                    self.i += 1
            else:
                break

    def parse(self):
        self._skip()
        if self.i >= len(self.d):
            raise ValueError("unexpected end of data")
        c = self.d[self.i:self.i + 1]
        if (self.d[self.i:self.i + 2] == b"<<" or c == b"[") and self.depth >= MAX_DEPTH:
            raise ValueError("the data is nested too deeply")
        if self.d[self.i:self.i + 2] == b"<<":
            self.i += 2
            self.depth += 1
            out: dict = {}
            while True:
                self._skip()
                if self.d[self.i:self.i + 2] == b">>":
                    self.i += 2
                    self.depth -= 1
                    return out
                key = self.parse()
                if not isinstance(key, Name):
                    raise ValueError("a dictionary key must be a name")
                out[str(key)] = self.parse()
        if c == b"[":
            self.i += 1
            self.depth += 1
            arr = []
            while True:
                self._skip()
                if self.d[self.i:self.i + 1] == b"]":
                    self.i += 1
                    self.depth -= 1
                    return arr
                arr.append(self.parse())
        if c == b"/":
            self.i += 1
            j = self.i
            while j < len(self.d) and self.d[j] not in _WS and self.d[j] not in _DELIM:
                j += 1
            raw = self.d[self.i:j]
            self.i = j
            return Name(re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw).decode("latin-1"))
        if c == b"(":
            return self._string()
        if c == b"<":
            j = self.d.index(b">", self.i)
            hexs = re.sub(rb"\s", b"", self.d[self.i + 1:j])
            self.i = j + 1
            if len(hexs) % 2:
                hexs += b"0"
            return decode_text(bytes.fromhex(hexs.decode("ascii")))
        j = self.i
        while j < len(self.d) and self.d[j] not in _WS and self.d[j] not in _DELIM:
            j += 1
        tok = self.d[self.i:j].decode("latin-1")
        if not tok:
            raise ValueError(f"unexpected character {c!r}")
        self.i = j
        if tok in ("true", "false"):
            return tok == "true"
        if tok == "null":
            return None
        try:
            num = float(tok) if ("." in tok or "e" in tok.lower()) else int(tok)
        except ValueError:
            return Name(tok)  # an operator or unknown word: keep it as a name, never fail
        if isinstance(num, float) and not math.isfinite(num):
            raise ValueError("a number is out of range")
        # an indirect reference looks like "12 0 R"
        m = re.match(rb"\s+\d+\s+R\b", self.d[self.i:self.i + 20])
        if isinstance(num, int) and m:
            self.i += m.end()
            return Ref(num) if self.refs else None
        return num

    def _string(self):
        self.i += 1
        depth, buf = 1, bytearray()
        d = self.d
        while self.i < len(d):
            ch = d[self.i]
            self.i += 1
            if ch == 0x5C and self.i < len(d):          # backslash
                nxt = d[self.i]
                self.i += 1
                esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
                if nxt in esc:
                    buf.append(esc[nxt])
                elif 0x30 <= nxt <= 0x37:
                    octal = bytes([nxt])
                    while len(octal) < 3 and self.i < len(d) and 0x30 <= d[self.i] <= 0x37:
                        octal += bytes([d[self.i]]); self.i += 1
                    buf.append(int(octal, 8) & 0xFF)
                elif nxt not in b"\r\n":
                    buf.append(nxt)
            elif ch == 0x28:
                depth += 1; buf.append(ch)
            elif ch == 0x29:
                depth -= 1
                if depth == 0:
                    return decode_text(bytes(buf))
                buf.append(ch)
            else:
                buf.append(ch)
        raise ValueError("a string is not closed")


class Name(str):
    """A PDF name such as /Square."""


def decode_text(b: bytes) -> str:
    if b[:2] == b"\xfe\xff":
        return b[2:].decode("utf-16-be", "replace")
    if b[:2] == b"\xff\xfe":
        return b[2:].decode("utf-16-le", "replace")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1")


