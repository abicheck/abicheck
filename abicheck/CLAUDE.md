# CLAUDE.md — `abicheck/` package

This is the main Python package. See `/CLAUDE.md` at the repo root for the
authoritative module map, key types, conventions, and quick-reference
commands; that file already documents this directory in depth.

## Quick orientation

Pipeline order (data flow):

1. **Parse** binary → platform-specific metadata (`elf_metadata.py`,
   `pe_metadata.py`, `macho_metadata.py`, `dwarf_*.py`, `pdb_*.py`,
   `btf_metadata.py`, `ctf_metadata.py`, `sycl_metadata.py`).
2. **Snapshot** → `dumper.py` builds `AbiSnapshot` (model in `model.py`),
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

- Read the matching section of `/CLAUDE.md` before touching `cli.py`,
  `diff_platform.py`, `dumper.py`, or `compat/cli.py` — they are large
  and intentionally so.
- New `ChangeKind` values: follow the four-step procedure in
  `/CLAUDE.md` ("Adding a new ChangeKind").
- Every module must start with `from __future__ import annotations`
  (except `__init__.py` / `__main__.py`).
- Public types live in `model.py`, `checker_types.py`,
  `checker_policy.py`. Changing their public surface is a breaking
  change to the Python API — coordinate it.

## Tests

Unit tests sit in `/tests/`. The default fast run command (see
`/CLAUDE.md`) excludes integration, libabigail, abicc, slow, and golden
markers — use it.
