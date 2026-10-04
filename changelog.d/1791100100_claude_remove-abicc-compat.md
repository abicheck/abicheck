### Removed

- **The ABICC drop-in `abicheck compat` is removed** (`compat check`,
  `compat dump`), with no alias. `abicheck compat` now exits `64` naming
  `compare`; *Upgrading to 0.6* maps each ABICC flag (`-old`/`-new`,
  `-skip-symbols`, `-symbols-list`, `-strict`, `-warn-newsym`,
  `-report-path`, ...) to its `compare` equivalent. Removed with it, as
  nothing else used them: ABICC XML descriptor parsing, ABICC Perl `ABI.dump`
  input (also as a `compare` operand, which is now rejected as an
  unrecognised input), the ABICC-styled HTML layout (`compat_html`) and XML
  report, `html_report.write_html_report`, `SuppressionList.merge`, and the
  per-finding `library` attribution only multi-library `compat` runs set
  (still accepted by the report schema, never emitted). Owner of the
  surviving path: `compare`. `abi-compliance-checker` remains a benchmark.
- The native HTML report no longer labels itself "ABICC-compatible".
