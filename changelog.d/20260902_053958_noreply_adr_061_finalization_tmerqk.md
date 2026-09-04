### Changed

- **ADR-061 file-size cleanup, batch 2.** Split `abicheck/comparability.py`
  (1524 → 1240 lines): `check_contracts_comparable`'s profile-fingerprint
  axis (`_platform_identity_confirmed`, `_unexplained_profile_fields`,
  `_profile_mismatch_reason`, `_check_profile_fingerprint_comparable`)
  moved into a new sibling, `comparability_profile.py`. `comparability.py`
  reaches it via a dynamic `importlib.import_module` (the new module needs
  several private fingerprint-diagnostic helpers back from
  `comparability.py`, so a static two-way import would cycle); the handful
  of `comparability_sequences.py`/`comparability_language_mode.py` names
  the profile axis used, which several `tests/test_comparability_gate*.py`
  modules still import from `abicheck.comparability` directly, keep a
  self-aliased re-export there, matching this file's own pre-existing
  `comparability_fields.py`/`comparability_language_mode.py` re-export
  idiom.
