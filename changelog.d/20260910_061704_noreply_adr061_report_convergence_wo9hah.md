### Fixed

- **`ReportEnvelope` fully decouples from the caller's mutable operands** —
  `_snapshot_change` now deep-copies each `Change` (closing a gap where a
  nested mutable field like `impact_proof_path`'s dicts stayed shared after
  a shallow per-field list copy), and `build_report_envelope` now
  deep-copies the `old`/`new` `AbiSnapshot` operands too, so mutating either
  after the envelope was built cannot change what HTML's version/dependency
  fields or JUnit's testcase tree report.
- **SARIF's per-result `level` reuses the envelope's already-resolved
  finding** instead of re-deriving it from `result`'s live `policy_file`/
  `policy` — a `PolicyFile.overrides` mutation between two projections of
  one envelope could previously escalate a finding's SARIF `level` while
  every other format kept reporting its original, frozen verdict.
