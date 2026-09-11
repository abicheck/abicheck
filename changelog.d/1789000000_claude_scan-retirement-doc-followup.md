### Fixed

- **Removed the dead `scan` help-panel scaffolding left over from ADR-068
  Phase 6.** `abicheck/frontends/cli/help.py` still carried a `"* scan"`
  rich-click `OPTION_GROUPS` panel, a `"scan"` entry in the root-command
  panel, a `SCAN_COMMON_OPTION_NAMES` frozenset, and an unused
  `scan_help_options` decorator, all referencing the `scan` command deleted
  in ADR-068 Phase 6 — removed, with the matching dead test coverage in
  `tests/test_cli_help.py` retired alongside it.
- **Fixed two scenario tests that still invoked the deleted `scan` command**
  (`tests/test_scenarios.py::test_sc_baseline_pin`, previously failing with
  `No such command 'scan'` on every run) — migrated to `compare`. A third,
  `test_sc_scan_binary_depth_matrix_args`, asserted on a `coverage`/
  `pattern_scan` report shape `compare --format json` has never emitted;
  retired rather than faked, with the gap it leaves open recorded in
  `docs/contribute/known-gaps.md`.

### Documentation

- Migrated the remaining ~56 documentation, catalog, and use-case-registry
  mentions of the retired `scan` command to their real `compare`/
  `compare --no-baseline` equivalents (verified against the live CLI
  surface rather than token-swapped), closing out the retired-surfaces
  warnings the ADR-068 Phase 6 doc sweep first found. Fixed a stale claim
  in `docs/reference/config-file.md` about `risk_rules:`/`crosschecks:`
  still being parsed by the now-deleted `buildsource/risk.py`.
- Recorded two new tracked gaps found during this sweep:
  `compare`'s migrated cross-source-evolution path does not forward
  `--since`/`--changed-path` into `CrosscheckConfig.changed_paths` (so
  `public_to_internal_dependency`'s confidence boost never fires under
  `compare`), and `compare --depth binary` has no equivalent of `scan`'s
  per-rung `coverage` block, so "did the binary rung actually skip deeper
  collection" is currently unverifiable.
