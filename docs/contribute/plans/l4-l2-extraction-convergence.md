# L2/L4/L5 extraction convergence — one parse per TU, one owner per primitive

**Status:** Proposed; Phase 1 implemented (this plan's first PR).
**Parent plans:** [Duplication & convergence assessment](duplication-and-convergence-assessment.md)
(its P1 "Compiler invocation handling needs one typed model" owns Phase 3
here — this plan does not restate that design), [One Semantic Pipeline](one-semantic-pipeline.md)
(ADR-063 owns identity and `SemanticIR`; Phases 4–5 here are that plan's
L4 slice, not a parallel design).

## Why this exists

A `dump --depth source` of a real, modest C++ library (PVXS, epics-base/pvxs
PR 216: 34 translation units, one `libpvxs.so`) was profiled end to end.
The header tier (L2) finished in ~43s. The source tiers took over 30 minutes,
and the time was not in any detector — it was in **re-running the same
compiler front end over the same TUs**:

| Stage (PVXS, 4 cores, thread executor, before this plan) | Wall |
|---|---|
| DWARF (pyelftools) | 27–28s |
| L2 header AST (castxml, one pass) | 5.5–6.3s |
| L4 source replay — one `clang -ast-dump=json` per TU | 232s |
| L5 call-graph pass — **a second** `clang -ast-dump=json` per TU | 343s |
| L5 type/override/template/macro/callback passes — **four more full dumps** per TU each... | > 15 min (run was killed at 30 min) |

The six clang-backed L5 passes (`inline_graph_fold.fold_semantic_graphs`,
before this plan) built the *identical* argv for a TU (`call_graph._safe_clang_args_from_compile_unit`)
and ran the identical bounded dump (`clang_ast_run.run_clang_ast_dump`); they
differed only in which **pure** parser they applied to the resulting tree.
L4's replay then dumps the same TU a seventh time under a separately-built
argv. Profiling also showed the worker "pools" running on threads with the
AST post-processing serialized on the GIL (load average ~1.3 on 4 cores);
the process pool is opt-in (`ABICHECK_L4_EXECUTOR=process`).

A correction worth recording so it is not re-derived: a cProfile of the L2
run attributed ~83s to `dwarf_metadata._process_struct` re-processing each
struct in every CU. Measured without the profiler, the whole basic DWARF pass
is 9.7s, and skipping *every* repeat outright (unsound) saves 0.37s. The
profile was an artifact of cProfile's per-call overhead on pyelftools' very
high call counts. Measure unprofiled before attacking a "hotspot".

## Inventory — what is shared, what is duplicated, what is intentionally distinct

| Concern | L2 owner | L4/L5 owner | State |
|---|---|---|---|
| castxml invocation | `extract/castxml_compiler_emulation` | `source_extractors/castxml.py:322` | **Shared** |
| castxml XML → model | `dumper_castxml._CastxmlParser` | `source_extractors/castxml.py:511` (same parser + `base.entity_from_*`) | **Shared** — the model for Phase 4 |
| clang `-ast-dump=json` tail flags | `_compiler_options.clang_ast_dump_tail` | same | **Shared** |
| clang argv from a compile unit | `dumper_ast_config.py` | L4 `source_extractors/clang.py:_clang_context_args` · L5 `call_graph._safe_replay_flags_from_context` | **Duplicated three ways** → Phase 2/3 |
| compile-db flag rewriting | `CompileContext` / `header_utils` | `source_extractors/_argv.py` (~1,700 lines) + `_argv_shortopts.py` | **Duplicated** → Phase 3 (parent plan's P1) |
| clang subprocess runner | `dumper_toolchain` / `dumper_clang_streaming` | L4 `_deadline_bound.run_bounded_for_extraction` · L5 `clang_ast_run.run_clang_ast_dump` | **Duplicated** → Phase 2 |
| L5 per-TU AST dump | — | six extractors, one dump + one loop each | **Fixed (Phase 1)**: one `l5_ast_pass` run |
| clang JSON → declarations/types | `dumper_clang._ClangAstParser` → `RecordType`/`Function` | `source_extractors/clang.py` + `clang_nodes.py` → `SourceEntity` | **Duplicated** → Phase 4 |
| body/default-arg/template fingerprints, macros (`-E -dD`) | — | `clang_nodes.py`, `clang.py:macros_from_preprocessor` | **Intentionally L4-only** — additive facts, keep |
| entity identity | `model.identity.EntityId`, `extract.semantic_normalizer` | `SourceEntity.identity`, `abicheck-clang-canonical` fact set | **Duplicated** → Phase 5 (ADR-063) |
| per-TU cache | `dumper_cache.py`, header AST cache | `source_replay.SourceAbiCache` | **Duplicated** → Phase 6 |
| worker sizing | `extract.tu_jobs._tu_jobs` | `source_replay._l4_jobs`, `call_graph._call_graph_jobs` | **Duplicated** → Phase 7 |
| diffing | `diff_types` / `diff_symbols` | `source_diff.diff_source_abi` | **Partly intentional** → Phase 8 (assess, don't assume) |

## Phases

Each phase is a separate PR, is behavior-preserving unless it says otherwise,
and proves itself on the public workflow (a real `dump --depth source`), not
only on an internal unit.

### Phase 1 — one L5 AST pass replaces six extractors (implemented)

`buildsource/l5_ast_pass.py`. The six `Clang*GraphExtractor` classes (call,
type, override, template, macro-range, callback) are **deleted**, with the
two modules that existed only to hold two of them
(`override_graph_extractor.py`, `template_graph_extractor.py`), the lazy
`template_graph.ClangTemplateGraphExtractor` re-export, and
`call_graph.extract_from_args`/`_safe_clang_args_from_argv`. Each class
carried its own copy of one loop -- select the units, size a worker pool,
dump every TU, fold diagnostics in input order, merge across TUs -- and
dumped every TU itself.

What replaced them:

- `run_l5_ast_pass` scopes the compile DB **once** (the precedence
  `_scope_narrowed_target` already defined) and resolves the clang binary
  once; `run_ast_passes` sizes one pool, dumps each TU **once**, and applies
  every family's `AstPass` (`L5_AST_PASSES`: a per-TU parser, a cross-TU
  merge, and the exceptions that degrade a TU for that family only).
- Each family keeps only what is genuinely its own: its pure parser, its
  cross-TU merge (now public: `merge_call_edges`, `merge_type_edges`,
  `merge_override_facts`, `merge_template_instantiations`,
  `merge_decl_ranges`, `merge_callback_edges`; the macro family's per-TU
  cwd resolution is `parse_tu_decl_ranges`), and its `inline_graph_fold`
  fold, which now reads `run.outcomes[name]` instead of constructing an
  extractor.
- `fold_semantic_graphs` is the one entry point. The out-of-band `collect`
  path used to keep its own copy of the pass list (and fell behind it three
  times, per its own comments); it now calls the same function.

Two observable differences, both deliberate:

- The override family's three parsers run as one parser
  (`parse_clang_ast_override_facts`); a malformed AST that made the *third*
  raise used to still record the first two's facts for that TU, and now
  records none. Either way the TU degrades to a diagnostic.
- The `collect` path now runs the include-graph fold last (as the inline
  path always did) rather than third; the include pass is independent of
  the others, so only the order of extractor rows changes.

Tests: `tests/test_l5_ast_pass.py` states the runner's contract as
generated invariants against an independent oracle (the family's own
`merge(parse(...))` computed directly): one dump per TU whatever the family
count, each family's result and diagnostics equal to that derivation, a
parse failure degrading only its own family and TU, and no dependence on
worker completion order. Mutating the runner to dump per family, or to fold
in completion order, fails it. `tests/_fake_l5_ast_pass.py` is the shared
stand-in the fold tests use in place of the old per-class fakes.

### Phase 2 — L4 replay and L5 read the same dump

Make L4's `ClangSourceExtractor` and the L5 passes share one dump per TU:
one argv builder for a clang AST replay of a `CompileUnit`, one bounded
runner, and the L4 extractor contributing an `AstPass` to the same run
(its macro pass, `-E -dD`, is a different invocation and stays separate).
Prerequisite: reconcile `_clang_context_args` with
`_safe_replay_flags_from_context` — they intentionally differ today (L5
allowlists flags; L4 carries ABI-relevant flags through), so the unified
builder must be the *union* L4 needs, and the L5 parsers must be shown to
produce the same edges under it (differential test on real fixtures, both
builders, per AGENTS.md's "a differential test must prove both of its
configurations ran"). Expected effect: removes the remaining second dump per
TU, i.e. roughly halves what Phase 1 leaves.

### Phase 3 — one compile-invocation model

Owned by the parent plan's P1 (`CompilerInvocation`/`SourceOperand`).
From this plan's side the acceptance criterion is concrete: `_argv.py`,
`call_graph._safe_replay_flags_from_context` and `dumper_ast_config`'s clang
argv all consume the one parsed invocation, and `_argv.py` shrinks to the
backend-specific rendering it genuinely owns (clang-cl driver mode,
castxml emulation).

### Phase 4 — one clang AST → declaration/type parser

Do for clang what `source_extractors/castxml.py` already does for castxml:
run L2's `dumper_clang._ClangAstParser` over the TU's AST and project through
`base.entity_from_*`, keeping L4's genuinely additive facts (body/default-arg/
template fingerprints, macros, source edges) as a layer over it. This changes
the L4 `SourceEntity` payload for clang-produced surfaces, so it needs a
`CLANG_EXTRACTOR_VERSION` bump (cache invalidation) and a stated
comparability rule for a stored pre-change L4 surface (the `fact_set`
mechanism, ADR-038 C.8) — decided in an ADR-063 amendment before code.

### Phase 5 — one entity identity

`SourceEntity.identity` derived from `model.identity.EntityId` (ADR-063
Phase 2's `entity_id`), so an L4 finding and an L2 finding about the same
declaration share an identity and can be joined rather than re-matched by
spelling. Owned by [One Semantic Pipeline](one-semantic-pipeline.md); this
plan only records that L4 is the remaining consumer.

### Phase 6 — one per-TU parse cache

A single content-addressed per-invocation store (key: the Phase 3 parsed
invocation + tool identity + input digests) that the header-AST cache and
`SourceAbiCache` both become views of. Depends on Phases 2–3; without a
shared invocation key a shared store would just be two key schemes in one
directory.

### Phase 7 — one worker-sizing and executor policy

`_tu_jobs`, `_l4_jobs` and `_call_graph_jobs` apply the same RAM-per-worker
and CPU clamps three times. Converge on one function in `process_resources`,
and decide the thread-vs-process default from measurement (see "Measurements"
below) rather than keeping `ABICHECK_L4_EXECUTOR` an opt-in forever.

### Phase 8 — diffing: assess before merging

`source_diff.diff_source_abi` compares L4-only facts (macro values, body
fingerprints, template patterns, ODR) that `diff_types`/`diff_symbols` have
no counterpart for — that part is not duplication. What *is* likely
duplicated is declaration/type comparison once Phase 4 makes both tiers
produce the same model; audit it then, with a list of each `ChangeKind`
either tier can emit for the same underlying change.

## Measurements

Recorded per phase on the same PVXS fixture (`libpvxs.so.1.5`, 34 TUs,
`-std=c++17` extraction config from PR 216, compile DB restricted to
`src/`), unprofiled, fresh caches (`XDG_CACHE_HOME` per run).
See the Phase 1 PR description for the before/after table.

## Out of scope

- The L2 DWARF cost. pyelftools' DIE decoding dominates it and is paid
  regardless of what the walk does; the lever there is decoding less
  (skipping CUs that define nothing reachable from the public surface), a
  separate design question.
- Replacing clang's JSON AST with a selective traversal — see
  [libclang selective AST traversal](libclang-selective-ast-traversal.md).
