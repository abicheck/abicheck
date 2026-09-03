### Fixed

- **`abicheck.model.source_graph` (added in the previous ADR-061 Phase 5
  item 2 slice) could not be imported directly** — a Codex review on the
  PR caught it, and it reproduces on a clean interpreter:
  `import abicheck.model.source_graph` as the very first touch of the
  package raised `ImportError: cannot import name
  '_FULL_WALK_SOURCE_EDGES_PRODUCER' from partially initialized module`.
  Root cause: `model/source_graph.py` imported `EntityResolver`/graph
  schema names via `..buildsource.entity_resolver`/`..buildsource.
  graph_facts` — and importing *any* submodule of `abicheck.buildsource`
  first runs that package's `__init__.py`, which eagerly imports
  `call_graph.py`, which imports the legacy `buildsource/source_graph.py`
  facade, which (since the previous slice) imports back from
  `abicheck.model.source_graph` — still mid-initialization at that point.
  Existing tests never hit this because they all reach `source_graph`
  through the legacy facade first, which sidesteps the ordering.

  Fixed at the root rather than patched around: `graph_facts.py` and the
  `EntityResolver`/`entity_identity` machinery it depends on physically
  move to `abicheck/model/` (not just classified — genuinely relocated),
  since none of them have any dependency on `buildsource` at all (only
  `abicheck.name_classification`/`abicheck.demangle`, both leaves). With
  them inside `model/`, `model/source_graph.py` imports them via
  same-package relative imports (`.graph_facts`, `.entity_resolver`) that
  never touch the `abicheck.buildsource` namespace, so the cycle cannot
  form regardless of import order. `buildsource/graph_facts.py`,
  `buildsource/entity_resolver.py`, and `buildsource/entity_identity.py`
  become thin back-compat facades (`X as X` re-exports, matching this
  file family's own pre-existing convention) so every existing same-
  package relative import (`from .graph_facts import GraphNode`, used
  throughout `buildsource/`) and every absolute import
  (`abicheck.buildsource.graph_facts`/`entity_resolver`/`entity_identity`,
  used by several tests) keeps resolving unchanged.

  `graph_facts.py` (1123 lines moved as-is) split into three files to
  clear the new-file 800-line production cap moving it triggered:
  `graph_vocabulary.py` (confidence labels + the `*_NODE_KINDS`/
  `*_EDGE_KINDS` family vocabulary, 347 lines, no internal dependents),
  `graph_identity.py` (the decl/type node-id normalization functions the
  original file's own comment already called out as "split out here...
  purely to stay under the line-count cap", 322 lines), and
  `graph_facts.py` itself (`GraphFact`/`FactConflict`/`merge_graph_facts`/
  `GraphNode`/`GraphEdge`/`ensure_facts_and_resolve`/`register_fact`/
  `merge_entity_facts`/`edge_relation_key`/`edge_occurrence_id`, 542
  lines) — each re-exporting what the others need where a caller expects
  it. `entity_identity.py`'s one `from .. import demangle` module import
  became `from ..demangle import demangle as _demangle` (a specific-name
  import instead of a bare-package one) so `check_architecture.py`'s
  import scan resolves one unambiguous target (`abicheck.demangle`,
  classified `model`) instead of the ambiguous bare `abicheck` package
  reference the original form produced.

  `demangle.py` and `entity_resolver.py` join `architecture/modules.yaml`'s
  `model` `legacy_paths`; the now-superseded `graph_facts.py`/
  `entity_resolver.py` entries (from the previous slice, before this one
  physically relocated them) are removed since a physically-migrated
  file's classification comes from its real path, not a legacy-paths
  entry. No `architecture/debt.yaml` entries needed: every relocated file
  ended up under the 800-line cap after the three-way split.

  Verified: `import abicheck.model.source_graph` (and every other entry
  point — `abicheck.buildsource.source_graph`, bare `abicheck`) now
  succeeds cleanly on a fresh interpreter; `check_architecture.py` 0
  errors; `mypy abicheck/` clean; `ruff check`/`format` clean; every test
  file referencing any of the moved/relocated names directly
  (`test_source_graph*.py`, `test_entity_resolver.py`,
  `test_entity_identity.py`, `test_consumer_graph.py`, `test_use_cases.py`,
  `test_virtual_dispatch_graph.py`, `test_callback_graph.py`,
  `test_macro_graph.py`, `test_template_graph_identity.py`,
  `test_lambda_identity_ordinal.py`,
  `test_analysis_assurance_depth_and_graph_overlap.py`,
  `test_internal_leak.py`, `test_appcompat.py` — 767 tests) passes
  unchanged, and the full fast suite was re-run clean.
