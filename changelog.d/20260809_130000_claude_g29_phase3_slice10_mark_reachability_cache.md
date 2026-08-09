### Added

- **`Change.impact_assessment` — `MarkReachability` now caches it, closing
  the D2 producer sweep** (ADR-052 D2 follow-up, Slice 10):
  `post_processing.MarkReachability` — the only pipeline step that mutates
  `public_reachable`/`reachability_state`/`reachability_kind`/
  `reachability_proof_path` on an already-constructed `Change` — now calls
  `impact.engine.assess_change(change)` right after it finalizes those
  fields and attaches the result to `Change.impact_assessment`, the same
  cache-and-reuse pattern `internal_leak.py`/`appcompat.py` used in Slices
  8-9. This was previously an open, unmeasured question; it is now measured:
  a `compare --format json --secondary-format sarif` invocation genuinely
  calls `assess_change()` twice for the same `Change` object in one process
  (`reporter.py`'s JSON path and `sarif.py`'s SARIF path each read the same
  already-computed `DiffResult` independently), confirmed by an instrumented
  test rather than assumed.
  `abicheck/buildsource/source_graph_findings.py`'s ten `Change(...)`
  construction sites (across nine finding functions) were re-audited and
  found *not* individually safe to cache at construction time — unlike
  `internal_leak.py`'s builder, they run **before**
  `post_processing.DEFAULT_PIPELINE` (their findings are merged into
  `checker.compare`'s `changes` ahead of the whole pipeline), so
  `MarkReachability` still runs downstream of them and would invalidate an
  eagerly-cached assessment. None of the ten sites were changed to cache
  directly; `MarkReachability`'s own new caching reaches every one of these
  findings anyway, once tagged. `suppression.py`'s own named role in D2
  remains the one unresolved item (it constructs no `Change` of its own) —
  see ADR-052's "Slice 10" and "Deliberately not implemented this slice"
  sections for the full per-site detail and the measurement methodology.
