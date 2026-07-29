<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **MCP `abi_compare` gains `contract_evaluation`** — opt-in ADR-049 Phase 3
  shadow contract evaluator, mirroring the `abicheck.compare()`/
  `service.compare_snapshots()` Python API parameter that already existed.
  When set, each finding gains an advisory `contract_relevance` (plus
  `contract_reason_code`/`contract_assurance`); it never changes `verdict`
  or `exit_code`. Off by default, so existing responses are unchanged.
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
- **`abi_compare`'s top-level `changes` array now carries the shadow
  contract-evaluation fields too**: previously, `contract_evaluation=True`
  only stamped `response["report"]["changes"]`, leaving the more commonly
  consumed top-level `response["changes"]` array without
  `contract_relevance`/`contract_reason_code`/`contract_assurance` even
  though the caller had opted in.
