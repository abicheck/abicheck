### Added

- **Directory/package `compare` now records each library's suppression audit
  in the report, not only on stderr.** Every `libraries[]` entry in the
  release JSON carries the same `suppression_audit` block a single-pair
  report does (stale, expired and near-expiry rules, and rules that hid a
  BREAKING change; release schema 1.12), and the release Markdown ends with
  a per-library "Suppression Audit" section rendered from it. Rule labels
  are shown verbatim; matched symbols are demangled.
