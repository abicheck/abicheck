---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A resolved PDB/DWP/dSYM artifact reaches no dump path

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-25): OPEN.** `abicheck/dumper.py:1051` `_dump_pe` has no debug_roots parameter, and `dwp_path`/`dsym_path` are set only by `debug_resolver.py` and read only by `extract/debug_artifact.py`'s description (no dump consumer). The directory form still resolves PDB/DWP/dSYM artifacts that are then dropped.


`debug_resolver`'s chain can resolve four kinds of artifact, and only one
of them is consumed. `service_dump_native._dump_elf` reads
`DebugArtifact.dwarf_path` and nothing else -- a `dwp_path`, a `dwo_dir` or
a `dsym_path` is resolved, logged, and dropped -- and the PE branch of
`_run_dump_uncached` never passes `debug_roots` to `_dump_pe` at all, so a
PDB found in a debug root is dropped too (the PE path's only PDB input is
`debug.pdb_path`, via `locate_pdb(pdb_path_override=...)`).

**Pre-existing, and predates plan Phase 7n** -- `--debug-root` had the same
shape. What 7n changed is that the gap became *sayable*: merging the debug
role into one input meant naming a detached artifact directly, which would
have resolved a PDB/DWP and then silently ignored it, and (first review
round) made the resolver return one *ahead* of `EmbeddedDwarfResolver`, so
a binary carrying perfectly good DWARF would have been compared with none.
Both are closed: `extract/detached_debug.DetachedDebugFileResolver` is
DWARF-only and yields to the rest of the chain, and naming a PDB/DWP file
is a usage error naming `debug.pdb_path` (the spelling that *is* read).

The *directory* form is deliberately still accepted, because rejecting it
would break the case that works -- a build-id tree or path mirror of ELF
`.debug` sidecars, which is what the input is mostly for. So a directory
that happens to hold PDBs still resolves to an artifact nobody reads. That
is this gap, unchanged; do not "fix" it by rejecting directories.

Closing it properly means threading `debug_roots` into `_dump_pe` and
teaching `_dump_elf` to consume `dwp_path`/`dwo_dir`/`dsym_path` -- real
capability work on the extraction paths, not a CLI change, which is why
ADR-068's flag-topology slices leave it alone. Found by Codex review on
PR #1253.

**Update (plan Phase 7n):** `--debug-root`, `--devel-pkg` and
`--probe-matrix` no longer exist as flags -- each merged into the one input
for its evidence role (`--debug-info`, `-H/--header`, `--build-info`). The
rejections above are unchanged in substance and still keyed on the
generated destinations (`devel_pkg2`, `probe_matrix_*`, ...), which is what
made them survive the rename; only the spelling each message names moved.
Writing the test found one the manual audit had missed
(`--used-by-manifest`), which is the argument for the mechanism in one line.

The whole compute/render split is new in this pass too:
`report/no_baseline.py` now follows `abicheck/report/AGENTS.md`'s convention
— one `compute_no_baseline_document` resolving per-finding verdict/category,
evolution counts, coverage and exit contributions, and pure `render_*` halves
that format and decide nothing — so a new audit report section goes in the
document, never into one renderer. The audit publishes its own schema
(`abicheck/schemas/audit_report.schema.json`, `audit_report_schema_version`
starting at `1.0`) rather than a version of the compare report's: `findings[]`,
`cross_source_evolution`, `exit_axes` and a candidate-only
`pattern_preprocessor_scan` block are its own, and `changes: []` stays, so a
consumer reading `changes` off any abicheck report still finds it. It briefly
stamped `report_schema_version` instead, which offered the audit under the
compare report's identity -- see that field's own note in
`report/no_baseline_document.py` for why that is not merely cosmetic.
