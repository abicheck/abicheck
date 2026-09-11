### Changed

- **Internal (ADR-061 gap D, closure package 4): the release fan-out's
  pre-execution resolution is now a typed request/plan pair, not CLI
  locals.** `compare-release`'s own input discovery, ADR-065 scope/inventory
  evidence, `ReleaseScopePlan` resolution, resolved `GateOptions`, and each
  side's stored-degraded markers -- previously about a dozen local variables
  `cli_compare_release.py` threaded by hand through four separate calls --
  are now fields on `frontends.cli.release_compare_request.
  ReleaseCompareRequest`/`ReleaseComparePlan`, resolved by one function,
  `resolve_release_compare_plan`, callable directly from Python with no
  Click context. `compare-release` itself now builds one request and reads
  one resolved plan; behavior is unchanged (verified against the existing
  `test_compare_release.py`/`test_release_scope_*.py` suite, plus a new
  parity test asserting a CLI-shaped invocation and a direct typed-request
  call resolve to the same scope, gate configuration, and degraded-member
  markers). See `release_compare_request.py`'s own docstring, and ADR-061's
  gap D section, for what remains open (a typed `abicheck.service` entry
  point still needs the underlying input-discovery/package-extraction call
  chain relocated out of `frontends`/flat `cli_*` into `workflows`/`extract`).
