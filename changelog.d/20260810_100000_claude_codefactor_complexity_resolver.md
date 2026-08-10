### Changed

- **Split the two CodeFactor "Complex Method" findings in the ADR-049 field
  resolver.** `compatibility_evaluation_resolver.resolve_field` now delegates to
  `_reject_unresolvable_candidates`, `_shadowed_legacy_candidate` and
  `_winning_candidate` — one per D7 rule it enforces — leaving the entry point as
  the resolve-then-record-provenance shape it describes.
  `detect_pack_conflicts` splits its three layers of
  annotation-isn't-runtime-enforced input validation into
  `_validate_explicit_overrides`, `_collect_pack_assignments` and
  `_reject_invalid_assignment`, so D8's actual conflict rule is no longer buried
  under them. Behaviour-preserving.
