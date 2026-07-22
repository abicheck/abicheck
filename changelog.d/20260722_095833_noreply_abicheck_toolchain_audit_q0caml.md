### Fixed

- **The C++20 dialect detector now also joins a bare "concept" keyword
  split across lines** (no backslash continuation), matching the same
  line-split gap already fixed for "requires" — a "concept" keyword at the
  end of one line with its name/definition starting the next was never
  seen as one construct, since the line-join lookahead only triggered on a
  trailing "requires".
