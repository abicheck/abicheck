### Added

- **ADR-063 Phase 3 (D5), slice 10: `pattern_verdicts.py` /
  `diff_surface_metrics.py` old/new `public_entity_ids` pair.**
  `apply_pattern_verdicts()` and `diff_surface_metrics()` each gain an
  `old_public_entity_ids`/`new_public_entity_ids` keyword-only pair
  (`frozenset[EntityId] | None`, default `None` on both), threading each
  side's own already-resolved public-surface id set to its *matching*
  `build_surface_graph()`/`compute_surface_metrics()` call — never the
  other side's, since a declaration can cross the public/private line
  between old and new. `None` (every call site outside `compare()`'s own
  pipeline, unwired until slice 11) preserves the exact pre-Phase-3
  behavior on that side, pinned by monkeypatch-spy regression tests
  asserting each side's call receives its own set and that the disabled/
  default paths are unaffected.
