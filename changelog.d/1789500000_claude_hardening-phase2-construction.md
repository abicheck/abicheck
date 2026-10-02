### Changed

- Design-hardening Phase 2 (F2): one construction path per request and per
  snapshot. A directory/package `compare` now gives each member the release's
  resolved request plus a typed per-member delta
  (`workflows/release_member_request.py`: only the operands and their debug
  files may differ), so a new request field can no longer be dropped for
  release members. Every production `AbiSnapshot` is built by
  `workflows/snapshot_factory.py` (`new_snapshot`, `finish_snapshot`,
  `absent_baseline`), which applies provenance, dependency scoping and
  ownership in one fixed order; only the storage decoders construct directly,
  enforced by a `repo_scan` gate. `build_snapshot_from_dwarf` moved to
  `abicheck.workflows.dwarf_snapshot_assembly` (the DIE walk stays in
  `dwarf_snapshot.extract_dwarf_declarations`), and the unused
  `python_ext.detect_python_extension_from_binary` was deleted.
