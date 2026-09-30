### Fixed

- **A public-header type whose header origin was not read no longer hides a
  real break silently.** Public-surface scoping seeded header-declared enums
  and records from the legacy `source_header`/`qualified_name` strings, so when
  a producer stated it had not observed them (`source_header_fact` or
  `qualified_name_fact` unsupported, failed, or not collected with a reason)
  the type was demoted as `non-public-type` and a breaking change became
  `NO_CHANGE` with no stated gap. Such a demotion is now labelled
  `header-origin-unknown` in the out-of-surface ledger, adds the
  `header-origin-unknown` surface-scope note (confidence `reduced`), and adds a
  coverage warning, so every report format states that the type's public
  status could not be established.
