<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`scan --artifact-set`: `--budget` now covers set discovery, not just
  the per-member scans.** `run_scan_set()` started its shared budget clock
  only after `discover_artifact_set()` had already run — that step stats
  every candidate path and parses each one's ELF program/dynamic table to
  classify it, real work on a large set that was previously invisible to
  `--budget`. Even `--budget 0s` could spend substantial time discovering
  before ever reporting overflow, contrary to the documented time guard.
  The clock now starts before discovery, so a slow discovery phase is
  caught the same way a slow member scan or bundle audit already was.
