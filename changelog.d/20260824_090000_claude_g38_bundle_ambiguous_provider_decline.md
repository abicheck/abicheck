<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G38 bundle analysis: signature-change promotion now declines to
  attribute a consumer when more than one bundle sibling could plausibly
  be the resolved provider.** `consumer_resolves_via_provider` checked
  each candidate provider independently, so when a consumer directly
  needs *two* DSOs that both export a matching (unversioned/default, or
  same-labeled) definition of the same bare symbol, the check returned
  `True` for both — the bundle model has no notion of real ELF
  symbol-search order (`DT_NEEDED`/global-scope precedence) to say which
  one actually wins, so a signature change on *either* sibling could be
  attributed to a consumer that may bind to the other one entirely. Fixed
  by requiring the reachable+matching provider set for the symbol to be
  exactly one library before promoting — an ambiguous case now declines
  (a missed promotion) rather than fabricating an attribution (a false
  one), matching this codebase's established preference for that
  direction of error. A per-symbol GNU version pin (`version_soname`) is
  unaffected, since GNU symbol versioning already ties that reference to
  one specific provider by construction.
