### Added

- `scan --against` accepts `--contract-evaluation` and `--contract
  public|exports|all`, mirroring `compare`'s own flags: every comparison
  finding can now carry ADR-049 Phase 3's shadow contract decision
  (`contract_relevance` / `contract_reason_code` / `contract_assurance` /
  `contract_evidence_refs`). Advisory exactly like `compare`'s — stamping a
  decision never changes the verdict, the exit code, or which findings
  appear. `ScanRequest` gained matching `contract_evaluation`/
  `contract_mode` fields for the Python API.
- `scan --against`'s JSON `diff` block now carries per-finding `finding_id`
  (the same canonical identity `compare`'s report emits, so findings from
  the two commands are joinable) and a `detectors` block with the same shape
  and the same "detectors with findings or a coverage gap" filter
  `compare`'s report uses. `scan_schema_version` is `1.6`; a run without
  `--contract-evaluation` is otherwise unchanged from `1.5`.

### Changed

- The MCP `abi_compare` tool resolves one `CompatibilityEvaluationConfig`
  through the canonical resolver instead of patching its own gate, so the
  `evaluation_context` block in its report carries real per-field ADR-049 D7
  provenance. Both live front ends now consume a resolved config.
