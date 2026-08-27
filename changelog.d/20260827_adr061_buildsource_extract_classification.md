### Changed

- **ADR-061 continuation**: 19 more of `buildsource/`'s 73 flat modules (24
  total, plus its pre-existing 5) are now classified into the `extract`
  responsibility layer in `architecture/modules.yaml`, alongside 7 into
  `compare`, 2 into `storage`, 1 into `model` (plus its pre-existing 2), and
  1 into `workflows` -- 37 of 73 `buildsource/` files classified overall.
  Verified via `scripts/check_architecture.py` (0 errors).

  - `extract`: raw fact-extraction/graph-building modules
    (`header_graph.py`, `template_graph.py`, `type_graph.py`,
    `clang_ast_run.py`, `comdat_groups.py`, and 19 siblings) with no
    cross-layer import violations.
  - `compare`: `source_diff.py`/`build_diff.py` (compare old/new surfaces,
    emit `Change` findings via `ChangeKind`); `crosscheck_base.py`/
    `crosscheck_coherence.py` (raw-finding cross-check detectors, not fact
    readers); `entity_identity.py`/`entity_resolver.py` (canonical
    identity computation for old/new node matching, the same shape
    `finding_identity.py` -- already `compare` -- already has for flat
    findings); `graph_reconcile.py` (old/new node reconciliation --
    rename/move/identity-reconciliation `Change` findings). Two review
    rounds of Codex findings on this PR, all correct on reading the
    flagged files directly.
  - `storage`: `build_cache.py` (content-addressed cache, on-disk
    reads/writes) and `baseline_set.py` (baseline manifest schema/version
    parsing, snapshot-artifact loading, content-digest verification) --
    both `storage`'s explicit "manage caches"/"own schemas" ownership, not
    extraction.
  - `model`: `graph_facts.py` -- defines the shared `GraphFact`/
    `FactConflict`/`GraphNode`/`GraphEdge` schema multiple layers consume,
    not an extraction algorithm.
  - `workflows`: `baseline_publish.py` -- bridges an already-validated
    `BuildOutput` to a CI Action's payload shape, orchestration rather than
    fact-reading.

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

  The remaining 35 `buildsource/` files stay unclassified: 30 of them are
  directly imported by `frontends`-layer `cli_*.py` modules (a pre-existing
  `frontends -> buildsource` direct-import pattern that bypasses the target
  `frontends -> workflows -> extract` routing -- ADR-061 D9's own worked
  example, `abicheck/workflows/extraction.py`), and re-classifying them
  without first adding equivalent `workflows` facade re-exports would trip
  `dependency-direction`. `check_report.py`/`run_plan.py`/`project_targets.py`
  additionally import `workflows.aggregate`/`exit_decision` themselves, so
  they aren't purely `extract` layer regardless. Each needs its own
  facade-routing follow-up, not a config-only reclassification.
