### Changed

- **ADR-061 continuation**: 19 more of `buildsource/`'s 73 flat modules (24
  total, including its pre-existing 5) are now classified into the `extract`
  responsibility layer in `architecture/modules.yaml`, alongside 5 into
  `compare`, 1 into `storage`, 1 into `model` (including its pre-existing 2),
  and 1 into `workflows` -- 34 of 73 `buildsource/` files classified
  overall. Verified via `scripts/check_architecture.py` (0 errors).

  - `extract`: raw fact-extraction/graph-building modules
    (`header_graph.py`, `template_graph_extractor.py`, `type_graph.py`,
    `clang_ast_run.py`, `comdat_groups.py`, and 19 siblings) with no
    cross-layer import violations. (`template_graph.py` itself stays
    unclassified -- see below.)
  - `compare`: `source_diff.py`/`build_diff.py` (compare old/new surfaces,
    emit `Change` findings via `ChangeKind`); `crosscheck_base.py`/
    `crosscheck_coherence.py` (raw-finding cross-check detectors, not fact
    readers); `graph_reconcile.py` (old/new node reconciliation --
    rename/move/identity-reconciliation `Change` findings).
  - `storage`: `build_cache.py` -- content-addressed cache, on-disk
    reads/writes, `storage`'s explicit "manage caches" ownership, not
    extraction.
  - `model`: `graph_facts.py` -- defines the shared `GraphFact`/
    `FactConflict`/`GraphNode`/`GraphEdge` schema multiple layers consume,
    not an extraction algorithm.
  - `workflows`: `baseline_publish.py` -- bridges an already-validated
    `BuildOutput` to a CI Action's payload shape, orchestration rather than
    fact-reading.

  **`entity_identity.py`/`entity_resolver.py`: two more review rounds, two
  reclassifications, ending unclassified.** A fifth round moved both
  `compare` -> `model`: an earlier round had classified them `compare` to
  unblock `graph_reconcile.py`'s old/new-matching cascade, reasoning by
  analogy to `finding_identity.py` (already `compare`, old/new-matching
  only) -- but `source_graph.py` (a *single*-graph schema, no old/new
  pairing at all) imports `EntityResolver` directly, stores it as
  `SourceGraphSummary.entity_resolver`, and populates it from
  `resolve_entities()` walking the graph's own node set (confirmed by
  reading the code), the same single-graph-schema shape `graph_facts.py`
  already established for `model`.

  A sixth round found that resolution itself doesn't belong in `model`
  either: `EntityResolver.resolve()` calls `resolve_identity_for_node()`,
  whose `normalize_mangled_name()` invokes `demangle.demangle()` --
  confirmed by reading both functions -- which can shell out to the
  external `c++filt` binary via `subprocess.run()` (confirmed in
  `demangle.py` itself). `model`'s own established contract (`graph_facts.
  py`/`model/change_catalog/registry.py`'s `may_import: []`, "a true leaf")
  is a dependency-free, executable-subprocess-free innermost ring;
  `entity_identity.py`/`entity_resolver.py` are executable identity-
  resolution machinery with a real subprocess dependency, not inert
  schema/value types, even though `demangle.py` itself is currently
  unclassified so no mechanical violation fires today. Per Codex's own
  suggested fallback ("split the serialized shapes from active resolution/
  demangling, or leave the active modules unclassified until that
  separation is made"), reverted both to unclassified rather than force a
  third classification without a genuinely clean fit. `graph_reconcile.py`
  (`compare`) still imports both without a `dependency-direction` error,
  since an unclassified target is not itself a forbidden layer.

  `source_graph_findings.py` and `graph_impact.py` -- also flagged as
  compare-shaped, and they are (both emit/enrich findings rather than raw
  facts) -- stay unclassified: both import `header_graph.py`/
  `call_graph.py` (still `extract`-classified, genuine raw AST/graph
  extraction) at module/lazy scope, which `compare`'s `may_import: [model]`
  forbids. Unlike `entity_identity.py`/`entity_resolver.py`/
  `graph_reconcile.py` above, there is no clean further reclassification
  that resolves this cascade -- `call_graph.py` is real Clang-AST call-graph
  extraction and correctly stays `extract`. Left unclassified per Codex's
  own suggested fallback rather than forcing an incomplete fix.

  **`scripts/check_architecture.py` fix, found while investigating a third
  round of Codex findings**: `template_graph.py`/`virtual_dispatch_graph.py`
  both import the `compare`-owned `diff_cxx_rules.py` via
  `from .. import diff_cxx_rules`, a real `extract -> compare` violation --
  but the checker reported zero errors for either, because its import
  resolver only ever read `node.module` for an `ImportFrom` node, which is
  empty for this bare-dot form (`from . import x`/`from .. import x`); the
  resolved target silently collapsed to the enclosing package, dropping
  which submodule was actually imported. Fixed generally (not just for
  these two files): the resolver now also records `<package>.<name>` for
  each name in a bare-dot `from`-import, so a real submodule import via this
  idiom can't be invisible to `dependency-direction` anywhere in the repo.
  Verified narrowly scoped before trusting it -- a real repo-wide check with
  the fix applied surfaced exactly these two known violations and zero new
  false positives from any other module's ordinary `from . import <plain
  symbol>` usage. Two new regression tests in `tests/test_architecture_
  check.py` pin both directions (the violation is now caught; an ordinary
  same-layer bare-dot import of a plain symbol still isn't flagged),
  confirmed to fail/pass correctly against both the pre-fix and post-fix
  checker.

  With the checker now catching it, `template_graph.py`/
  `virtual_dispatch_graph.py` themselves stay unclassified (reverted out of
  `extract`) rather than resolving the cascade -- `diff_cxx_rules.py` is a
  genuinely shared C++-mangling decoder both `compare` and these two
  extraction modules need, so cleanly classifying either side needs its own
  design (move the shared decoders to an inward layer, or accept the
  duplication), not a same-PR reactive patch.

  **A seventh review round found the bare-dot fix above was itself
  incomplete**: it only handled a relative import with an empty
  `node.module` (`from . import x`/`from .. import x`), not the equally
  valid absolute form (`from abicheck import legacy_compare`, `node.module`
  set to `"abicheck"`) -- so a `legacy_paths`-classified importer using the
  absolute spelling stayed exactly as invisible to `dependency-direction`
  as the relative form was before the third-round fix, with no
  `unclassified-import` fallback to catch it either. Fixed by generalizing
  rather than adding a second special case: the `<package>.<name>`
  recording now runs unconditionally for every `ImportFrom` node, relative
  or absolute alike, since both spellings are equally ambiguous from the
  AST alone (`<name>` may be a plain symbol or a submodule of `<package>`).
  Verified the same way as the third-round fix -- a real repo-wide check
  still surfaces exactly the known violations and zero new false positives
  -- with a new regression test,
  `test_absolute_import_of_forbidden_submodule_is_enforced`, confirmed to
  fail against the narrower (bare-dot-only) checker and pass against the
  generalized one.

  **`baseline_set.py` reverted back out of `storage`, a fourth review
  round.** Codex's storage classification itself was correct (confirmed
  again above), but flagged fresh evidence this PR's own earlier "0
  errors" check couldn't see: `baseline_set.py` imports and calls
  `elf_metadata.parse_elf_metadata` -- `extract`'s documented owner per
  `AGENTS.md`'s task-routing table -- and `elf_metadata.py` isn't
  classified on this branch only because PR #899 (the parser-module
  `extract` classification) hasn't merged yet, not because the dependency
  doesn't exist. Verified by simulation rather than taking the claim on
  faith: temporarily adding `elf_metadata.py` to `extract` (as #899 will)
  and re-running the checker reproduces exactly the predicted
  `storage -> extract` violation, plus a `extract -> storage -> extract`
  dependency cycle. Reverting `baseline_set.py` to unclassified now avoids
  landing a classification this repository's own in-flight, independently
  reviewed sibling PR would immediately break.

  **`export_accounting.py`'s compare-shaped claim investigated and found
  incorrect** -- the one Codex finding across four review rounds on this
  PR that didn't hold up, so left as `extract`, unchanged. The finding
  attributes `_check_exported_not_public`'s `Change`-emission to this
  module, but that function is defined in the separate, still-unclassified
  `crosscheck.py`, not here -- `export_accounting.py`'s own docstring
  states the opposite explicitly ("These helpers are free of any
  `Change`/`ChangeKind` concern; `crosscheck` turns the undocumented
  buckets into findings"), and a direct grep confirms no `Change`/
  `ChangeKind` construction anywhere in the file. It genuinely is what its
  docstring says: pure mangled-name classification over already-extracted
  snapshot/export data, `extract`'s job.

  The remaining 38 `buildsource/` files stay unclassified: 30 of them are
  directly imported by `frontends`-layer `cli_*.py` modules (a pre-existing
  `frontends -> buildsource` direct-import pattern that bypasses the target
  `frontends -> workflows -> extract` routing -- ADR-061 D9's own worked
  example, `abicheck/workflows/extraction.py`), and re-classifying them
  without first adding equivalent `workflows` facade re-exports would trip
  `dependency-direction`. `check_report.py`/`run_plan.py`/`project_targets.py`
  additionally import `workflows.aggregate`/`exit_decision` themselves, so
  they aren't purely `extract` layer regardless. Each needs its own
  facade-routing follow-up, not a config-only reclassification.
