<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ADR-049 Phase 1: pack selection wiring** (no behavior change):
  `abicheck/compatibility_evaluation_wiring.py` adds
  `resolve_selected_packs()`, the first front end to compose the existing
  pack-manifest loader (`load_pack_manifest`) with pack-conflict detection
  (`detect_pack_conflicts`) into real `contract.packs`/`policy.packs`/
  `gate.packs` field resolutions from a list of manifest paths. Not called
  from any live command yet — no `--pack` CLI flag exists (`cli.py` is at
  its line-count hard cap) — but fully tested against its own semantics,
  matching the existing `resolve_legacy_contract_mode`/
  `resolve_internal_namespaces` wirings' pattern.
