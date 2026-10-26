### Changed

- **Internal: the assembled `ChangeKind` registry moved to `model/`, and `workflows/dump/` stops importing private names** — no behaviour change.
  The root `abicheck/change_registry.py` is retired. `REGISTRY`,
  `BREAKING_KINDS`, `API_BREAK_KINDS`, `COMPATIBLE_KINDS`, `RISK_KINDS` and
  `unanimous_entity_for` now live in `abicheck.model.change_catalog.assembled`,
  and the registry types (`Verdict`, `ChangeEntity`, ...) are imported from
  `abicheck.model.change_catalog.registry`, so `policy/` depends only on
  `model/`. `abicheck.checker_policy` and `abicheck.change_registry_types`
  keep their documented exports. The non-public `ScopeOrigin` set, which
  `idioms.py` and `type_reachability_spelling.py` each defined separately, is
  now one public `abicheck.model.vocabulary.NON_PUBLIC_ORIGINS`. The
  dependency-retention and hybrid-identity helpers that sibling modules import
  (`candidate_identity`, `directly_referenced_dependency_names`,
  `kept_signature_haystack`, `macho_normalize_mangled`, ...) and the spelling
  helpers `namespace_suffix_spellings` / `stripped_signature_spelling` are
  public in their owner modules, with no `_`-prefixed alias left behind.
