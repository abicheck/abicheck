<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **`ReportSideFacts`'s report-only fields no longer affect `DiffResult` equality/repr** — `old_evidence_depth`/`new_evidence_depth`/`suppression_audit` (ADR-061 Phase 2 item 5) are now declared `compare=False, repr=False`, matching a Codex review finding that pure report annotations shouldn't make two otherwise-identical comparison results compare unequal. Two more internal callers (`buildsource/graph_reconcile.py`, `internal_leak.py`) migrated off the `buildsource/source_graph.py` compatibility facade to their real owning modules (ADR-061 Phase 5 item 2), via a small, explicit `architecture/debt.yaml` no-growth baseline bump for each.
