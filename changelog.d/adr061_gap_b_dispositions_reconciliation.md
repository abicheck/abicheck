### Fixed

- **Internal: reconciled `architecture/dispositions.yaml`/`debt.yaml` with
  this PR's already-completed migration.** Codex found that merging in
  `main`'s independent module-dispositions track left five records
  describing pre-migration state for modules this PR had already migrated:
  `checker_policy.py`, `contract_gating.py`, `reclassify.py`, and
  `contract_coverage_ledger.py`'s `dispositions.yaml` entries still said
  `disposition: migrate` with a "trial classification measured N
  violations" rationale, and `api_types.py`'s entry still described it as
  holding the typed request/response dataclasses directly. Since the
  architecture check validates record presence/shape, not factual
  freshness, this contradiction passed CI while leaving the ownership
  source of truth misleading. Updated all five to `disposition: retain`
  with rationale describing the actual retained-facade state (what moved
  where, and why the *facade* specifically stays unclassified — the same
  model→policy/compare→policy direction conflict each record's original
  trial run measured, now against the thin re-export shim rather than the
  monolith).

  Also removed `architecture/debt.yaml`'s now-fully-paid-down `no_growth`
  entries for `api_types.py` (baseline 1177) and `checker_policy.py`
  (baseline 1559): both files are now 69- and 132-line facades, and their
  tracked growth history belongs to the new owner modules
  (`workflows/contracts.py`, `policy/classification.py`,
  `policy/evidence_status.py`), none of which crossed the 800-line
  new-file ceiling that would warrant its own entry.
