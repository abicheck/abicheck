### Fixed

- **`scripts/perf_receipt.py` could not be imported on Windows.** `import
  resource` at module scope raised `ModuleNotFoundError`, and since the Windows
  unit-test matrix *collects* the perf test files, that failed the lane during
  collection — before any `requires_linux` skip could apply. The harness is
  Linux/ELF-scoped and refuses to *run* elsewhere, which is a different thing
  from failing to import. The import is guarded and CPU accounting degrades to
  `None` with a stated scope rather than a fabricated `0.0`.

- **A receipt recorded the harness's revision as the measured product's.** The
  PR-vs-base lane deliberately runs HEAD's harness against BASE's installed
  package — the only way the two numbers are comparable — but the single
  `product_sha` was resolved from the directory holding the *script*, so both
  receipts claimed the head revision and the provenance could not tell them
  apart. Receipt schema 2 splits `harness_sha` from `measured_product`, the
  latter identified from the installed package (version, location, and its
  checkout's revision when it is an editable install, with a stated reason when
  it is not).

- **The perf path classifier still missed the L2 extraction layer.** A PR
  touching only `abicheck/extract/**` or one of eight `dumper_*` siblings
  classified as not-perf-sensitive, so every perf job — including the new
  full-CLI L2 gate, whose whole subject is that path — was skipped for it. Six of
  those eight were found by *deriving* the expected set from `dumper.py`'s own
  imports in the test rather than listing them by hand; that test now fails if
  `dumper.py` starts importing a sibling no pattern covers.
