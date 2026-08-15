### Documentation

- **`include`/`old-include`/`new-include` Action inputs now call out the
  "header includes a dependency's header" case explicitly** — found during a
  PVXS Action acceptance run: `abicheck scan`/`compare`/`dump` failed to
  parse `pvxs/version.h` because it transitively `#include`s EPICS Base's
  `epicsVersion.h`/`epicsTime.h`, and only PVXS's own `include/` directory
  had been passed via `header`. Traced end-to-end against real EPICS Base
  7.0 + PVXS 1.5.2 sources: this is not a header-parse defect — both AST
  backends already raise an actionable `SnapshotError`/`HeaderToolchainError`
  naming the missing include and pointing at `--include-dir`/`-I`
  (`dumper_clang_errors.diagnose_header_compile_failure`, shared by the
  castxml and clang backends) rather than a bare subprocess exit-code
  message. The Action's `include` input description now says so directly,
  and a new end-to-end regression
  (`tests/test_dependency_header_include_diagnostic.py`, mirroring
  `test_pvxs_regression.py`'s real-compile pattern) locks in both halves:
  the actionable hint when the dependency include dir is missing, and a
  clean `NO_CHANGE` self-compare once it's supplied.
