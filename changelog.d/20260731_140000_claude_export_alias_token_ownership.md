### Fixed

- `contract=exports` (ADR-049): a user-defined typedef sharing a bare key
  with a captured standard-library record no longer inherits that record's
  toolchain exclusion. Ownership is now decided per *token* — using the
  implementation-reserved identifier rule ([lex.name]/3) and the stdlib
  namespaces — instead of per alias, so a genuinely missing type inside such
  an alias's target is reported as an unresolved edge rather than silently
  leaving `exclusion_is_provable` true.
