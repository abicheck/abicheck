### Fixed

- **`DispositionLedger.with_gate` now refreshes stale `reclassify`/verdict
  overlays instead of leaving them stamped at `compare()` time** — the
  ordinary case for a real comparison is that `checker.compare()` already
  built and attached a `DispositionLedger` before a report is ever rendered.
  `with_gate` (the path `ledger_for` takes for such a ledger) re-labelled
  `gating`/`non_gating` dispositions but never re-ran
  `resolve_reclassifications`/`resolve_verdict_classes`, so a ledger's
  `reclassified_by`/`verdict_class` overlays — resolved once at *comparison*
  time — stayed stamped after the `reclassify:` rule they name expired by
  envelope-construction time. The JSON document's `disposition_audit` could
  then report an expired rule as still active while `changes[]`/
  `policy_reclassify` (both re-resolved fresh from the envelope's own
  `today`) correctly said it was not. Both overlays are now cleared and
  re-resolved fresh on every `with_gate` call.
- **`resolve_compare_exit_decision`'s legacy (no severity configuration)
  branch now honors a caller-supplied `today`** — it previously always
  translated the cached `result.verdict`, frozen at `compare()` time,
  ignoring `today` entirely. If a dated `reclassify:` rule expired between
  `compare()` and envelope construction, a JSON document's `changes[]` entry
  (correctly re-derived under the envelope's `today`) could report a finding
  as breaking while the top-level `exit.code` stayed the stale, pre-expiry
  verdict's `0`. When `today` is given, the legacy branch now re-derives the
  worst per-finding effective verdict under it instead; every pre-existing
  caller that never passes `today` is unaffected.
