### Changed

- **The per-artifact resolve/execute pipeline now has a real home**
  (ADR-061 Phase 3). `abicheck.workflows.artifact` gained `resolve` (decide
  what an extraction will do) and `execute` (run that plan and report what it
  achieved) alongside the existing `contracts`; `abicheck.service_input_resolution`
  survives as a delegating facade for the import paths callers already use.
  Keeping "decide" runnable without "do" is what lets `dump --dry-run` render
  the same resolved plan a real run consumes rather than a separately-derived
  preview. No behavior change: every public name keeps its spelling, signature
  and semantics.
- **The compare-side build/source evidence report moved to the engine.**
  `diff_embedded_build_source`, `prepare_embedded_build_source` and
  `attach_evidence_metrics` now live in `abicheck.buildsource.evidence_report`,
  which owns no output stream: it renders its ADR-028 D7 coverage/capability
  report as lines and hands them to an optional `on_output` sink, replacing a
  `quiet` flag that only ever meant anything to a caller that had a stream.
  The CLI adapter supplies a stderr sink, so the report still covers every
  output format without polluting a `--format json` stdout. Exit codes are
  unchanged and now pinned: a malformed out-of-band pack stays **exit 1**
  (operational), never 64 (usage).
