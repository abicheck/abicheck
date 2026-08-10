### Changed

- **Split seven CodeFactor "Complex Method" findings across the comparability,
  contract-evidence and core-compare modules.** `comparability.
  check_contracts_comparable` now dispatches to one checker per axis
  (`dependency_scope`/`scope_fingerprint`/`profile_fingerprint`), and
  `compute_extraction_contract` to one builder per fields dict; the field layer
  moved to a new leaf module `abicheck/comparability_fields.py` to keep
  `comparability.py` under the 2000-line hard cap, with `IncludeDir`,
  `_sha256_of` and `_fingerprint_matches_fields` re-exported so no import path
  changes. `comparability_sequences._include_sequence_is_additive_owned_growth`,
  `finding_identity._looks_like_itanium_encoding`, `checker.compare`,
  `export_surface._resolvable_type_spellings` and
  `export_surface._seed_export_roots` were split the same way. All
  behaviour-preserving; `_looks_like_itanium_encoding` was additionally
  differentially checked against its pre-refactor self over 592,354 inputs with
  no differences.
