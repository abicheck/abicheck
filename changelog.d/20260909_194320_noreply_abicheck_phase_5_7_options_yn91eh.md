### Fixed

- **`compare`'s `debug.pdb_path` rejection now only fires when both sides
  are live PE binaries** — the first version (Phase 7's `--pdb-path`
  removal) rejected a configured `debug.pdb_path` for *any* two-operand
  compare, which broke the common stored-baseline-vs-live-candidate shape
  (`compare old.json new.dll --config ...`) where only the live side could
  ever consult it. A PDB is only ever read while extracting a PE binary, so
  the sharing risk the rejection guards against — two different binaries
  silently reading the same PDB — only exists when both operands are PE
  (Codex review, PR #1180, "Allow PDB config when only one operand is live").
- **`CompareRequest` no longer silently rebinds a positional caller's later
  arguments when a field is removed from the middle of the dataclass** — a
  `KW_ONLY` sentinel now sits exactly where the removed `reconcile_build_context`
  field used to be, so a positional caller reaching that far now fails
  loudly at construction (`TypeError`) instead of shifting every later value
  onto the wrong field (Codex review, PR #1180).
