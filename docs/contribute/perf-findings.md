# Performance findings registry

Optimization candidates found by the performance instruments
(`scripts/perf_report.py`, `scripts/audit_repeated_calls.py --by-cost`, the
call-count and per-declaration gates; see [performance.md](performance.md))
and what was decided about each. Read this before re-investigating a hot spot
the weekly report surfaces: a candidate marked *accepted* has a reason, and a
*fixed* one has a gate that should catch it coming back.

## Open

None. Add a row here when an instrument surfaces a candidate nobody has
decided about yet.

## Fixed (guarded)

| Fix | Guard |
|---|---|
| `pattern_verdicts` type-name and pimpl-pointee scans were types × findings; now indexed (`_TypeNameIndex`, `_PimplPointeeIndex`) | `tests/test_compare_call_complexity.py`, `tests/test_pattern_verdicts_indices.py` |
| `_attribute_stdlib_embedding` re-scanned a record's fields once per field (O(width²)); now once per record | `wide_record_churn` workload in the call-count gate |
| Namespace-shape detectors demangled the public surface per detector; now once per snapshot via `detection_memo` | `repeats:*` budgets |
| `mask_operator_symbols`, `template_angle_depth`, `_strip_param_signature`, `_cpo_function_stem` recomputed per call; memoized | `repeats:*` budgets, H5 coverage rows |
| Legacy surface-fact fallback allocated a new `Fact` per read; now shared constants | `legacy_signature_churn` workload |
| Long-double pairing and public-surface scoping forked one `c++filt` per name on hosts without `cxxfilt` whenever no earlier batch had warmed those names (Mach-O spellings); now one batch each, the surface one only on first need | `tests/test_demangle_batching_consumers.py`; Mach-O-spelled corpus gates in `tests/test_extract_call_complexity.py` |
| `_admit` in `compare/surface_reconcile.py` rescanned the other surface per alias-resolved key (quadratic on Mach-O) | `tests/test_surface_reconciliation_properties.py::test_alias_resolved_keys_scan_the_other_surface_at_most_once` |
| The real-library call-count gate reused corpus names across sizes, so process-wide demangle memos made the small size look cheap (phantom superlinear sites on macOS); names are now salted per run | `tests/_cpp_corpus.py` `tag` |
| Proof-path de-dup in `internal_leak.py` and `_compute_occurrences` used list membership | `perf-antipatterns` baseline |
| `qualified_declaration_name` / `demangle_one_batched` ran ~13x per declaration on `add_remove`: each reconciliation slot rebuilt its alias index over the same full list, and six template detectors re-derived the same names. The alias index is shared per detector pass; detectors read names through a per-snapshot table (`template_surface.qualified_name_lookup`, comparison-memo backed, fetched once per loop) | `per_decl_x10:*` budgets (128 -> 63); `tests/test_qualified_name_table.py` |
| `_reconciled_function_surfaces` "~12 repeats" was a measurement artifact, not a repeat cost: `audit_repeated_calls.py --by-cost` apportioned cProfile's cumulative time by the repeated share of calls, charging a memoised wrapper's one real computation to its cache hits. `--by-cost` now times the repeat calls themselves | `scripts/audit_repeated_calls.py` `repeat_seconds` |
| `--pattern-verdicts` + `--surface-metrics` built each side's surface graph twice. `compare()` owns the graphs' lifetime explicitly: `surface_graph.shared_surface_graphs()` spans exactly those two stages, so nothing outlives them (a comparison-wide memo was rejected for its memory cost) | `repeats:_build_surface_graph` = 0 in every mode; `tests/test_surface_graph_sharing.py` |
| Same-named types in different namespaces (`Ctx (x8)` in the C++ corpus) were a correctness bug, three ways: findings were labelled with the bare name; impact attribution matched it as a substring, so `m3::Ctx`'s change listed `m0::Ctx`'s users; and `_dedup_exact` dropped the second of two namesakes that changed identically (a lost break). Findings on an ambiguous bare name are now relabelled with the qualified spelling of their own `entity_id` (after suppression, so bare-name rules match as before), attribution resolves bare mentions by C++ unqualified lookup from the function's or record's scope, and the exact-dedup key carries the qualified identity for those entities only. Unique bare names are unchanged | `tests/test_type_symbol_disambiguation.py` (generated namespace layouts; oracle = placement) |
| `_is_boundary_char` growth on the macOS call-count gate. The mechanism was never confirmed, so it was removed rather than explained: `finditer_allow_nested` stepped `endpos` down one character per probe and boundary-checked every rejected candidate, so its cost grew with how many registered spellings are prefixes of a longer token (`Data1` of `Data17`). It now jumps straight to the last token break, which is the only place a shorter valid match can end; the result is identical and the cost no longer depends on name overlap, whatever castxml emits on macOS | `tests/test_spelling_nested_same_offset.py::test_cost_does_not_grow_with_prefix_overlap` (fails on the previous form) |
| The 46 baselined `perf-antipatterns` sites were triaged: 23 fixed (list-as-set de-duplication, `list.index` ranking, loop-invariant `sorted`, a char-by-char string build, a per-call `re.compile` fallback), 17 inherent ones exempted in place with a `# perf-ok: <reason>` comment (JSON-lines parsing, `deepcopy` for isolation or inside `__deepcopy__`), and 6 were linter false positives now not flagged (a string rebound unconditionally each iteration; a module-scope comprehension). The baseline is empty | `perf-antipatterns` (empty baseline); `tests/test_perf_antipatterns.py` exemption cases |
| `qualified_name_normalization.segments` re-segmented the same declaration list per removed experimental item (3.2 M calls over 17 k names): `_findings_for` builds both `_signature_decls` sets once; `segments` and `_scope_path_of` are memoized | `tests/test_experimental_promotion.py` (unchanged answers) |
| Load-time `_strip_anonymous_type_locations` walked every string field (8-12 M calls) even when no raw `(lambda at <path>)` spelling existed; now gated on `has_anonymous_type_location` over the already-collected strings | `tests/test_anonymous_type_location_predicate.py` (predicate false => strip is identity) |
| `reclassify.resolve_kind_sets` copied the mutable `*_KINDS` sets per call (685 k calls, 11 inputs); `policy_kind_sets` memoized per policy | policy test suites |
| Closure renumbering handed `SemanticIR.occurrences` (a `FrozenMapping`) to the unpruned walk whole whenever any occurrence held a marker: 2.0 M nodes to rewrite ~120 entries. The pruned walk now descends any non-dict mapping entry by entry | `tests/test_closure_identity_pruned_rewrite.py::test_semantic_ir_occurrences_are_pruned_per_entry` (fails on the previous form) |
| DWARF calling-convention extraction built a pyelftools DIE per formal parameter to read one attribute; `DW_AT_type` is now read from the raw unit bytes and the by-value trait memoized per referenced type (advanced pass 3.8 s -> 2.3 s) | `tests/test_dwarf_subtree_index.py::test_raw_parameter_type_refs_equal_the_decoded_attribute` (two CUs, every producer/DWARF version) |
| Fingerprint rename matching ran the name predicate on every same-size (removed, added) pair (4.4 M calls on a template-heavy library); size buckets are indexed by blocking keys the predicate provably requires | `tests/test_rename_blocking_keys.py` (key contract + keyed == unkeyed) |
| `AbiSnapshot.canonical_ir` built a fresh `SemanticIR` per access, so every identity-keyed memo over it missed (`_project` x24, `canonical_entities` x57 per compare); the view and the reduction are now cached on the IR. The detection memo spans the whole comparison, `declared_special_members` is built once per snapshot, the opaque by-value scan visits each spelling once, `strip_template_args` is memoized (compare 12.8 s -> 9.0 s) | call-count / cost-budget gates |
| `compare` resolved two live binaries one after the other (and two threads would not overlap under the GIL); `balanced` now resolves them in two concurrent forked children (cold 60-module compare 64 s -> 42 s, ~1.5x peak) | `tests/test_performance_profile.py`, `tests/test_side_isolation.py` |

## Measured, not changed

- **DWARF low-memory threshold.** Freeing each CU's DIE cache was faster *and* lower-peak at every size measured, so the default threshold is now 0 (`ABICHECK_DWARF_LOW_MEMORY_MB=-1` restores retention).
- **Skipping childless, unvisited DIEs in the DWARF metadata walk** made that pass 3.6x faster in isolation but saved nothing end to end: the advanced pass shares the CU's DIE cache and needs the same children (method declarations for triviality, members for packing), so the decoding only moved. Not shipped.
- **Header-graph clang parse under the castxml frontend.** It needs clang's AST (call/type edges castxml does not produce) and already overlaps the primary dump; on the fixtures measured it finished within 0.3 s of the attach.
- **"SYCL headers kept" 0:59 -> 3:20.** Not reproduced with libstdc++ (`<bits/stdc++.h>`) kept on 556b8783f vs fbcf75253 vs HEAD (HEAD fastest, same peak); the regression is in a DPC++/`-fsycl`-specific path that needs `icpx` and SYCL headers to reproduce.
