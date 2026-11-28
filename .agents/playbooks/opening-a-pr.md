---
extends: opening-a-pr
when: Finishing any OpenRevu change, from the end of every other playbook.
---

# OpenRevu: opening a PR

- **Before** "Work from a git worktree off trunk" run the done gate:
  1. Run the full suite with `.venv/bin/python -m pytest`. It is green. Keep the pass/skip/fail counts and the names of the skipped tests for the reply.
  2. For a GUI change, walk the matching section of `docs/testing/MANUAL_TEST_PLAN.md` and update it where behaviour changed. When the change alters a UI that a README screenshot shows, regenerate the screenshots with `docs/make_screenshots.py`.
  3. Every out-of-scope problem found during the work is a GitHub issue on `mdouglass95/openrevu`. The reply lists their numbers.
- **Replace** "Title: `type(scope): subject`, naming a real symbol when one carries the change. Body: a one-minute briefing under 40 lines with these `##` headings in order, dropping empty ones: `## Why`, `## What changed`, `## Scope`, `## Tradeoffs` (only rejected alternatives a reviewer would otherwise ask about), `## Blast Radius`, `## Verification`." with: Title: a plain sentence naming the change, ending with the issue number, as in `Write Revu page scales, viewports and measurements (#12, phase 2)`. Write the body with the `pr` skill, ending with a `## Verification` section that carries the suite counts and skipped tests from the done gate.
- **Before** "Stack follow-ups by base branch." stop unless the user asked for a PR, and report the branch as ready for review. When they did, open it with `gh pr create -R mdouglass95/openrevu --base main`.
