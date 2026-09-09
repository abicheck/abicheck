<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The GitHub Action's `mode: compare` silently dropped `since:`/
  `changed-path:`** (Codex review, round 13). `docs/use/github-action-source-
  scans.md` already documented `mode: compare` as taking "the identical
  depth/since/changed-path/sources/build-info inputs `mode: scan` does",
  but `action/run.sh`'s real `mode: compare` single-pair branch only forwarded
  `sources`/`build-info`/`depth` — `since`/`changed-path` were forwarded only
  by the two `scan`-mode branches. A `since:` value on a `mode: compare`
  workflow was therefore silently ignored and a pinned `depth: source`
  replayed the whole target instead of the PR's changed files, exactly the
  unrelated-findings/expensive-CI-run risk the docs were written to avoid.
  `compare`'s CLI genuinely supports `--since`/`--changed-path`
  (`changed_path_options`, ADR-068 Phase 2c), so the fix wires both into the
  single-pair branch, matching the existing `--sources`/`--build-info`/
  `--depth` forwarding scope — left out of the directory/package fan-out
  branch, which collects no build/source evidence for either flag to scope.
  Regression tests run the real `action/run.sh` end-to-end against a fake
  `abicheck` capturing its argv, the same harness `test_action_run_sh_
  compare_build_source.py`'s existing `sources`/`build-info`/`depth`
  coverage (PR #625) uses.
