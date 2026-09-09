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
- **`debug.pdb_path` is now rejected for a directory/package (release)
  compare** — the per-side-liveness check above never sees a set input's
  member DLLs (only the raw directory/package path), so it never fired, and
  the release fan-out has no PDB parameter of its own at all: a configured
  value was silently dropped, every member falling back to PDB
  auto-discovery. Rejected outright now, the same way every other
  single-pair-only flag already is (Codex review, PR #1180).
- **A `--budget` abort now renders its refusal document to a markdown,
  text, review, or HTML target too**, not just JSON/SARIF/JUnit — an
  `-o out.md`/`--write html=out.html` target used to stay silently absent,
  or worse keep a stale prior-run document, on an aborted run (Codex
  review, PR #1180).
- **The Markdown report's out-of-surface hint now says `--view filtered`**
  instead of the removed `--show-filtered` (Codex review, PR #1180).
