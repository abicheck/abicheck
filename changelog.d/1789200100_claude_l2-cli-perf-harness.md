### Added

- **A full-CLI L2 performance harness** (`scripts/check_l2_cli_perf.py`), the
  third and outermost of the project's perf measurement levels: the whole
  `abicheck` CLI as a subprocess against a real compiled C++ fixture, covering
  the six supported L2 forms (`dump` to a stored snapshot, live/live with
  side-specific headers, stored/live, stored/stored, `--no-baseline` audit, and
  a JSON plus human report over one extraction of evidence). The measured window
  is what a user waits for — interpreter startup through report writing — with
  fixture compilation and every correctness check strictly outside it.

  Correctness is gated alongside duration, because the worst regression
  available to a timing harness is getting faster by doing less: each scenario
  asserts that the resolved evidence depth really reached `headers`, that public
  scoping resolved without falling back, that the expected declarations survived
  into the snapshot, that the header call/include/type graph passes all ran
  undegraded, and that the fixture's deliberate break produced both a
  removal-family and a layout-family finding. Native tool invocations are
  *observed* through `PATH` shims and classified by argv, which is what turns
  "a stored/stored comparison runs no compiler at all", "a stored/live
  comparison extracts one side only", and "this warm run really was served by a
  cache" into measurements rather than assumptions — version probes are counted
  separately from header extraction so they cannot inflate either number.

  A missing required scenario shape fails the run rather than reading as a clean
  pass, and a deliberately narrowed run prints an explicit "coverage not
  claimed" line.
