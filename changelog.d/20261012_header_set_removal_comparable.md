### Fixed

- **Removing public headers no longer makes `compare` refuse to run** — a
  release whose declared header set shrinks (oneDAL 2026.0 deleted 122 public
  headers), or both loses and gains headers, under unchanged flags, defines and
  include configuration was refused as "not comparable" (exit 16) with a
  "manifest/CLI-flag drift" hint, after both sides had been parsed. The
  comparability gate now treats any verifiable change of the declared
  header/header-dir set as API evolution and runs the comparison, so the removed
  headers' declarations are reported as removals. Genuine extraction drift —
  differing compiler, language standard, macros, external include content or
  topology, a reordered surviving header, or a header declared on both sides but
  parsed on only one — is still refused. Removals and additions that move the
  common header root (a whole directory deleted) are realigned instead of
  refused.
