"""openrevu-cli on the sample files: CSV export, compare, a protected input, and the error path. No window.

    .venv/bin/python .claude/skills/verify/scripts/orv.py run .claude/skills/verify/journeys/cli_batch.py
"""


def journey(s):
    samples = s.samples()
    out = s.work / "markups.csv"
    r = s.cli("csv", samples / "plan_v1.pdf", out)
    header = out.read_text(encoding="utf-8").splitlines()[0] if out.exists() else ""
    s.check(r.returncode == 0 and header == "page,type,subject,author,status,comment,replies,measure,value,unit",
            f"csv exits 0 with the markups header: {header!r}")

    overlay = s.work / "compare.pdf"
    r = s.cli("compare", samples / "plan_v1.pdf", samples / "plan_v2.pdf", overlay, "--match", "sheet")
    got = {line.split(":")[0]: line.split(":", 1)[1].strip().split(",")[0] for line in r.stdout.splitlines()
           if line[:1].isalpha() and ":" in line and "-" in line.split(":")[0]}
    want = {k: v.split()[0] for k, v in s.expected["compare_v1_to_v2_by_sheet"].items()}
    s.check(r.returncode == 0 and got == want, f"compare by sheet matches expected.json: {got}")
    s.check(overlay.exists(), "compare wrote the overlay PDF")
    s.keep(overlay)

    r = s.cli("csv", samples / "protected.pdf", s.work / "p.csv")
    s.check(r.returncode == 1 and r.stderr.startswith("error:"), "a protected input without its password exits 1")
    r = s.cli("csv", samples / "protected.pdf", s.work / "p.csv", "--in-password", s.expected["protected_password"])
    s.check(r.returncode == 0, "with --in-password it exits 0")

    r = s.cli("csv", s.work / "missing.pdf", s.work / "m.csv")
    s.check(r.returncode == 1 and r.stderr.startswith("error:"), f"a missing input exits 1 with error: {r.stderr!r}")
