### Removed

- **BREAKING: the `scan` command is removed.** `abicheck scan` duplicated
  `compare` (with or without a stored baseline) and is deleted outright,
  with no alias and no deprecation window (ADR-068 Phase 6). `abicheck
  scan` now exits `64` with a `No such command` usage error naming
  `compare`/`compare --no-baseline` as the replacement. Every scan-only
  module (`cli_scan*.py`, `scan_engine.py`, `pr_comment_scan*.py`,
  `scan_abi3_resolve.py`, the `frontends/cli/scan_*.py`/
  `workflows/scan_*.py` siblings, `buildsource/poi.py`/`buildsource/
  risk.py`) and `SCAN_SCHEMA_VERSION` are deleted with it.
