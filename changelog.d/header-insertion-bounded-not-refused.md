### Fixed

- **A public-header addition no longer refuses the whole comparison.**
  `profile_fingerprint`'s `header_sequence` field records declared-header
  order because the aggregate driver TU parses headers sequentially, and the
  only waiver was a strict *trailing* append. Headers discovered by a sorted
  directory sweep therefore refused a comparison outright whenever a newly
  added public header's name happened to sort into the middle of the existing
  set (`data.h, log.h` → `data.h, json.h, log.h` reported
  `profile_fingerprint mismatch; differing fields: header_sequence` and
  produced no verdict at all, on any axis). A growth that preserves every
  existing header's relative order and adds only headers the scope
  fingerprint independently confirms as new is now **comparable**: the
  comparison runs and the residual risk is recorded as a bounded assurance
  reduction on the `declaration`/`layout` dimensions
  (`DiffResult.comparability_assurance`, plus the reason in
  `coverage_warnings`), rather than disposed of as a refusal. A genuine
  reorder of existing headers, an unconfirmed added header, or any other
  unexplained profile field remains a hard `ProfileMismatchError` exactly as
  before, and a trailing append still keeps full assurance.
- **An operational refusal now reaches every requested output.** `compare`'s
  comparability-refusal path rendered only the primary `-o` target, so
  `compare -o review=report.md -o json=report.json` on a not-comparable pair
  wrote the Markdown refusal and no `report.json` at all — a CI wrapper
  expecting that sidecar reported a generic missing-report error instead of
  the real outcome. Every requested output now carries the same refusal
  document (`verdict: null` plus the structured reason for JSON/SARIF/JUnit),
  matching what every other abort path already did.
