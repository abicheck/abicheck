### Added

- **`compare --require-same-generation`** refuses a pair whose operands were
  extracted by different abicheck extractor generations (a baseline dumped by
  an older abicheck) with a usage error (exit 64) and a "re-dump baseline"
  message. It applies to single-pair comparisons only; a directory or package
  operand is refused.

### Changed

- **An undeclared cross-generation respelling is now reported at risk level
  instead of as a break.** When the two operands come from different extractor
  generations, a type-spelling finding whose old and new values differ only in
  an identifier is demoted to `COMPATIBLE_WITH_RISK`. This applies only when
  the identifier is neither a builtin nor a type the old snapshot declares.
  The finding stays in the report with rule `extractor_generation_drift` and
  the reason "extractor-generation drift; re-dump baseline" (ADR-067).
  Respellings that a `SPELLING_MIGRATIONS` rule covers are still migrated on
  load.
- Every `SPELLING_MIGRATIONS` row (generations 1-13) now cites the commit that
  introduced the generation and what an audit of its extractor diff found.
  Only generation 6 (`AutoType` → `auto`) changed a stored type spelling.
  Generations 7 and 8 never reached main.

### Fixed

- A frozen corpus now tests cross-generation spelling drift. The corpus is in
  `tests/fixtures/generations/`. It has snapshots that the last main commit of
  each extractor generation from 1 to 12 dumped from one small C++ library.
  `tests/test_generation_corpus.py` compares each snapshot against a fresh
  dump and checks two things: the comparison has no breaking finding and
  carries a drift notice, and every respelling is covered by a declared
  migration. A new AI-readiness check, `generation-corpus`, fails an
  `EXTRACTOR_GENERATION` bump when the bump has no audited migration row or
  no frozen entry for the generation it retires. Run
  `scripts/freeze_generation_corpus.py` to add the frozen entry.
