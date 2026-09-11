### Removed

- `compare --require-complete-analysis` is removed (rulings.py
  deferred-option followup, ADR-068 D5 guard #2). P0.4's orthogonal
  ANALYSIS_INCOMPLETE exit-1 axis is config-only now — a project's CI
  strictness is a stable property, not a per-run flag. Put it in
  `.abicheck.yml` instead:

  ```yaml
  assurance:
    require_complete: true
  ```

  The old spelling is a hard usage error (`No such option`, exit 64) — there
  is no hidden alias, and no CLI escape hatch. Behavior is otherwise
  unchanged: it still contributes exit `1`, folded with `max` the same way
  `--contract`'s coverage axis is, and a directory/package (release)
  `compare` still rejects it (now via `assurance.require_complete: true`
  rather than the flag).
- The composite GitHub Action's own dedicated `require-complete-analysis`
  input, and `actions/check-target`'s mirrored input, are retired the same
  way (hard removal, no deprecation window) — both still declared so a
  workflow that sets either gets an explicit `::error::` naming
  `assurance.require_complete: true`/`.abicheck.yml` as the replacement,
  rather than a silently-ignored input.

### Added

- `.abicheck.yml`'s new `assurance:` block, with one key today:
  `require_complete` (default `false`) — see the `Removed` entry above and
  [Config file reference § `assurance:`](../docs/reference/config-file.md#assurance).
