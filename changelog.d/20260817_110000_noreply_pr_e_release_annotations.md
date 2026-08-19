### Added

- **A directory/package `compare` (release fan-out) now persists each
  library's `annotations` array in `--format json` output — CLI cleanup
  phase two, PR E's release-operand persistence half.** Same shape as
  single-library `compare --format json`'s top-level `annotations`
  (`{"level": "error"|"warning"|"notice", "annotation": "::error
  file=...,line=...,title=...::message", "always_visible": true}`), one
  entry per `libraries[]` entry, computed straight from that library's own
  already-produced comparison — `--annotate` on a release operand no
  longer re-runs any library's comparison a second time to collect
  annotations (it used to, via `_collect_release_extras`).

### Changed

- **`annotations.annotation_report_entries`'s persisted entries gain
  `always_visible` (report schema 2.44).** One `"notice"`-level entry kind
  — a `--contract` finding compatibility policy never evaluated — is shown
  by plain `--annotate` with no `--annotate-additions` at all; every other
  notice (an addition, a quality issue, an `info`-severity finding) exists
  only because this array computes the `--annotate-additions` superset. A
  consumer must gate a `"notice"` entry on `always_visible`, not on
  `level` alone, or it silently drops the always-shown contract finding
  (Codex review on the schema 2.43 slice this extends).
