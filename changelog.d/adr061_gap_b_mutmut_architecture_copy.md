### Fixed

- **Internal: the `mutmut` (detector core) CI lane no longer aborts at
  collection when a test reads the real `architecture/modules.yaml`.**
  `tests/test_adr061_gap_b_facades.py::test_public_root_surfaces_matches_the_reviewed_exception_set`
  reads the committed `architecture/modules.yaml` directly (the same
  `Path(__file__).resolve().parents[1]`-relative pattern several other
  real-repository-reading tests already use), but `mutmut run` measures the
  suite from inside its own `mutants/` copy, which only ever contains what
  `[tool.mutmut].also_copy` names — `architecture/` was missing from that
  list, so the test raised `FileNotFoundError` under `mutmut` specifically
  and, with `-x` set, aborted the whole mutation run before a single mutant
  was measured. Added `"architecture"` to `also_copy`, the same
  copy-only/generous-not-minimal fix every prior instance of this failure
  class received.
