---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Interpreter startup dominates a small-input CLI run

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** Measurement only. The entry says import cost was never investigated, and no lazy-import work is recorded; the Click registration-by-import pattern is unchanged. unverified: wall time was not re-measured.


`abicheck --version` — interpreter plus import plus Click tree construction,
before any work at all — costs **0.56–0.66 s**. A stored-snapshot/stored-snapshot
`compare` of a small fixture costs ~1.05 s end to end, so roughly **60% of it is
startup**. `compare --dry-run` (startup plus config and input resolution, diff
never run) costs 0.60–0.79 s, which bounds resolution itself at well under
0.2 s.

Consequences worth knowing before optimizing anything else: a change that halves
the actual L2 comparison work would move that run's wall time by under 20%, and
the full-CLI lane's absolute noise floor has to be set around this cost rather
than around the comparison's (hence `--regress-min-delta-seconds 0.5` by
default there, where the in-process gates use 0). A warm-cache `dump` is
*not measurably faster* than a cold one on a small fixture for the same
reason — see below.

Not investigated: which imports dominate, and whether lazy-importing the heavy
ones is feasible without breaking the registration-by-import-side-effect pattern
the CLI is built on.
