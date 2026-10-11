---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `compare --depth binary` skipping matrix-wide `--sources`/`--build-info` is tested only on snapshot operands, which cannot prove it

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** tests/test_scenarios.py:653 test_sc_scan_binary_depth_matrix_args was revived, but per docs/contribute/usecase-registry.yaml:1040-1055 it runs on stored JSON snapshots (tests/scenarios/ci_gating.yaml:120), so it cannot tell 'binary depth skipped collection' apart from 'snapshot inputs always skip it'.


Found while retiring `tests/scenarios/ci_gating.yaml`'s
`SC-SCAN-BINARY-DEPTH-MATRIX-ARGS` scenario and its automated test
(`test_sc_scan_binary_depth_matrix_args`) in the ADR-068 Phase 6 doc
follow-up: the scenario's premise was that a CI matrix builds one command
template and varies only `--depth`, leaving `--headers`/`--sources`/
`--build-info` present on every rung, and the binary rung must ignore the
deeper inputs and skip pattern-scan/L3 collection. The retired `scan`
command exposed this as a checkable `coverage` array
(`layer`/`status` rows) plus a `pattern_scan.files_scanned` field; verified
live, `compare -o json=...` emits neither at any `--depth`. `compare
--depth binary` does reach the same verdict/exit code on the scenario's
fixture, but nothing currently asserts it *skipped* the deeper collection
rather than merely projecting an already-collected superset down afterward
-- the two are observably different only through the now-gone coverage
block.

Tractable when picked up: decide what `compare`'s own coverage-reporting
surface should look like for this question (a `--depth`-scoped rung either
was or wasn't collected), add it, and un-retire the scenario against it.
