---
extends: bug-fix
when: Fixing a bug in OpenRevu.
---

# OpenRevu bug fix

- **Replace** "See the **tdd** skill for the failing-test-first cadence when the bug has a cheap local test path. Skip it when the test would be expensive, integration-heavy, or unclear." with: run `pstack:tdd`. Every fix ships with a regression test, so this step has no skip. Agree the test seam with the user before writing the test. Run the test without the fix and keep its **red** output, then with the fix and keep its **green** output. Quote both, trimmed to the assertion, in the reply.
