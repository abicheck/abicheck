### Changed

- **ADR-049 Phase 7: contract evaluation is now on by default** for `compare`
  (CLI, typed `CompareRequest`/`compare_snapshots`, and the Action). When no
  `--contract` domain is stated, the domain is chosen from the evidence:
  `public` when header evidence closes on every compared side, otherwise
  `exports` when the export table does, otherwise `all`. Findings outside the
  selected contract no longer gate, and the orthogonal coverage exit (`1`)
  can now apply without `--contract`. Diagnostic/analysis-hygiene findings
  are not contract-relevant. `--contract all` restores the previous
  behaviour exactly.

### Fixed

- `--contract exports` now treats an observed export that no header
  declaration accounts for as an export root, so removing an undeclared
  (accidental) export is `IN_CONTRACT` rather than `UNKNOWN_UNRESOLVED`. The
  evidence-adaptive default `public` domain consults that export domain for
  any finding the headers make no commitment about, so such a removal still
  gates by default; only an explicit `--contract public` treats the headers
  as the whole promise.
