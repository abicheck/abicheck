### Fixed

- **A stored snapshot records a partial export-table read, and every reader
  honors it (ADR-079, snapshot schema v70)** — each `elf`/`pe`/`macho` block
  now carries `export_read` (`complete`, `partial`, `failed`; written only
  when not `complete`). A snapshot whose table was cut short used to read as
  a complete table forever, so a symbol in the unread part read as
  unexported and a comparison reported its removal as an ABI break. Now a
  `partial`/`failed` block reads as a `PARTIAL`/`FAILED` export fact (consumer
  scoping refuses it), its "not exported" answers are withdrawn, each removal
  it cannot establish is kept as `COMPATIBLE_WITH_RISK` under the new
  `partial_export_table_read` rule, `analysis_assurance.status` reads
  `partial` with the side named in `analysis_assurance.export_read` (report
  schema 7.1) and in the Markdown coverage rows, and `run_outcome.scope`
  reads `incomplete`. A pre-v70 document reads `unrecorded` and keeps the
  previous rule — never `complete`. No exit code changes; under
  `assurance.require_complete: true` the gap exits 1.
