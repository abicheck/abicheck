# CLAUDE.md — `abicheck/` package

This is the main Python package. See the repository-root [`CLAUDE.md`](../CLAUDE.md)
for the authoritative module map, key types, conventions, and quick-reference
commands, and this directory's own [`AGENTS.md`](AGENTS.md) for the canonical
(vendor-neutral) task-routing table and dependency-direction rules the
bounded-module migration introduced — this file is scoped, per-area context
for Claude Code sessions rooted here, not an adapter that just points
elsewhere (root `CLAUDE.md`'s "Claude Code-specific notes" section).

## Quick orientation

Pipeline order (data flow):

1. **Parse** binary → platform-specific metadata (`elf_metadata.py`,
   `pe_metadata.py`, `macho_metadata.py`, `dwarf_*.py`, `pdb_*.py`,
   `btf_metadata.py`, `ctf_metadata.py`, `sycl_metadata.py`).
2. **Snapshot** → `dumper.py` builds `AbiSnapshot` (model in `model/`),
   optionally cached via `snapshot_cache.py`.
3. **Diff** snapshots (`diff_symbols.py`, `diff_types.py`,
   `diff_platform.py`, `diff_filtering.py`, `diff_versioning.py`,
   `diff_sycl.py`).
4. **Classify** changes (`detectors.py`, `detector_registry.py`,
   `checker.py`, `checker_types.py`, `checker_policy.py`).
5. **Policy / suppression** (`policy_file.py`, `suppression.py`,
   `severity.py`).
6. **Report** (`reporter.py`, `html_report.py`, `sarif.py`,
   `junit_report.py`).

## When adding code here

- Read the matching section of the root `CLAUDE.md` before touching `cli.py`,
  `diff_platform.py`, `dumper.py`, or `compat/cli.py` — they are large
  legacy files, not a design precedent (see this directory's `AGENTS.md`
  "Working with legacy large modules": debt may shrink, may not grow).
- **New code goes to its ADR-061 target owner, not the flat legacy
  namespace.** Check this directory's `AGENTS.md` "Route work by
  responsibility" table before adding a new top-level `cli_*`, `service_*`,
  `dumper_*`, `diff_*`, `reporter_*`, `bundle_*`, or `contract_*` sibling —
  the migration targets are `domain/`, `evidence/`, `compare/`, `evaluate/`,
  `storage/`, `workflows/`, `report/`, `interfaces/`, and
  `scripts/module_architecture.py` gates new/growing files and cross-layer
  imports against `architecture/module-boundaries.json`.
- New `ChangeKind` values: follow the five-step procedure in the root
  `AGENTS.md` ("Adding a new ChangeKind").
- Every module must start with `from __future__ import annotations`
  (except `__init__.py` / `__main__.py`).
- Public types live in `model/`, `checker_types.py`, `checker_policy.py`.
  Changing their public surface is a breaking change to the Python API —
  coordinate it.

## Tests

Unit tests sit in `/tests/`. The default fast run command (see the root
`CLAUDE.md`/`AGENTS.md`) excludes integration, libabigail, abicc, slow, and
golden markers — use it. `python scripts/verify.py --profile pr --only
module-architecture-tests,module-architecture` reproduces the bounded-module
gate locally before it runs in CI.
