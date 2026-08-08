<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Documentation

- **Corrected `--dry-run`/`dry-run` input docs claiming "always exits 0"**
  (CLI-audit P1): verified against the actual code (`abicheck/dry_run.py`'s
  `DryRunResult.blockers` → exit 1 for an unsatisfiable requested depth/
  evidence contract; `cli.py`'s `dump_cmd` raises `UsageError`/
  `BadParameter` → exit 64 for a malformed invocation, unconditionally
  before the `--dry-run` branch) — a dry run validates what it can see, it
  does not turn every outcome into success. `action.yml`'s `dry-run` input
  description, `docs/reference/github-action-inputs.md` (regenerated),
  `docs/use/github-action.md`, and `docs/use/scan-levels.md` now describe
  this accurately, carving out the one genuine exception: the composite
  Action's own `abi-baseline` auto-fetch, which is deliberately tolerated
  (not hard-failed) under `--dry-run` since a preview shouldn't require the
  comparison already be resolvable — `action/run.sh`'s own comments and
  `tests/test_action_run_sh_dry_run_baseline.py`'s docstrings updated to
  match.
