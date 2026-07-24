<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`consteval`/`constinit` detection now joins a bare trailing keyword
  with its declarator on the following line.** A header like
  `consteval\nint f();` (the specifier and its declarator split across
  physical lines) was never detected, since the per-line scan only ever
  saw each half separately — neither alone satisfies the "followed by an
  identifier" positive-lookahead check. The existing multi-line lookahead
  join already used for a trailing `requires`/`concept` now also triggers
  on a trailing `consteval`/`constinit`.
</content>
