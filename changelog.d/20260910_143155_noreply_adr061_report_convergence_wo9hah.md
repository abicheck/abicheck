### Fixed

- **`ReportEnvelope`'s JSON document now shares its captured `today` with the
  disposition-audit, exit-decision, and annotation blocks too** — the
  previous fix threaded `today` through `_add_changes_block`/
  `_add_policy_overrides`/`_build_severity_json`, but `_add_disposition_audit`
  and `_add_contract_context` (its `exit` block, via
  `resolve_compare_exit_decision_with_abort_axes` -> `compute_exit_code`, and
  its `annotations` block, via `annotation_report_entries` ->
  `_collect_annotations_detailed`) still had no way to receive it. If
  envelope construction straddled midnight on a dated `reclassify:` rule's
  expiry, a compatible finalized finding with `severity.exit_code: 0` could
  sit beside a stale top-level `exit.code: 4` and an `::error`-level
  annotation. `today` is now threaded through the disposition ledger
  (`policy/disposition_ledger.py`'s `_GateContext` gained a `today` field,
  used by `with_gate`/`resolve_verdict_classes`/`resolve_reclassifications`
  and by `_kept_disposition`'s gate-contribution resolution) and through the
  exit-decision/annotation resolvers (`policy/exit_decision.py`,
  `policy/exit_decision_precedence.py`, `annotations.py`).
