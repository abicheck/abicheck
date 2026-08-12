<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`protect-committed-baseline.yml` now always protects
  `.github/workflows/**` too, independent of `protected-paths`.** This
  reusable workflow's own `protected-paths`/`bypass-label` configuration
  is supplied by the *calling* workflow file, and for an ordinary
  `pull_request` trigger (this workflow's own required, fork-safe
  trigger) that file is read from the PR's own head commit — so a PR
  could previously edit the calling workflow (narrowing `protected-paths`
  to a glob that no longer matches, or dropping the check's invocation
  entirely) and the committed baseline in the same change, recreating the
  exact self-approval bypass this workflow exists to prevent. A change
  under `.github/workflows/` now always counts as a hit, uses the same
  `bypass-label` gate, and gets its own dedicated error message
  distinguishing it from an ordinary `protected-paths` hit.
- **`actions/stage-baseline/run.sh`'s zstd fast-path gate** no longer
  redundantly checks for a standalone `zstd` binary alongside
  `_tar_zstd_works` — a `tar` build with zstd support linked in directly
  needs no separate `zstd` CLI on `PATH`, and `_tar_zstd_works`'s own real
  trial archive already proves the whole path works regardless of which
  way `tar` gets its zstd support.
