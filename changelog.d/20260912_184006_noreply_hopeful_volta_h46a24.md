### Removed

- **The empty `validation` optional-dependency extra** — `zstandard` became a
  core dependency in ADR-059, and the extra was kept (empty) only so an
  existing `pip install "abicheck[validation]"` wouldn't fail on an unknown
  extra. No first-party script, CI job, or doc ever referenced it, so it is
  now deleted outright rather than carried to release. The `validation/`
  harness itself is unaffected — install with `pip install -e ".[dev]"`.

### Added

- **`mypy-override-targets` AI-readiness gate** — mypy silently ignores a
  per-module `[[tool.mypy.overrides]]` entry whose target does not exist, so a
  deleted or moved module leaves its override (and the comment explaining it)
  behind with no signal anywhere. The new gate fails on any first-party
  (`abicheck.*`) target that resolves to neither a module nor a package, and on
  any wildcard target matching nothing. It found eleven stale entries on the
  tree it was written against, all now removed — eight `cli_*` modules plus
  `cli_scan`/`cli_scan_baseline`/`workflows.scan_config`, the last three left
  behind by ADR-068 Phase 6's `scan` removal.

### Documentation

- **Corrected two stale statements about already-removed surface** —
  `docs/use/api-surface-intelligence.md` said the `--surface-metrics` flag
  "still exists but is now a no-op"; ADR-068 D4/Phase 5 removed it outright, so
  passing it is a usage error (exit 64). `AGENTS.md`'s module map described
  `abicheck/cli.py` as a large file "at the 2000-line hard cap" long after
  ADR-061 Phase 4 reduced it to a ~140-line registration root.
