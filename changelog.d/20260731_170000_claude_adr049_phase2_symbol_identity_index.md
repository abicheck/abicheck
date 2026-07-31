### Changed

- **ADR-049 Phase 2: `diff_symbols.py`'s old/new function and variable matching
  now joins through the shared identity primitive.** New
  `finding_identity.SymbolIdentityIndex` — the flat-symbol counterpart of
  `diff_helpers.TypeMap` — is a `Mapping` over the same keys
  `_public_functions`/`_public_variables` already return, plus an
  ambiguity-checked alias tier (`unique_alias_match`, which answers `None` for
  "no candidate" and "several candidates" alike; `alias_candidates` tells the
  two apart). `_match_old_function`'s `extern "C"` fallback is now one alias
  lookup with an eligibility predicate instead of a hand-rolled name multimap
  and an inline candidate count, and `_diff_variables` joins through the same
  index. Matching behaviour is unchanged: verified against the golden, FP-rate,
  per-tier-accuracy, detector-oracle and fact-conservation suites. Unlike
  `TypeMap`, key lookup never resolves an alias, and variables enable no alias
  tier at all — two differing mangled names are two different exports, so a
  display-name join would report a genuine removal as a modification.
