### Changed

- **ADR-063 Phase 9 (selector/suppression/reclassification consolidation,
  D10) is complete.** `suppression.py`'s `Suppression` and
  `reclassify.py`'s `ReclassifyRule` now share one selector-matching
  primitive — `abicheck/policy/selectors.py`'s new `SelectorSet` — instead
  of `reclassify.py` reusing `Suppression` purely for its selector grammar
  via a runtime `importlib.import_module` workaround for an import cycle
  (`policy_file -> reclassify -> suppression -> checker_types ->
  policy_file`). The shared grammar (`symbol`/`symbol_pattern`/
  `type_pattern`/`member_name`/`namespace`/`entity_namespace`/
  `cause_namespace`/`source_location`/`change_kind`/`binding`/`finding_id`/
  `expires`) lives in a dependency-free leaf module (zero import of
  `checker_types.py`/`suppression.py`/`reclassify.py`/`policy_file.py`/
  `finding_identity.py`, enforced by a new `scripts/check_architecture.py`
  gate), so `reclassify.py` now imports it **statically** and the
  `importlib` workaround is gone. Selector-matching *behavior* is
  unchanged — every existing `suppression.py`/`reclassify.py` selector test
  still passes against the shared matcher, and the namespace-glob
  compilation machinery moved verbatim into a sibling leaf
  (`abicheck/policy/selectors_namespace_glob.py`, split purely to stay
  under the 800-line architecture ceiling).
