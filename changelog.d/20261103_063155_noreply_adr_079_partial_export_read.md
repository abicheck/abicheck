### Fixed

- **A stored snapshot records a partial export-table read, and every reader
  honors it (ADR-079)** — the `binary` section now carries the
  `abicheck.export_read` extension fact (ADR-062 D19): each platform block's
  read as `complete`, `partial` or `failed`, with only the non-`complete`
  ones listed. No `schema_version` bump: a `complete` dump stays readable by
  older builds, and a `partial`/`failed` one is must-understand for the
  `binary` section, so an older build reports those analyses not comparable
  instead of reading the table as complete. A snapshot whose table was cut short used to read as
  a complete table forever, so a symbol in the unread part read as
  unexported and a comparison reported its removal as an ABI break. Now a
  `partial`/`failed` block reads as a `PARTIAL`/`FAILED` export fact (consumer
  scoping refuses it), its "not exported" answers are withdrawn, each removal
  it cannot establish is kept as `COMPATIBLE_WITH_RISK` under the new
  `partial_export_table_read` rule, `analysis_assurance.status` reads
  `partial` with the side named in `analysis_assurance.export_read` (report
  schema 7.1) and in the Markdown coverage rows, and `run_outcome.scope`
  reads `incomplete`. A document without the fact reads `unrecorded` and keeps
  the previous rule — never `complete`. No exit code changes; under
  `assurance.require_complete: true` the gap exits 1.
