### Fixed

- **`DumpRequest.resolved_collect_mode` accepted any string, including a
  typo or an out-of-vocabulary value.** `resolve_dump_request_evidence`
  would forward such a value straight to
  `buildsource.source_replay.collection_for_ci_mode()`, whose own
  `.get(mode, ())` silently treats any unrecognized mode as `"off"`-
  equivalent — an out-of-band override could therefore be silently
  downgraded to no evidence collection with no error. `DumpRequest.
  validation_errors()` now rejects a `resolved_collect_mode` that isn't
  one of the real ADR-033 CI-mode strings
  (`CI_MODE_TO_SCOPE`'s own keys) (Codex review on #814).
