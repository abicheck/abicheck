### Changed

- A test can no longer fabricate a `Change` in a state its own type forbids.
  `Change.symbol` is annotated `str` and every producer under `abicheck/`
  honours it, but `mypy` never sees `tests/`, so three fixtures had been
  passing `symbol=None` unchecked. Instrumenting a run through one of them
  produced a `None` that read as detector behaviour, and a `known-gaps.md`
  entry recording a product defect that does not exist — now corrected, with
  the fixtures moved onto the sentinel the real producer passes. The new
  `test-change-symbol-typed` AI-readiness gate rejects
  `Change(..., symbol=None)`/`make_change(symbol=None)` anywhere under
  `tests/`; its own regression test states the rule against synthesized call
  sites with CPython's argument binding as the oracle, rather than restating
  the gate's logic back at itself.
