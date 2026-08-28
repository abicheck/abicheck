### Fixed

- **ADR-061**: ported the same `contract_evidence.py`-unclassification fix
  from PR #931 onto this branch (rather than depending on merge order) —
  `python scripts/check_architecture.py`'s pre-existing `model -> policy`
  violation on `contract_evidence.py` (see PR #931's changelog entry for the
  full root cause) fails CI here too, since this branch was pushed before
  #931 merged. Removed `contract_evidence.py` from `model`'s `legacy_paths`
  here as well; becomes a no-op once #931 merges and this branch re-merges
  `main`. Does not address this branch's separate, already-documented
  `debt-no-growth`/`new-file-size` architecture findings (pre-existing file
  growth predating this repo's debt-tracking package) — see the PR comment
  thread for that follow-up.
