# G31 — Header-Graph Default-On: Follow-Up Phases B–D

**Origin:** ADR-041's `--header-graph`/`--header-graph-includes` flags were
opt-in from the day they shipped. G31 Phase A (2026-07-20) flipped them to
default-on: the L2 header-only semantic graph now always builds whenever
`--depth headers` or deeper evidence is available, with the two flags
demoted to hidden, deprecated no-ops. This plan scopes the phases that
follow — deeper reconciliation, backend unification, and new
detection/perf surfaces — none of which shipped in Phase A.

**Note on naming:** this plan was drafted as "G29" before the `g29` letter
was found already claimed by
[`g29-impact-analysis-layer.md`](g29-impact-analysis-layer.md) (a separate,
earlier-registered initiative on the same graph infrastructure — unified
graph-driven impact model, reachability/suppression, consumer scoping). It
is filed here as **G31** (the next free gap letter) instead. The two
initiatives share a graph substrate but are independent efforts.
Cross-reference, don't merge, the two when planning future work — a reader
arriving at either doc should check the other for adjacent context (e.g.
Phase B's canonical entity identity below overlaps with the
impact-analysis-layer plan's own graph-identity work, Phase 2 there).

**ADR:** [ADR-041](../adr/041-compiler-facts-semantic-impact-graph.md)
(introduced the flags and the header-only graph; carries the Phase A update
note). Phase B (canonical entity identity) likely needs its own ADR before
implementation, per the same bar ADR-044's "Post-merge review rounds" note
sets for graph-identity changes.
**Type:** Initiative plan (cross-cutting; spans `abicheck/buildsource/`,
`abicheck/service.py`, `abicheck/dumper*.py`, `abicheck/binder.py`/
`resolver.py`, `abicheck/change_registry*.py`, `examples/`, `docs/`).
**Effort:** Phase A — S, done (see below). Phase B — L. Phase C — L. Phase D
— M (mostly content: new kinds/examples/docs/perf gate).
**Risk:** low for Phase A (additive default-flip, both flags kept as inert
shims). Phase B is medium-risk (touches graph node/edge identity, the same
class of change ADR-044 flags as needing a recorded decision). Phase C is
medium-risk (backend AST-reuse touches the hot `dumper_clang.py`/
`dumper_castxml.py` parse path every snapshot goes through). Phase D is
low-risk (additive kinds/examples/docs, opt-in perf gate).

---

## Phase A (done) — mechanical default-on flip

**What shipped.** The L2 header-only semantic graph (and its include-file
extension) is now always built whenever headers are parsed for a
single-library `dump`/`compare` — no flag required. `--header-graph`/
`--header-graph-includes` remain as hidden (`hidden=True`, absent from
`--help`), deprecated no-op shims on `compare` and `dump`: passing either
prints a one-line deprecation note to stderr and otherwise changes nothing.
Directory/package (set-input) `compare` still does not build the graph (the
per-library fan-out never routed through the attach step, before or after
this change); a raw `--old-sources`/`--new-sources`/`--old-build-info`/
`--new-build-info` tree on `compare` also still does not (the inline-embed
path reloads from a JSON snapshot that never attaches a graph) — both are
the same pre-existing structural gaps, just no longer flag-gated. Internally,
`service.py` gained module constants `_HEADER_GRAPH_ENABLED = True`/
`_HEADER_GRAPH_INCLUDES_ENABLED = True`; `service.resolve_input()` and
`service.run_dump()` no longer accept `header_graph`/`header_graph_includes`
keyword arguments at all — a breaking Python-API change for any direct
caller of those two functions. `service_dump_cache.py`'s whole-snapshot
cache (`cached_run_dump`) no longer disables caching for header-graph
requests, since the graph is now unconditional and deterministic given the
same already-hashed inputs, and the full snapshot (including
`build_source`) round-trips through the JSON cache.

**Files.** `abicheck/service.py` — `_HEADER_GRAPH_ENABLED`/
`_HEADER_GRAPH_INCLUDES_ENABLED` (module constants), `_attach_header_graph`
(the actual attach step, unchanged in behavior, now always invoked with
`True`/`True` except through the internal `_skip_header_graph_attach` knob
used by the buildsource-embed recursion — see the
`# TODO(header-graph-phase-D)` comment there), `run_dump`. `abicheck/cli_options.py`
— `header_graph_options` (the shared, now-hidden decorator) and
`warn_deprecated_header_graph_flags` (the deprecation-note helper both
`compare` and `dump` call). `abicheck/cli_resolve.py` —
`_EVIDENCE_SET_INPUT_FLAGS` (documents which `compare` input combinations
structurally never reach the graph-attach step, unchanged by Phase A other
than no longer needing a `UsageError` for an explicit flag that no longer
exists).

**Not fixed in this pass** (tracked as the `# TODO(header-graph-phase-D)`
comment in `service.py`): `header_graph_includes`'s per-header `clang -M`
pass has no caching of its own independent of the whole-snapshot cache. A
whole-snapshot cache *hit* restores the previously-computed `build_source`
(including the include-graph edges) without rerunning anything — the pass
only actually executes on a cache *miss* or for an uncacheable call shape
(see `service_dump_cache._dump_is_cacheable`). On a miss, though, it
re-runs unconditionally and fails soft (degrades to not_collected/partial
coverage) when clang is unavailable, with no cheaper incremental path of
its own. See Phase C's sequencing note.

## Phase B (done) — Unify graph/public-surface vocabulary + canonical entity identity

**Status: done.** Implemented via [ADR-048](../adr/048-canonical-entity-identity-and-graph-reconciliation.md)
("Canonical Entity Identity and Graph Reconciliation") — see that ADR for
the shipped scope. The problem/scope description below is retained as the
original planning record.

**Problem.** The header-only graph and the build-integrated
(`--sources`/`--build-info`) graph currently identify the same declaration
differently depending on which pass saw it first
(`SourceGraphSummary.add_node`'s first-writer-wins merge — see ADR-046 §2).
There is no USR/mangled-name-based reconciliation step that can safely tell
"this is the same entity renamed" from "this is a genuinely new/removed
entity" across an old/new comparison, which is a prerequisite for linking a
flat, single-line finding (e.g. `struct_field_type_changed`) to a graph
*proof path* (impact closure showing which public entries actually reach
it) with confidence instead of best-effort name matching.

**Scope.**
- Canonical entity identity keyed on Itanium/MSVC mangled name where
  available, falling back to a qualified-name + kind + arity key when a
  header-only pass has no mangling to offer (mirrors the existing fallback
  already used ad hoc in `internal_leak.py`'s trigger matching — see
  ADR-044's "leak triggers were a category error" post-merge note — but
  generalized into one shared resolution path instead of a per-consumer
  workaround).
- Rename-vs-add/remove disambiguation for old/new comparisons: given the
  same canonical identity resolves on both sides, a graph-visible signature
  change should be reported as one finding, not a spurious
  remove-then-add pair.
- Linking flat findings to their graph impact/proof-paths as first-class
  structured data (not just the prose `graph explain` already produces),
  so a report consumer (SARIF/JUnit/JSON) can carry the proof path
  alongside the finding it corroborates.

**Files likely to change.** `abicheck/buildsource/header_graph.py`,
`abicheck/buildsource/source_graph.py`, `abicheck/buildsource/source_graph_findings.py`,
`abicheck/buildsource/call_graph.py`, `abicheck/buildsource/type_graph.py` —
node/edge identity and merge logic. `abicheck/binder.py`/`abicheck/resolver.py`
— existing symbol-binding infrastructure to potentially reuse rather than
building a second identity-resolution mechanism from scratch.
`abicheck/demangle.py` — canonical-name derivation for the mangled-name key.

## Phase C — CastXML schema-completeness audit + backend unification

**Problem.** Two independent gaps, related but separable:

1. **Fact completeness.** Neither header AST backend currently extracts
   bitfields, vptr placement, standard-layout/trivially-copyable traits,
   deprecation, or default-argument facts with a consistent tri-state
   provenance model (known-true / known-false / not-collected) the way
   G28's Phase 1 CastXML schema-completeness audit did for the flat
   snapshot. The header-only graph inherits whatever the underlying
   snapshot parse already knows, so this is a prerequisite for the graph
   to reason about those facts at all. **Not started.**
2. **Backend duplication (done — AST reuse, not backend unification).**
   The direct-clang backend used to run a *second*, independent
   `clang -ast-dump=json` pass specifically to build the header-only graph
   (`service._attach_header_graph` → `header_graph`'s own AST walk),
   separate from the pass `dumper_clang.py` already ran to build the flat
   snapshot — exactly the gap Phase A's `# TODO(header-graph-phase-D)`
   comment flagged: since the graph is unconditional, every `--ast-frontend
   clang` dump paid a full second AST parse it didn't pay when the graph
   was opt-in and rare. **Fixed via in-process memoization, not AST
   threading**: `dumper._clang_header_dump`'s content-addressed cache key
   is now backed by an in-process memo (`dumper_cache.load_cached_ast`/
   `store_cached_ast`, bounded to 4 entries) in addition to the existing
   on-disk cache — `_attach_header_graph`'s call with the identical
   resolved headers/includes/toolchain now hits that memo and reuses the
   already-parsed dict instead of a second disk read + JSON re-parse (or a
   second subprocess, on a cold cache). This closes the *duplicated-cost*
   problem the TODO named without the deeper "thread the AST object itself
   through service.py/ElfHeaderAstResult/dumper_manifest.py across
   ELF/PE/Mach-O" plumbing a literal reuse would have needed — the memo is
   keyed the same way the disk cache already was, so it generalizes to all
   three formats and the hybrid-merge path for free, with no new
   parameters threaded through any of those call chains. The remaining,
   deliberately out-of-scope piece of "backend unification" below (a
   *single* AST object shared end-to-end, rather than a second read of an
   equivalent one) is still open — see its own bullet.

**Scope.**
- CastXML schema audit for the facts listed above, following G28 Phase 1's
  discipline (verify against real CastXML XML output before claiming a
  fact is extractable — some may turn out infeasible the way `_Atomic`
  inner-type recovery and comment-text extraction did there). **Not
  started.**
- ~~Single-AST reuse for the direct-clang backend~~ **Done** (see above) —
  via in-process memoization of `_clang_header_dump`'s result, not by
  threading the parser's already-consumed AST object through
  `service.py`/`ElfHeaderAstResult`/`dumper_manifest.py`. A literal
  single-object reuse (no second `json.loads` of the *cache file*, but also
  no second dict *construction* from disk at all, and no dependence on the
  cache key matching) is still open if the memoization's residual cost
  (cache-key recomputation, one dict lookup) ever turns out to matter.
- Hybrid-backend provenance-tagged merging: extend G28 Phase 3's
  `--ast-frontend hybrid` per-field provenance model to graph nodes/edges,
  not just snapshot facts.
- Header-defined body fingerprints (for detecting a behavior-preserving
  vs. behavior-changing inline/template body edit, distinct from a
  signature change).
- Preprocessor/build-context reconciliation: macros, `#ifdef` conditionals,
  and compile-DB flags flowing into the header parse consistently between
  the flat-snapshot pass and the graph pass (today each independently
  resolves its own compiler flags/include roots — see
  `service._attach_header_graph`'s own `-isystem` deferred-root handling,
  which G28 Phase 4's hardening already had to fix once for a cache-key gap).

**Files likely to change.** `abicheck/dumper_castxml.py`,
`abicheck/dumper_clang.py`, `abicheck/dumper.py` (`_header_ast_parser`),
`abicheck/dumper_hybrid.py`, `abicheck/buildsource/header_graph.py`,
`abicheck/buildsource/include_graph.py`.

## Phase D — New ChangeKinds, examples, docs, perf

**New/expanded `ChangeKind`s** for identity-rename/move/impact findings that
Phase B's canonical identity makes possible. Illustrative, not final —
actual names need to go through the usual `change_registry.py`
categorization step:

- `PUBLIC_API_INTERNAL_TYPE_RENAMED` — a public entry's dependency target
  was renamed (Phase B identity resolves it as the same entity, not an
  add+remove pair).
- `PUBLIC_API_INTERNAL_DEPENDENCY_MOVED` — a dependency target moved
  between internal headers/translation units without changing shape.
- `PUBLIC_API_IMPACT_PROOF_PATH_CHANGED` — an existing dependency's proof
  path (the specific edge chain a `graph explain` would show) changed
  shape even though the finding it supports did not.

**~16 new runnable examples with ground truth**, following
`examples/README.md`'s case-numbering convention. Check the current
highest case number before assigning new ones:

```bash
ls examples/ | grep case | sort -V | tail -5
```

Propose the next contiguous block (e.g. if the highest existing case is
193, claim 194–209) covering: rename-vs-add/remove disambiguation (Phase B),
each new fact family from Phase C's schema audit, at least one case per new
`ChangeKind` above, and a header-only-vs-build-integrated collector-upgrade
case exercising the reconciliation path end-to-end.

**Full documentation rewrite** covering:
- A backend capability matrix (CastXML vs. direct-clang vs. hybrid, which
  facts/edges each can and cannot see).
- "Why CastXML can't do all graph edges" (the schema-limit findings from
  Phase C's audit, in the same spirit as G28 Phase 1's "explicitly declined
  as infeasible" section).
- "How to extend clang parsing" roadmap comparing JSON AST dump vs.
  `clang.cindex` vs. LibTooling vs. preprocessor callbacks vs.
  `VTableContext` — when each is the right tool, referencing G28 Phase 4's
  own LibTooling companion-tool experience (`tools/clang-layout-tool/`) as
  a worked example of the LibTooling option's cost/benefit.

**Performance benchmarks + regression gate — done.** Now that the
header-only graph is always-on rather than opt-in, its per-dump cost is paid
on every run, not just when a user explicitly asked for it.
`scripts/check_header_graph_perf.py` (new dedicated script, following
`check_fp_rate.py`/`check_tier_accuracy.py`/`benchmark_scaling.py`'s
conventions) isolates `service._attach_header_graph`'s own marginal cost
from the `dumper.dump()` call it's layered on top of, across a synthetic
header-declaration-count sweep, and gates a later run against a committed
baseline JSON (`--baseline`/`--regress-tolerance`; report-only, same as
`check_mutation_score.py`'s `SURVIVOR_BASELINE`, until a baseline is
supplied). Self-skips without a real `clang`/`clang++`/`g++` install or off
Linux/ELF. Mirrored in `tests/test_header_graph_perf_gate.py` (pure-logic
tests run unconditionally; the live-measurement tests self-skip the same
way the script does). Not yet wired into a CI workflow — that (and
establishing/committing a real baseline JSON, so `--regress-tolerance`
actually gates rather than only reporting) is a follow-up, the same
"first scheduled run establishes the number" step `check_mutation_score.py`
itself still needs before its `SURVIVOR_BASELINE` is ever set.

**Synthetic-consumer compile-probe layer, or a deferring ADR.** If a
compile-probe layer (actually compiling a synthetic consumer against
old/new headers to observe real compiler diagnostics as corroborating
evidence, distinct from the existing runtime `app.c`/`app.cpp` fixtures in
`examples/`) turns out to be out of scope for this initiative, record that
explicitly as an ADR rather than silently dropping it — the same
discipline G28 Phase 5 used when deferring concepts/`requires` handling to
[G4](g4-header-ast-extractor.md) instead of quietly not doing it.

**Files likely to change.** `abicheck/change_registry.py` (or a sibling
`change_registry_<topic>.py`), the relevant `diff_*.py` detector module(s),
`examples/caseNNN_*/`, `docs/reference/`, `scripts/check_tier_accuracy.py`/
`scripts/check_fp_rate.py` (or a new perf-gate script).

## Sequencing note

Phase C's AST-reuse fix (in-process memoization, see above) has landed, so
Phase D's perf-regression gate is now meaningful to add: the always-on
header graph no longer structurally pays a second disk read/JSON re-parse
(or a second subprocess) on every `--ast-frontend clang` dump the way Phase
A's `# TODO(header-graph-phase-D)` comment originally flagged. A gate added
before this fix would just have baselined that known, already-diagnosed
inefficiency rather than catching a new one; that's no longer the case.
Phase C's *other* half (the CastXML fact-completeness audit) remains
unstarted and is not a blocker for Phase D's perf gate. Phase B
(canonical identity) is largely independent of Phase C and can proceed in
parallel, but Phase D's new `ChangeKind`s depend on Phase B's identity
resolution being in place first (a rename-detection finding needs the
identity layer to exist before it can be defined precisely).

## Cross-references

- [ADR-041](../adr/041-compiler-facts-semantic-impact-graph.md) — introduced
  the header-only graph and the flags Phase A retired.
- [ADR-044](../adr/044-reachability-aware-suppression.md) — reachability-aware
  suppression; documents the identity-mismatch category of bug (mangled-key
  vs. qualified-name fallback) Phase B generalizes into a shared resolution
  path.
- [ADR-046](../adr/046-source-graph-identity-v2-and-evidence-merge.md) —
  first-writer-wins merge semantics Phase B's canonical identity work
  directly supersedes for the header-graph/build-integrated overlap case.
- [g28-castxml-clang-l2-parity-hardening.md](g28-castxml-clang-l2-parity-hardening.md)
  — the sibling initiative this plan's Phase C schema-completeness audit and
  backend-unification work directly parallels (same discipline, applied to
  the graph-construction path instead of the flat-snapshot path).
- [g29-impact-analysis-layer.md](g29-impact-analysis-layer.md) — the
  unrelated, earlier-registered initiative sharing the "G29" label; see the
  naming note at the top of this document.

## Out of scope

- Re-litigating Phase A's already-shipped default-on flip or the two flags'
  deprecation-and-removal timeline — that is a settled decision recorded in
  ADR-041's Phase A update note.
- `scan`'s multi-binary fan-out gaining the header-only graph — out of
  scope for this plan the same way it was out of scope for ADR-041's
  original addendum; a separate design decision, not a mechanical
  extension of Phases B–D.
- Directory/package `compare` and the raw `--old-sources`/`--new-sources`
  inline-embed path gaining the graph — both remain structural gaps
  Phase A explicitly did not close; closing them would need routing those
  paths through `service.run_dump`'s attach step (or an equivalent), which
  is its own scoped follow-up, not implied by anything in Phases B–D above.
