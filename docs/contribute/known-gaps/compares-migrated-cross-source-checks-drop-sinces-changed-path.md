---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `compare`'s migrated cross-source checks drop `--since`'s changed-path confidence boost

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** workflows/cross_source_evolution.py:360 is still `def compute_cross_source_evolution(old, new)` with no changed_paths parameter, and checker.py has no changed_paths.


Found during the ADR-068 Phase 6 doc follow-up (a stale mention of the
retired `scan --since` in `catalog/cases/case181_xcheck_public_to_internal_dependency/README.md`
prompted checking whether the claim still held).

`buildsource/cross_source_checks.py`'s `CrosscheckConfig.changed_paths` lets
a check (currently `public_to_internal_dependency`) report a finding at
higher confidence when the internal declaration it reaches lives in a file
the revision actually changed — "this call reaches a file that changed this
revision" is a stronger signal than "this call reaches *something*
internal". The retired `scan --since` wired its changed-path set through to
this automatically.

`compare()`'s own migrated cross-source-evolution path
(`checker.compare()` → `workflows.cross_source_evolution.
compute_cross_source_evolution(old, new)`) does **not** currently accept or
forward a changed-path set at all — the function's signature is
`(old: AbiSnapshot, new: AbiSnapshot)`, with no `changed_paths` parameter,
even though `compare`'s own `--since`/`--changed-path` flags exist and are
already threaded through to other consumers (`cli_compare_helpers.py`'s
`changed_paths` parameter, ADR-043 D7 POI scoping — comment-tagged
`# ADR-068 Phase 2c: ADR-043 D7 POI scoping`). So `public_to_internal_dependency`
is always reported at the lower "reaches something internal" confidence
under `compare` today, regardless of `--since`/`--changed-path`.

Tractable when picked up, but **`checker.compare()` has no `changed_paths`
parameter to thread today** (verified against its real signature,
`abicheck/checker.py`'s `def compare(old, new, suppression=None, *,
policy=..., ...)` — no `changed_paths` anywhere in it) — an earlier version
of this entry wrongly described the value as already existing on
`compare()` and needing only to be threaded further down. Adding a
`changed_paths` parameter to `checker.compare()` (and updating every
caller that already resolves a changed-path set — `cli_compare_helpers.py`
already has one — to actually pass it through) is itself the first piece
of required wiring work, not a step that can skip straight to modifying
`compute_cross_source_evolution`/`CrosscheckConfig`. Needs a regression
test asserting the confidence *does* change under `--since`/
`--changed-path` on a real (not just internal-API) `compare` invocation,
not only an internal `run_crosschecks(...)` call — the gap here was
invisible to internal tests precisely because nothing exercises the public
`--since` flag's effect on this specific check's confidence.
