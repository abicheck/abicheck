<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ADR-049 Phase 1: pack-manifest loader** (no behavior change):
  `abicheck/compatibility_evaluation_packs.py` adds `load_pack_manifest()`,
  reading a small versioned YAML pack-manifest format (`id`/`version`/
  `kind: contract|policy|gate`/`assignments`) into a `LoadedPack` —
  content-digested `ImmutableIdentity` plus the pack's own resolved
  `field name -> value` assignments — and `assignments_for_conflict_check()`
  to feed a list of loaded packs straight into
  `compatibility_evaluation_resolver.detect_pack_conflicts()`. A
  `kind: policy` manifest's `ChangeKind` slug -> severity assignments go
  through `policy_file.py`'s newly-public `parse_severity_value()` (shared
  rather than re-declared), with the identical unknown-slug hard load error
  `--policy-file` already enforces (ADR-049 D8). This module only loads pack
  *content*; selecting/composing which packs apply to a run is still
  unimplemented front-end wiring.
- **ADR-049 Phase 2: fact-conservation property tests**: a new Hypothesis
  suite (`tests/test_finding_identity_properties.py`, `slow`) exercises
  `finding_identity.py`'s identity primitive against the invariants a real
  old/new reconciliation call site will need — determinism, that an
  unchanged entity's identity is stable across independently-built
  content-identical declarations (never a spurious removal+addition pair),
  that distinct verified mangled names never collide onto the same
  CANONICAL-tier identity, and that a batch-shaped finding's identity is
  invariant under which arbitrary export was sampled into it. No wiring
  changes — `finding_identity.py` remains unconsulted by any live
  comparison path.
