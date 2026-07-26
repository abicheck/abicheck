<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`compare --dump-manifest [old=|new=]PATH` (ADR-050 D3, G32 Phase B)**:
  side-scoped companion to `dump --dump-manifest` — dumps one side of a
  `compare` from a real multi-translation-unit manifest instead of a single
  `-H/--header` list, reusing the same side-aware `old=`/`new=`/`both=`
  grammar as `--header`/`--include` (ADR-040 Lever 1). Mutually exclusive
  with `-H/--header` for that same side; rejected outright when that side
  isn't an ELF binary, and on a directory/package (release) comparison,
  since the per-library fan-out doesn't thread a per-pair manifest.
