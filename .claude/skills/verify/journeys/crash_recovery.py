"""Autosave, a simulated crash, and recovery on the next start (manual test plan T48).

    .venv/bin/python .claude/skills/verify/scripts/orv.py run .claude/skills/verify/journeys/crash_recovery.py

The crash is simulated inside one run: the first window is abandoned without closing (closing would delete its
snapshot, as a clean exit does), then a second window starts against the same private recovery folder.
"""
import os


def journey(s):
    os.environ["OPENREVU_AUTOSAVE_SECONDS"] = "1"       # read when the window starts
    pdf = s.samples() / "plan_v1.pdf"
    original = pdf.read_bytes()
    s.launch(pdf)
    s.tool("Rectangle")
    s.drag(0, (520, 260), (640, 340))
    s.tool("Rectangle")                                  # drawing tools fall back to Select after one placement
    s.drag(0, (150, 300), (230, 360))
    s.check(len(s.table(s.w.mk_table)) == 2, "two markups drawn, not saved")
    s.settle(2500)                                       # let the 1 s autosave timer fire
    rec = s.work / "recovery"
    snaps = [p for p in rec.rglob("*") if p.is_file()] if rec.exists() else []
    s.check(bool(snaps), f"autosave wrote a snapshot: {[p.name for p in snaps]}")

    # "crash": stop the first window's timer and abandon it without the close path
    s.w.autosave_timer.stop()
    s.w.hide()
    crashed = s.w
    s.step("simulated crash: first window abandoned without closing")

    def recover(box):
        s.step(f"recovery offer text: {box.text()!r}")
        s.shot("recovery-offer", box)
        s.button("Recover", within=box)

    s.modal(lambda: s.launch(), recover)
    tabs = [s.w.tabs.tabText(i) for i in range(s.w.tabs.count())]
    s.check(tabs == ["*plan_v1.pdf (recovered)"], f"one recovered tab: {tabs}")
    s.check(len(s.table(s.w.mk_table)) == 2, "the recovered tab has both markups")
    s.shot("recovered")

    # saving a recovered document asks for a file name; the original stays untouched
    out = s.work / "recovered-copy.pdf"
    s.answer(("getSaveFileName", out))
    s.key("Ctrl+S", s.w.cv)
    s.check(out.exists() and s.status() == "Saved", f"Ctrl+S asked for a name and saved: {s.status()!r}")
    s.check(pdf.read_bytes() == original, "the original plan_v1.pdf is unchanged")
    r = s.cli("csv", out, s.work / "recovered.csv")
    s.check(r.returncode == 0 and len((s.work / "recovered.csv").read_text(encoding="utf-8").splitlines()) == 3,
            "openrevu-cli reads two markups from the saved copy")
    s.keep(out)
    crashed.deleteLater()
