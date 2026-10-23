# L2/L4/L5 extraction convergence — one parse per TU, one owner per primitive

**Status:** Proposed; Phases 1 and 2 implemented (the first two PRs); Phase 3 first slice implemented (the model and four clang/castxml consumers), and remaining slice 1 (the L2 header clang argv) implemented; slices 2–4 remain (see Phase 3).
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

### Phase 2 — L4 replay and L5 read the same dump (implemented)

The L4 `ClangSourceExtractor` and the L5 passes now read one
`clang -ast-dump=json` per TU, from one argv and one bounded runner. The
macro pass (`-E -dD`) stays a separate invocation.

What landed:

- **The prerequisite, first.** The L5 allowlist
  (`call_graph._safe_replay_flags_from_context`) is deleted. The one builder
  is `clang_command.build_clang_command`, which is L4's context argv: the
  union of what L4 needs. The L5 parsers produce identical edges under the old
  allowlisted argv and the union, on 253 catalog TUs and a synthetic two-TU
  project (`tests/test_tu_ast_shared_run.py`). That test restates the old
  allowlist as its reference and asserts both configurations ran to a tree
  and differ as argv.
- **The union carries no code-loading option.** A build pack is untrusted, and
  it can carry `-fplugin=` or `-Xclang -load`. `abi_flags.drop_code_loading_options`
  removes those forms from the final argv; nothing else is filtered, so a
  genuine build's argv is unchanged.
- **One runner.** `clang_ast_run.dump_tu_ast` runs the dump, spilled to a temp
  file as L4 always did, and returns a `TuAstDump` with a failure `stage`.
  Each reader maps the stage onto its own diagnostics: L4 keeps its
  `SourceExtractionError` text (`clang_dump_errors.py`), L5 its own.
- **L4 contributes to the same run.** `source_replay.run_source_replay(...,
  l5_run=...)` dumps each TU that is an L4 cache miss or in the L5 scope
  exactly once (`source_replay_shared.py`). L4 reads the raw tree first, the
  L5 passes then materialize locations in place (L4's sticky-file reading
  changes on a materialized tree, so the order is load-bearing), and the tree
  is released before the macro subprocess.
- **One driver.** `clang_command.ast_clang_binary` runs every AST dump under
  `clang++` when the default `clang` is given and `clang++` is on `PATH`, as the
  L5 passes always did. L4 output does not depend on the choice (checked below).

Deliberate differences, all from sharing one argv:

- L5 dumps now carry `-ferror-limit=0` (the L4 setting), so a TU with more
  than clang's default 20 errors yields its full recovered tree to L5 too.
- L5 dumps run in the effective build directory (`env -C`), as L4's always did.
- When both tiers run, a TU's dump is bounded by the L4 per-TU timeout
  (180 s by default), not the L5 120 s cap.

Verified:

- **L4 output is byte-identical**, so there is no `CLANG_EXTRACTOR_VERSION`
  bump. Every `SourceAbiTu` (declarations, types, inline bodies, macros, source
  edges, coverage, diagnostics) equals the pre-change output on 253 catalog
  TUs, under both `clang` and `clang++`.
- **Each TU is dumped once** when both tiers run; `test_l4_and_l5_share_one_dump_per_tu`
  counts the dumps, and `test_standalone_passes_dump_each_tu_again` shows the
  counter discriminates.

### Phase 3 — one compile-invocation model

Owned by the parent plan's P1 (`CompilerInvocation`/`SourceOperand`).
From this plan's side the acceptance criterion is concrete: `_argv.py`,
`call_graph._safe_replay_flags_from_context` and `dumper_ast_config`'s clang
argv all consume the one parsed invocation, and `_argv.py` shrinks to the
backend-specific rendering it genuinely owns (clang-cl driver mode,
castxml emulation).

**Status — first slice implemented.** `call_graph._safe_replay_flags_from_context`
is no longer a consumer: Phase 2 deleted it, so it is not in this slice's
acceptance. The model is `source_extractors/compile_invocation.py`:
`parse_compile_invocation()` reads a `CompileUnit` once into a frozen
`CompilerInvocation` (source, driver, forced language, standard, replay
directory, defines, rebased include dirs, sysroot, target, and the replayed
`CarriedFlag`s). Each `CarriedFlag` records whether the structured argv
suppresses it, which is the rule `replayed_tokens()` applies. The
replayed-flag scanner moved out of `_argv.py` into the model, and
`_argv.replay_extra_flags` is deleted (`_argv.py`: 1491 → 1293 lines).

Consumers now on the model: the L4/L5 AST-dump and macro argv
(`clang_command`), the castxml replay argv (`castxml`), the public-root
include probe (`clang_public_roots`), and the replay cache key
(`source_replay`).

Verified by a differential golden: `tests/test_compile_invocation_differential.py`
renders the clang and castxml argv for 16 units of a real compile database
(gcc, clang, clang-cl, icpx, `-I`/`-isystem`/`-iquote`/`-idirafter`, `-D`/`-U`,
`-include`, `-std` conflicts, `-x`, a response file, `env -C`, `env -i`,
sysroot/target, a code-loading option, SYCL) against the output recorded at
the pre-change commit. A mutation of the dedup rule fails it.

**Remaining slices**, each a separate PR:

1. **L2 header clang argv — implemented.** `_build_clang_header_command` is now a
   thin wrapper over `extract/headers/clang_header_argv.py`'s
   `HeaderCompileContext` and `render_clang_header_argv`, which
   `dumper_ast_config`'s re-export and the clang header backends reach through
   the same wrapper. Two decisions differ from the first draft of this slice.
   The renderer lives in `extract/`, not `buildsource/source_extractors/
   compile_invocation.py`: `extract` may import only `model` and `storage`
   (`architecture/modules.yaml`), and the header path has no `CompileUnit` for
   the model to parse. The header keeps its path-class include dedup
   (`drop_include_tokens_duplicating_paths`), not the token-identity rule
   `replayed_tokens` applies, because the two rules differ on purpose and the
   header inputs are already resolved, so there is no rebase to share. The
   golden (`clang_header_argv_golden.json`, 11 cases) was recorded at the
   pre-change commit and is asserted unchanged by
   `tests/test_compile_invocation_differential.py`.
2. **Typed include search for the public roots.** `clang_public_roots` still
   re-scans the replayed tokens for `-iquote`/`-I` operands. Carry the
   include-search entries as typed fields on the model and delete the rescan.
3. **Launcher and `env` parsing into the model.** `strip_launchers`,
   `pick_compiler_binary` and the `env` fold still live in `_argv.py` and are
   called from the model. Move the fold into `parse_compile_invocation`, so
   `_argv.py` keeps only the backend rendering the plan names.
4. **`SourceOperand` and `CompileAction`.** The parent plan's per-source
   `language`/`effective_output`/`action` and `link_inputs` are not in this
   slice. They need a multi-source corpus (`gcc -c a.c b.c`, `-x` switching
   between sources) before the model can carry them honestly.

### Phase 4 — one clang AST → declaration/type parser

Do for clang what `source_extractors/castxml.py` already does for castxml:
run L2's `dumper_clang._ClangAstParser` over the TU's AST and project through
`base.entity_from_*`, keeping L4's genuinely additive facts (body/default-arg/
template fingerprints, macros, source edges) as a layer over it. This changes
the L4 `SourceEntity` payload for clang-produced surfaces, so it needs a
`CLANG_EXTRACTOR_VERSION` bump (cache invalidation) and a stated
comparability rule for a stored pre-change L4 surface (the `fact_set`
mechanism, ADR-038 C.8) — decided in the
[ADR-063 amendment "L4 clang payload comparability"](../adr/063-one-semantic-pipeline.md#amendment-2026-10-21-accepted-l4-clang-payload-comparability)
(accepted) before code. That amendment fixes the rule: re-extract the old side
when its sources and compile commands are available; otherwise compare only
the facts both payloads carry, and report the lowered assurance through
`SOURCE_FACT_COVERAGE_INCOMPLETE`. Phase 4 implementation waits for Phase 3,
because re-extraction uses the one compile-invocation model.

**Status — Phase 4 measurement spike (measured; Phase 4 not implemented).**
`scripts/measure_l4_clang_projection.py` measures the candidate set ADR-063's
amendment "L4 clang payload comparability" (A3/A5) asks for. It dumps each of
the **253** catalog translation units (`catalog/cases/*/*.cpp`; the 235 `.c`
files are outside the amendment's C++ basis) once through `dump_for`, then builds
two entity sets from the same AST: the current L4 set from
`source_abi_from_dump`, and the projected set from `_ClangAstParser`'s
`parse_functions`/`parse_variables`/`parse_types`/`parse_enums`/
`parse_typedefs_qualified` through `base.entity_from_*`. Both sides are reduced to
the amendment's fields and paired per TU by `(kind, bare name)` in producer
order. Result: 253 TUs measured, 0 failed.

The spike depends on Phase 3 only through its first slice:
`compile_invocation.py` is on `main`, and the clang dump argv reads it through
`clang_command.build_clang_command`. The remaining Phase 3 slices are not
needed for the measurement. Phase 4 *implementation* still waits for Phase 3
under the plan's sequencing rule.

The projection configuration is the spike's own, not a production one:
`no_binary_evidence=True` (no export tables), `is_cxx=True`, no target triple,
the case directory as the public directory, and no public header paths. The
spike writes no L4 output, leaves `CLANG_EXTRACTOR_VERSION` at `"0.13"`, and
creates no `REVISION_COMPARABLE_FIELDS` set or golden. Those belong to Phase 4.

Per-(kind, field) result, 1531 paired functions, 290 records, 5 enums, 22
typedefs, 1 variable (`equal_empty` = both sides empty on every pair, so the
equality says nothing about the producer):

| kind | field | compared | both empty | differing pairs | status | TUs differing |
|---|---|---:|---:|---:|---|---:|
| function | qualified_name | 1531 | 0 | 1045 | differs | 205 |
| function | mangled_name | 1531 | 403 | 22 | differs | 18 |
| function | visibility | 1531 | 0 | 1531 | differs | 249 |
| function | api_relevant | 1531 | 0 | 1531 | differs | 249 |
| function | value | 1531 | 1513 | 18 | differs | 7 |
| function | signature_hash | 1531 | 0 | 1374 | differs | 247 |
| variable | qualified_name | 1 | 0 | 0 | equal (n=1) | 0 |
| variable | mangled_name | 1 | 0 | 1 | differs | 1 |
| variable | visibility | 1 | 0 | 1 | differs | 1 |
| variable | api_relevant | 1 | 0 | 1 | differs | 1 |
| variable | value | 1 | 1 | 0 | equal_empty | 0 |
| variable | type_hash | 1 | 0 | 0 | equal (n=1) | 0 |
| record | qualified_name | 290 | 0 | 130 | differs | 75 |
| record | mangled_name | 290 | 290 | 0 | equal_empty | 0 |
| record | visibility | 290 | 0 | 290 | differs | 168 |
| record | api_relevant | 290 | 0 | 290 | differs | 168 |
| record | type_hash | 290 | 0 | 290 | differs | 168 |
| enum | qualified_name | 5 | 0 | 5 | differs | 5 |
| enum | mangled_name | 5 | 5 | 0 | equal_empty | 0 |
| enum | visibility | 5 | 0 | 5 | differs | 5 |
| enum | api_relevant | 5 | 0 | 5 | differs | 5 |
| enum | type_hash | 5 | 0 | 5 | differs | 5 |
| typedef | qualified_name | 22 | 0 | 0 | **equal** | 0 |
| typedef | mangled_name | 22 | 22 | 0 | equal_empty | 0 |
| typedef | visibility | 22 | 0 | 0 | **equal** | 0 |
| typedef | api_relevant | 22 | 0 | 0 | **equal** | 0 |
| typedef | value | 22 | 0 | 0 | **equal** | 0 |
| typedef | type_hash | 22 | 0 | 22 | differs | 14 |

**Candidate set (A3's rule: a field that differs on any TU is dropped).**
Equal on every TU with evidence: `typedef.qualified_name`, `typedef.value`,
`typedef.visibility` and `typedef.api_relevant`. The last two are equal because
`base.entity_from_typedef` hard-codes `PUBLIC_HEADER`/`api_relevant=True`, and
the typedefs were in scope; they pin nothing about the projection. Equal only
because both sides are always empty (no evidence): `mangled_name` for `record`,
`enum` and `typedef`. Not established: every `variable` row, since the corpus
has one paired variable. Dropped (differs on at least one TU): every
`function` field, `record.{qualified_name, visibility, api_relevant, type_hash}`,
`enum.{qualified_name, visibility, api_relevant, type_hash}`,
`typedef.type_hash`, and `variable.{mangled_name, visibility, api_relevant}`.

**Explicit row: the mangled==name strip.** `entity_from_function` blanks
`mangled_name` when the L2 `Function.mangled` equals the bare name. Pair level:
2 functions differ this way, both `distance(Point, Point)` in
`case69_trivial_to_nontrivial` (`v1.cpp` and `v2.cpp`). The current side has
`_Z8distance5PointS_` and the projection has `""`, so identity falls back to
`qualified#signature_hash`. That is the fabricated-identity risk A1 names. The
pairing-free counters (`group_multiset_*`) show the blanking is wider:
projected in-scope functions have 502 blank mangled names against 404 on the
current side, and 545 of 1435 bare-name groups differ as multisets. The spike
does not trace why those counts differ, so do not read the 2 as the full count.

Presence (not a field): the projection walks every declaration, system headers
included. Function totals are 2600 current and 68629 projected, of which 66953
are outside the current side's names. Pairing excludes those and counts them.
In-scope paired counts: 1531 functions, 290 records, 5 enums, 22 typedefs,
1 variable.

**Limits of this measurement.** Pairing is by bare name plus producer order, so
overload order can cross two same-named overloads. The 20 non-empty
pair-level `mangled_name` differences are not separable from that and are
dropped with the field regardless. Equal-by-construction and equal-empty rows
are labelled as such, not counted as evidence. The gaps the table exposes
(origin unset, bare member and namespace names, unscoped output, hash spellings,
default-argument spelling) are recorded in
[`known-gaps.md`](../known-gaps.md) under "Clang projection"; Phase 4's
implementation cannot pin a field until those are closed or the field stays
excluded.

**Status — Phase 4 golden and pinned set (b1, measured; Phase 4 not implemented).**
b1 lands the pre-change golden and the measurement test that ADR-063's
amendment "L4 clang payload comparability" (test plan item 1) requires before
the producer switch. It changes no L4 code path or output, leaves
`CLANG_EXTRACTOR_VERSION` at `"0.13"`, adds no projection, no guard and no
`REVISION_COMPARABLE_FIELDS` in production code.

- **Golden.** `tests/golden/l4_clang/surface_0_13.jsonl`: a header line and one
  line per catalog C++ TU (253 TUs, 0 dump failures, 2921 current-side entities,
  about 1.0 MB). Each TU line carries its compile unit (the argv the spike uses)
  and the reduced view the measurement compares: each entity's kind and the
  candidate fields of that kind, in producer order. The full `SourceEntity`
  payload is not stored; the reduced view is exactly the input `compare_views`
  reads on the current side. Generator: `scripts/gen_l4_clang_golden.py`
  (`--check` regenerates in memory and fails on any difference). The header
  records clang `Ubuntu clang version 18.1.3 (1ubuntu1)`. Regenerating on a
  different clang may change the file; regenerate in a deliberate commit.
- **Golden drift test.** `test_golden_is_the_output_of_its_generator` regenerates
  the golden in-process and asserts it equals the checked-in file. The golden stores
  repository-relative paths only, so it is identical in any checkout (checked from a
  copy at another path).
- **Measurement test.** `tests/test_l4_clang_projection_golden.py` (`integration` and
  `slow`; skips without clang, and skips when the live clang differs from the version
  in the golden header). It reads the current side from the golden, computes the
  projection side live with the spike's configuration, compares per TU and per
  `(kind, field)`, and asserts the measured pinned set equals the literal
  `EXPECTED_PINNED`. The literal is written by hand from the amendment's candidate
  set and the rule A3 (a field that differs on any TU is dropped). It is not
  computed from the projection helper. A field is pinned only when its status is
  `equal` with at least two paired entities.
- **Measured pinned set.** `typedef.qualified_name`, `typedef.value`,
  `typedef.visibility`, `typedef.api_relevant`. The last two are equal by
  construction (`base.entity_from_typedef` hard-codes them), so they pin nothing
  about the projection; the rule admits them and b2 must decide whether to keep
  them. Equal only with no evidence (`equal_empty`, not pinned): `mangled_name`
  on `record`, `enum`, `typedef`, and `variable.value`. Not established
  (one paired variable): every `variable` row.

Per-(kind, field) table, the same run as the spike (`scripts/measure_l4_clang_projection.py --json`,
253 TUs, 0 failed; the golden reproduces it):

| kind | field | compared | both empty | differing pairs | status | TUs differing |
|---|---|---:|---:|---:|---|---:|
| function | qualified_name | 1531 | 0 | 1045 | differs | 205 |
| function | mangled_name | 1531 | 403 | 22 | differs | 18 |
| function | visibility | 1531 | 0 | 1531 | differs | 249 |
| function | api_relevant | 1531 | 0 | 1531 | differs | 249 |
| function | value | 1531 | 1513 | 18 | differs | 7 |
| function | signature_hash | 1531 | 0 | 1374 | differs | 247 |
| variable | qualified_name | 1 | 0 | 0 | equal (n=1, not established) | 0 |
| variable | mangled_name | 1 | 0 | 1 | differs | 1 |
| variable | visibility | 1 | 0 | 1 | differs | 1 |
| variable | api_relevant | 1 | 0 | 1 | differs | 1 |
| variable | value | 1 | 1 | 0 | equal_empty | 0 |
| variable | type_hash | 1 | 0 | 0 | equal (n=1, not established) | 0 |
| record | qualified_name | 290 | 0 | 130 | differs | 75 |
| record | mangled_name | 290 | 290 | 0 | equal_empty | 0 |
| record | visibility | 290 | 0 | 290 | differs | 168 |
| record | api_relevant | 290 | 0 | 290 | differs | 168 |
| record | type_hash | 290 | 0 | 290 | differs | 168 |
| enum | qualified_name | 5 | 0 | 5 | differs | 5 |
| enum | mangled_name | 5 | 5 | 0 | equal_empty | 0 |
| enum | visibility | 5 | 0 | 5 | differs | 5 |
| enum | api_relevant | 5 | 0 | 5 | differs | 5 |
| enum | type_hash | 5 | 0 | 5 | differs | 5 |
| typedef | qualified_name | 22 | 0 | 0 | equal | 0 |
| typedef | mangled_name | 22 | 22 | 0 | equal_empty | 0 |
| typedef | visibility | 22 | 0 | 0 | equal (by construction) | 0 |
| typedef | api_relevant | 22 | 0 | 0 | equal (by construction) | 0 |
| typedef | value | 22 | 0 | 0 | equal | 0 |
| typedef | type_hash | 22 | 0 | 22 | differs | 14 |

**Dropped fields** (differ on at least one TU, dropped for the whole revision
pair under A3): every `function` field, `record.{qualified_name, visibility,
api_relevant, type_hash}`, `enum.{qualified_name, visibility, api_relevant,
type_hash}`, `typedef.type_hash`, and `variable.{mangled_name, visibility,
api_relevant}`. The candidate `variable` fields are not established and stay out.

Presence (not a field, from the same run): current side 2600 functions, projected
68629, of which 66953 are out of scope and excluded from pairing. The
projected-only out-of-scope set is the public-surface problem below.

**What remains for b2** (a separate PR, after this one merges), in order:
1. **Status (2026-10-22): filter and production projection DONE; field gaps OPEN.**
   The projected side is in production code: `PublicSurfaceClangParser` and
   `project_clang_surface()` in `abicheck/buildsource/source_extractors/clang_projection.py`.
   The public-surface provenance filter runs on the declaration lists before any
   parse, so the 66953 out-of-scope declarations (for example `fclose`, `__uflow`,
   `asprintf`) no longer reach the projection; the golden test now builds its
   projected side through this code and its pinned set is unchanged. The four
   field-level gaps are not closed in this PR and their fields stay excluded:
   [Clang projection](../known-gaps.md#clang-projection-adr-063-phase-4-prerequisites)
   (origin unset, bare member and namespace names, unmangled `distance`, hash and
   default-argument spelling).
2. **Status (2026-10-23): A1–A4 guard DONE; pinned set decided.** A1 is
   `is_payload_revision_mismatch()` in
   `abicheck/buildsource/revision_comparability.py`. A3 is the early return in
   `diff_source_abi`: under a mismatch only the pinned typedef fields are in the
   intersection, and every other check is withheld. A4 is the
   `SOURCE_FACT_COVERAGE_INCOMPLETE` finding (compared-fact count, withheld
   checks, "withheld, not clean"). Its assurance already reads `unknown` through
   `policy/analysis_assurance/_fact_set.py`'s `producer_version_mismatch` branch,
   and a test pins it. The two equal-by-construction typedef fields are
   decided: `typedef.visibility` and `typedef.api_relevant` are **dropped** from
   `REVISION_COMPARABLE_FIELDS` (equality comes from `entity_from_typedef`'s
   hard-coding, so it measures nothing); `typedef.qualified_name` and
   `typedef.value` stay. Per-check decisions, from the amendment's A3 rule:

   | check (`source_diff.py` / `source_provenance_diff.py`) | under mismatch |
   |---|---|
   | `_diff_generated` | withheld: removal and value on unpinned fields |
   | `_diff_typedefs` | withheld: `type_hash` and removal are unpinned |
   | `_diff_macros` | withheld: value and removal, not pinned |
   | `_diff_concepts` | withheld: value/hash fallback chain, unpinned |
   | `_diff_declarations` | withheld: `value` and `signature_hash`, unpinned |
   | `_diff_inline_bodies` | withheld: body hash and removal keyed on mangled |
   | `_diff_templates` | withheld: body hash and removal |
   | `_diff_mappings` | withheld: symbol mapping keyed on mangled names |
   | `_diff_provenance` | withheld: mangled-name symbol match |
   | `_diff_odr` | withheld: keyed on `qualified_name` of unpinned records |
   | `_diff_fact_coverage` | replaced by the A4 finding for this case |

   Consequence, stated plainly: for a mismatched pair the pinned intersection
   produces no finding today, because no check reads `typedef.qualified_name`
   or `typedef.value` as a finding. The coverage finding reports the pinned
   count (for example `Compared 2 pinned facts (1 shared typedefs ...)`), so the
   run is never a clean pass. Growing the intersection needs a new pinned field
   first, which the measurement test has to confirm.
   A regression test (`tests/test_revision_comparability.py`) fails when the
   guard is disabled (mutation recorded in the PR body). The
   `_diff_provenance` block moved to `source_provenance_diff.py` to keep
   `source_diff.py` under its line budget (`debt.yaml` baseline 1113 → 1066).
3. The `CLANG_EXTRACTOR_VERSION` bump from `"0.13"` to `"0.14"`, last, once 1 and 2
   are in place. Re-run this measurement and the golden test in that PR; the
   expected set in `tests/test_l4_clang_projection_golden.py` changes there, not
   before.


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

### Phase 7 — one worker-sizing and executor policy (sizing implemented; executor default open)

`_tu_jobs`, `_l4_jobs` and `_call_graph_jobs` apply the same RAM-per-worker
and CPU clamps three times. Converge on one function in `process_resources`,
and decide the thread-vs-process default from measurement (see "Measurements"
below) rather than keeping `ABICHECK_L4_EXECUTOR` an opt-in forever.

**Status — sizing half implemented.** `process_resources.bounded_jobs` is the
one sizing rule (explicit override clamped to the oversubscription ceiling then
to the RAM cap; auto `min(units, cpus, 8)` reduced to the RAM cap; an
unparsable override is 1). Each caller keeps its own `ABICHECK_*_JOBS`
override, its per-worker RAM budget variables and constant, and its RAM probe
seam, so its thin wrapper is what tests patch. The call-graph wrapper keeps its
`n_units <= 1` short-circuit and its unit-count cap on explicit overrides.
`tests/test_bounded_jobs_equivalence.py` pins 61 rows against a hand-derived
oracle; the same table passes on the pre-convergence code. Log messages were
unified, and two edge cases changed (see the changelog fragment).

**Still open — the executor default.** `ABICHECK_L4_EXECUTOR` remains opt-in
(`thread` default). Choosing `process` as the default still needs the
measurement in "Measurements" on the PVXS fixture; it was deliberately not
changed in this phase.

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

The PVXS fixture is not in this repository, so Phase 2 is measured on the
C++ catalog instead: the 253 `catalog/cases/*/*.cpp` translation units, built
with `-std=c++17 -fPIC -fvisibility=hidden`, public roots at the catalog root.
One run per side, 4 CPUs, default concurrency. The process counts are exact;
the wall times are a single run and carry noise.

| Phase 2 (catalog, 253 TUs) | Before | After |
|---|---|---|
| clang AST-dump processes (`-ast-dump=json`) | 506 | 253 |
| clang macro processes (`-E -dD`) | 253 | 253 |
| Wall: L4 replay + L5 call/type/override/template/macro/callback passes | 103.1 s | 81.1 s |
| L5 call edges folded | 4,500 | 4,500 |

## Out of scope

- The L2 DWARF cost. pyelftools' DIE decoding dominates it and is paid
  regardless of what the walk does; the lever there is decoding less
  (skipping CUs that define nothing reachable from the public surface), a
  separate design question.
- Replacing clang's JSON AST with a selective traversal — see
  [libclang selective AST traversal](libclang-selective-ast-traversal.md).
