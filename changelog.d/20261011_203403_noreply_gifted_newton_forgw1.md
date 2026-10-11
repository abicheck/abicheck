### Fixed

- **An unchanged library no longer exits `1` because its headers contain an
  ambiguous name** — with a stated contract domain (`--contract public` or
  `.abicheck.yml` `scope.public: true`), one ambiguous identity anywhere on
  the surface (say `byte` and `std::byte`) marked the whole domain's coverage
  incomplete. Ambiguity now counts only for the findings it affects (ADR-049
  D5): such a finding is `UNKNOWN_UNRESOLVED` and listed in
  `contract_coverage_failures`; a surface that merely contains the ambiguity
  fails nothing. The `identity_coverage_incomplete` ledger reason is no
  longer emitted.
