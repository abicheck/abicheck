### Fixed

- **ADR-061**: ported the same `contract_evidence.py`-unclassification fix
  from PR #931 into this PR's own branch (rather than depending on merge
  order) — `python scripts/check_architecture.py`'s pre-existing
  `model -> policy` violation on `contract_evidence.py` (see PR #931's
  changelog entry for the full root cause) fails CI on this branch too, since
  this PR was pushed before #931 merged. Removed `contract_evidence.py` from
  `model`'s `legacy_paths` here as well; this fragment and the identical
  `architecture/modules.yaml` change become a no-op once #931 merges and this
  branch picks it up.
