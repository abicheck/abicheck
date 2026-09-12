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
  any wildcard target matching nothing. Module resolution and wildcard matching
  follow mypy's own rules — stub-only (`.pyi`) modules count, and a `*`
  component matches zero or more module components, ported from
  `mypy.options.Options.compile_glob` rather than approximated. It found eleven
  stale entries on the tree it was written against, all now removed — eight
  `cli_*` modules plus `cli_scan`/`cli_scan_baseline`/`workflows.scan_config`,
  the last three left behind by ADR-068 Phase 6's `scan` removal.

### Documentation

- **Corrected two stale statements about already-removed surface** —
  `docs/use/api-surface-intelligence.md` said the `--surface-metrics` flag
  "still exists but is now a no-op"; ADR-068 D4/Phase 5 removed it outright, so
  passing it is a usage error (exit 64). `AGENTS.md`'s module map described
  `abicheck/cli.py` as a large file "at the 2000-line hard cap" long after
  ADR-061 Phase 4 reduced it to a ~140-line registration root.
- **Stopped documenting the retired `scan` command as live surface** —
  ADR-068 Phase 6 deleted `scan`, but `AGENTS.md` still declared it part of
  "the public root surface" (a contract statement, and flatly wrong against
  `tests/test_cli_root_surface.py`'s own pinned set), and `README.md` still
  offered `scan --ast-frontend clang` and listed `scan` among the commands
  adding per-command exit codes. Corrected in all three places. Wider `scan`
  drift remains in `docs/` — see the PR discussion.
- **Re-verified ADR-049's Status and advanced its `**Verified:**` receipt** —
  the receipt named a commit unreachable from `main`, so `adr-status-sync`
  errored on `main` itself. Re-read claim by claim against the code rather
  than having its sha bumped; one drift corrected (it named
  `--surface-metrics`/`--pattern-verdicts`, removed by ADR-068 D4/Phase 5).
