- Use the repo venv: `.venv/bin/python`. Create it with `python3 -m venv .venv && .venv/bin/pip install -e '.[test]'`.
- Run tests with `.venv/bin/python -m pytest`. Config lives in `pyproject.toml` under `[tool.pytest.ini_options]`.
- Tests run headless (`QT_QPA_PLATFORM=offscreen`). `tests/conftest.py` makes any unexpected `QMessageBox`/`QInputDialog`/`QFileDialog` fail the test, and isolates settings and the recovery folder. A test that expects a dialog monkeypatches it itself.
- CI runs on Linux, Windows and macOS. Write path and process code that works on all three: `pathlib`, no shell-specific commands, no hard-coded `/`.
- OCR, PDF/A export and PDF/A check need `tesseract`, `gs` and `verapdf` on PATH. PDF/A tests skip when `gs` or `verapdf` is missing; list skipped tests when you report results.

## Rules

- Work on a feature branch off `main`. Open PRs against `origin`, never `upstream`.
- Every bug fix and every new function ships with a test. A fix lands only after its test was seen **red** without the fix and **green** with it.
- "Done" means the full suite is green locally. Report the pass/skip/fail counts, not a summary.
- Keep the version identical in `pyproject.toml` and `openrevu/__init__.py`; `packaging/check_version.py` checks it. Release steps live in `docs/RELEASING.md`.
- `build/`, `dist/` and `*.egg-info/` are generated output. Edit sources under `openrevu/` only.
- For GUI changes, also check the relevant section of `docs/testing/MANUAL_TEST_PLAN.md` and update it when behaviour changes. Regenerate README screenshots with `docs/make_screenshots.py` when the UI they show changes.
- Write any out of scope problems you find while working on a feature or fix to a Github issue. In your reponse to the user, indicate if any issues had been created in that run but do not explain them - they will be handled by future sessions

## Workflow

pstack's `poteto-mode` runs implementation work: a change that touches more than one file, a design choice, or a bug or slowdown whose cause is unknown. It applies this repo's additions to its playbooks from `.agents/playbooks/`. Smaller tasks (one file with an obvious test, a question, a one-line edit) run directly under the rules above.

An issue labelled `ready-for-agent` is a finished brief, written by `/to-spec`, `/to-tickets` or `/triage`. Start it under `poteto-mode`.

| Situation | Skill |
|---|---|
| Test-first work | `pstack:tdd` |
| Designing types, signatures or module shape | `pstack:architect` |
| Branch ready for review | `pstack:deslop`, then Matt's `code-review` against the originating issue, then `pstack:thermos` |
| Writing a PR body | `pr` |
| Writing prose humans read (docs, issues, PRs, commits) | `pstack:unslop`; add `pstack:technical-writing` for docs |
| Seeing a change work in the real app | `.claude/skills/verify/` when it exists, else `run` |
| Naming a domain concept, editing `GLOSSARY.md`, recording a decision in `docs/adr/` | `domain-modeling` |
| Plan or design needs stress-testing before work starts | `grilling` |
| Need facts from external docs (PyMuPDF, Qt, PDF spec) | `research` |
| Editing this file, `docs/agents/*`, `.agents/playbooks/*`, or any skill | `writing-for-agents` |

## Agent skills

### Issue tracker

Issues live in GitHub Issues on the `mdouglass95/openrevu` fork, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

@.claude/pstack-models.md
