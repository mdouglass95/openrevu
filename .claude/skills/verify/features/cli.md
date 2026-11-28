# Batch CLI

`openrevu-cli` runs document operations without the window: merge, split, watermark, flatten, optimize, redact, OCR, encrypt, CSV and summary exports, compare, Bates numbering, sheet bookmarks and links, footers, PDF/A, Markup Summary reports, and user scripts. Each command reads an input PDF and writes an output file. It prints a short result and exits 0 on success, 1 on an error, and 2 when PDF/A validation fails.

## Sub-features

- `exports`: `csv` (markups list) and `summary` (measurement totals) read a saved file without changing it.
- `transforms`: `merge`, `split`, `watermark`, `flatten`, `optimize`, `redact`, `encrypt`, `numbering`, `footer`, `sheets` write a new PDF.
- `compare`: `compare OLD NEW OUT [--match sheet|page] [--csv FILE]` prints one line per sheet and a summary.
- `pdfa`: `pdfa IN OUT [--level 1b|2b|3b]` needs `gs`, and is checked with `verapdf` when installed.
- `ocr`: `ocr IN OUT [--lang eng]` needs `tesseract`.
- `report`: `report IN OUT [--status S]` writes a Markup Summary PDF.
- `run`: `run IN OUT SCRIPT` executes a Python script with `doc` preloaded.
- `errors`: a bad input prints `error: …` on stderr and exits 1.

## How to get to it (user POV)

- `openrevu-cli <command> --help` in a terminal (the installed entry point).
- `python -m openrevu.cli <command> …` from a checkout. This is what `s.cli` runs.

## Driving it with orv

Preconditions:

- `s.samples()` has run. The GUI is not needed; skip `s.launch()` for a CLI-only journey (`journeys/cli_batch.py`).

- **Export markups.** `r = s.cli("csv", pdf, out)`. `r.returncode == 0` and `out` has the header `page,type,subject,author,status,comment,replies,measure,value,unit`.
- **Compare.** `r = s.cli("compare", samples / "plan_v1.pdf", samples / "plan_v2.pdf", out, "--match", "sheet")`. stdout has one `<sheet>: <status>` line each for A-101, A-102, S-201 and A-103, matching `s.expected["compare_v1_to_v2_by_sheet"]`.
- **Error path.** `r = s.cli("csv", s.work / "missing.pdf", out)`. `r.returncode == 1` and stderr starts with `error:`.
- **Protected input.** `s.cli("csv", samples / "protected.pdf", out, "--in-password", "test123")` exits 0. Without the password it exits 1.
- **Proof.** `s.cli` writes `NNN-cli-<command>.txt` with the command, exit code, stdout and stderr. `s.keep()` the outputs.

## Gotchas

- `s.cli` runs from the run's work folder with isolated settings. Pass absolute paths (`samples / ...`, `s.work / ...`).
- `pdfa` returns 2 for a non-compliant result only when `verapdf` ran. Without `verapdf` it exits 0 and prints `Not validated: veraPDF is not installed.` `doctor` shows whether `verapdf` is on PATH. Report a PDF/A check as unverified when it is missing.
- `split` derives numbered file names from `output` and prints them. Read stdout for the paths.
- `compare` prints sheets in the new file's order, then the removed sheets. Compare its result as a set, not a list.
