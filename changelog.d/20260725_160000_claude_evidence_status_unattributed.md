### Added

- **`evidence_status`/`evidenceStatus` gains a new value, `unattributed`**
  (report schema 2.16 → 2.17, P0 evidence-provider audit). Previously an
  `ARTIFACT_PROVEN`-classified finding (kind is intrinsically a
  `BREAKING_KINDS` member) always read `"artifact_proven"` regardless of
  whether *this particular comparison* ever actually examined a real binary
  — a comparison run entirely from hand-built/loaded `AbiSnapshot` objects
  (`DiffResult.evidence_tiers == ["header"]`, e.g. a direct Python-API
  caller) could still claim artifact proof it never had. New
  `checker_policy.evidence_status_for_result(change, evidence_tiers)`
  layers this comparison-level check on top of the existing, unchanged
  `evidence_status_for_change()`: only `ARTIFACT_PROVEN` findings from a
  comparison with no binary evidence downgrade to `EvidenceStatus.UNATTRIBUTED`
  — every other status (`source_contract`, `contextual_risk`,
  `consumer_proven`, `not_checkable`) is untouched, and the kind's own
  `BREAKING_KINDS`/`API_BREAK_KINDS`/`RISK_KINDS` classification is never
  re-litigated. `reporter.py`'s JSON output and `sarif.py`'s SARIF output
  both now thread `DiffResult.evidence_tiers` through to this refined
  function; `semver.py`'s existing SONAME evidence-tier gate now imports the
  same shared `checker_policy.has_binary_evidence()`/`BINARY_EVIDENCE_TIERS`
  instead of keeping its own private copy.
