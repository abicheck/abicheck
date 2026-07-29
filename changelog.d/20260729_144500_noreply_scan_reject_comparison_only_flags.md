### Fixed

- **`scan` now rejects `--policy`/`--policy-file`/`--suppress`/
  `--scope-public-headers`/`--strict-suppressions`/`--public-symbol`/
  `--public-symbols-list`/`--pattern-verdicts`/`--env-matrix` when passed
  without `--against`** — these only configure the `--against` baseline
  comparison; without a baseline they were previously silently parsed
  (and, for `--env-matrix`, even validated) and then discarded, which
  could hide a `--policy-file` requiring evidence the user actually
  needed. Now a loud usage error (exit 64) instead of a silent no-op
  (Codex review, PR #657).
