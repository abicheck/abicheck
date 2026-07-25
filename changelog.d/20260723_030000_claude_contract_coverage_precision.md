<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`compute_extraction_contract` could return a non-`None` "empty shell"
  `ExtractionContract`** (Codex review, PR #624): a caller passing
  L2-shaped keyword arguments (`declared_includes`, `macro_ops`,
  `compiler_family`) without also setting `l2_frontend_ran=True` — no L2
  invocation actually ran, and no scope inputs either — used to make the
  function return an `ExtractionContract` with both `profile_fingerprint`
  and `scope_fingerprint` set to `None`, instead of `None` itself.
  `checker.compare`'s `contract_coverage` logic keys off whether `contract
  is None` at all, so that shell object would misreport as real contract
  coverage. Now gated on `l2_frontend_ran` alone, matching what the
  profile-fingerprint block itself actually checks.
- **`DiffResult.contract_coverage` missed a mixed-pair case**: it only
  compared whether `old.contract`/`new.contract` were `None` as whole
  objects, so a pair where both sides carry a real `contract` but only one
  has a `profile_fingerprint` (e.g. a symbols-only side with only scope
  provenance compared against a full L2 side — `check_contracts_comparable`
  correctly skips the profile check for exactly this case, an ordinary
  depth difference) reported full coverage even though the profile axis was
  never actually checked. `contract_coverage` now compares
  `profile_fingerprint`/`scope_fingerprint` independently, mirroring
  `check_contracts_comparable`'s own per-fingerprint gating.
