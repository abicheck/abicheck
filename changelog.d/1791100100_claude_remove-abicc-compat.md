### Removed

- **The ABICC drop-in `abicheck compat` is removed** (`compat check`,
  `compat dump`), with no alias, tombstone or migration shim: `abicheck
  compat` is an ordinary unknown command. Removed with it, as nothing else
  used them: ABICC XML descriptor parsing, ABICC Perl `ABI.dump` input (also
  as a `compare` operand, now an unrecognised input), the ABICC-styled HTML
  layout (`compat_html`) and XML report, `html_report.write_html_report`,
  `SuppressionList.merge`, and the per-finding `library` attribution only
  multi-library `compat` runs set (still accepted by the report schema, never
  emitted). Use `compare`. `abi-compliance-checker` remains a benchmark.
- The native HTML report no longer labels itself "ABICC-compatible".
