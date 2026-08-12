### Fixed

- **`RESOLVE_FAILURE_OUTCOMES`** (`abicheck/buildsource/check_report.py`) now
  derives from the canonical `ResolveOutcome`/`ALL_OUTCOMES` registry instead
  of a hand-duplicated set that had fallen out of sync — `wrong_project_ref`
  was missing, which made `check-target` propagate that outcome as a usage
  error (no structured report) instead of a recognized operational-error
  report.
- **`publish-baseline.yml`'s "Upload release asset" safe-retry check** now
  compares the existing asset's manifest `profile` against the current run's
  profile before comparing content digests, and never treats a profile
  mismatch as a safe retry — closing a gap where an `asset-name-template`
  without `{profile}` in a multi-profile matrix could have one profile's
  baseline silently discarded as an identical "retry" of an unrelated
  profile's asset.
- **`publish-baseline.yml`'s retry-comparison download** now names the
  downloaded existing asset after its real filename (matching extension)
  instead of a hardcoded `.tar.zst`, so a non-default, non-zstd
  `asset-name-template` can actually reach the safe-retry comparison instead
  of failing on a decode error first.
- **`action/validate-inputs.sh`** now fails fast when `baseline-profile`/
  `baseline-target` are set without `abi-baseline` — those two inputs alone
  can never trigger the release-contract baseline-set fetch, so this was
  previously silently ignored.
- **`.github/workflows/protect-committed-baseline.yml`**'s diff now passes
  `--no-renames`, closing a gap where a protected baseline file renamed to an
  unprotected path could evade detection.
- **`action/validate-inputs.sh`** no longer warns on every ordinary
  `dump`/`appcompat`/`deps-*` run about `baseline-asset-name-template` being
  "set but has no effect" — the scope check now keys off
  `baseline-profile`/`baseline-target` only, not the template input's own
  always-nonempty default.
