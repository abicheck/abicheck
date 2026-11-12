### Fixed

- **PDB type database no longer keeps a half-parsed state.** `TypeDatabase.parse_all`
  marked itself parsed before parsing, so an unexpected error from any step
  left a partly filled type table that every later call treated as complete.
  It now marks itself parsed only after success and starts over on retry.
- The pattern-facts C lexer rejects an unknown internal state instead of
  scanning it as a character literal.
