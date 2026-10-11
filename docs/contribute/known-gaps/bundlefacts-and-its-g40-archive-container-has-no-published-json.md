---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `BundleFacts` (and its G40 archive container) has no published JSON Schema, in either `abicheck/schemas/` or `docs/reference/schemas/`

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/model/bundle_facts.py:53 BUNDLE_FACTS_SCHEMA_VERSION = 4, but abicheck/schemas/ and docs/reference/schemas/v1/ hold no bundle_facts schema (only aggregate/audit/audit_set/build_evidence/build_source_pack/compare). The module path in the entry is stale (bundle_facts.py is now model/bundle_facts.py).

—
investigated, not fixed (Codex review, CLI cleanup phase two's PR I
"artifact_type discriminator" prerequisite). That PR's own plan text
states the ordinary "Merge criteria" machine-contract obligations
(packaged *and* documented schema copies, JSON Schema validation) apply
when a manifest changes, and the bump `BUNDLE_FACTS_SCHEMA_VERSION` (in
`abicheck/bundle_facts.py`) got for the new `artifact_type`/
`BUNDLE_ARCHIVE_ARTIFACT_TYPE` markers is exactly that kind of change —
but a repo-wide search confirms neither
container has ever had a schema file: `abicheck/schemas/` covers
`compare_report`, `aggregate_report`, `build_evidence`, and
`build_source_pack` only, and `docs/reference/schemas/v1/` mirrors that
same set. This is a pre-existing gap predating this PR (the container
has existed since G38 Phase 2 with no schema at any version), not one
this PR's own diff introduced or made worse. Not fixed here because
authoring a first JSON Schema for a format with no existing schema
infrastructure (`scripts/publish_schemas.py`'s packaged/documented-copy
machinery, plus real validation tests) is a substantial, separate
deliverable — not a narrow addition to a field-and-classifier PR — and
because `BundleFacts`' own shape is still scheduled to change again
shortly: PR I's own `BundleCompareRequest` unification may still touch
this container's fields before the format truly stabilizes, and
authoring a schema now only to revise it again for that landing would
be wasted work on the exact same axis. **Correction (2026-09-04,
Codex review, PR #1050): `GateOptions` itself is no longer a blocker
here** — ADR-064's own dedicated slice landed it 2026-09-02
(`abicheck/policy/release_gate_options.py`); PR I's own
`BundleCompareRequest` unification simply hasn't landed yet, for
reasons unrelated to `GateOptions`. Tracked here rather than deferred silently;
the schema-authoring work belongs with (or immediately after) whichever
PR actually stabilizes `BundleFacts`' shape at its current
`BUNDLE_FACTS_SCHEMA_VERSION` (`abicheck/bundle_facts.py`) -- the
`BundleCompareRequest` PR itself, or a dedicated follow-up if that PR's
own scope doesn't naturally include it.
