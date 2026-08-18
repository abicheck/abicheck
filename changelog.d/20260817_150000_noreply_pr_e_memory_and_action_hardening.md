### Fixed

- **A directory/package (release) `compare` no longer builds the uncapped
  per-library `annotations` array unless a JSON render actually reads it**
  (primary `--format json` or a secondary `--write json=...`) —
  `_strip_diff_results_and_adjust_verdict` now takes a `needs_annotations`
  flag, the same class of gate the sibling `_old_snapshot`/
  `collect_diff_results` fix already applies for JUnit specifically. Every
  entry in `library_results` is held in memory until the whole release
  finishes, so building this for every library unconditionally grew peak
  memory by the combined size of every library's full finding set even for
  the common markdown/JUnit-only case that never reads it.
- **The composite Action's non-destructive report-freshness check
  (`action/run.sh`'s `_json_report_src`) no longer misfires inside a test
  harness that extracts and runs only a narrow subset of the script.** The
  fingerprint bookkeeping this check relies on is set up once, right before
  the real `abicheck` invocation, in a part of the script some existing
  tests deliberately don't execute (they extract only the helpers region).
  In that isolated context the fingerprint variables were completely
  undefined, which made the check always treat a legitimate, pre-populated
  report fixture as stale. Switched to POSIX `${var+x}` existence tests so
  the check can tell "bookkeeping ran and found nothing" (a real empty
  fingerprint) apart from "bookkeeping never ran at all" (the isolated-
  harness case), degrading to the pre-fingerprint "exists and non-empty"
  rule only in the latter case — full protection is unchanged in the real
  script, which always runs the bookkeeping.
