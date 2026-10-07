### Changed

- The finding record `Change` (with `LibraryMetadata`, `DetectorSpec` and the
  check-id/evidence-depth validators) now lives in `abicheck.model.change`;
  `abicheck.checker_types` keeps `DiffResult`. The evidence and evolution
  enums (`Confidence`, `EvidenceTier`, `ReachabilityState`,
  `FindingEvolution`, `CrossSourceEvolution`, `EvidenceStatus`) moved from
  `abicheck.policy.evidence_status` to `abicheck.model.evidence_status`, and
  the default-verdict kind sets (`BREAKING_KINDS`, `API_BREAK_KINDS`,
  `COMPATIBLE_KINDS`, `RISK_KINDS`) are owned by `abicheck.change_registry`.
  `abicheck.checker_policy` still re-exports all of them as documented public
  API. Internally, compare-layer detectors no longer reach `policy` at all;
  `DiffResult`'s one remaining call into `policy` is now a visible, reviewed
  exception in `architecture/debt.yaml` instead of passing through
  unclassified facades.
