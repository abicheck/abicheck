<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`abi_compare`'s `used_by`/`required_symbols`-scoped findings and
  missing-symbol labels now evaluate correctly under `contract_evaluation`**
  — a scoped-only finding, a missing-required-symbol label, or an ordinary
  finding relevant to the scoped gate (e.g. a plain `func_removed` the app
  actually imports) is itself ADR-049 section 4.3 item 1's strongest
  public-evidence tier (an explicit required symbol or a concrete consumer
  import), so each is now stamped `IN_CONTRACT` directly under a new
  `explicit_consumer_or_required_symbol_evidence` reason code — overriding
  a weaker header-surface-derived decision where one already existed —
  instead of being left unstamped, or run through the header-surface
  evaluator, which could misclassify a binary-only snapshot's unresolved
  header surface as merely `UNKNOWN_UNRESOLVED`.
