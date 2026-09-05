# `catalog/` — the calibration catalog

This is the calibration and compatibility-knowledge tree (Phase 4 of the [examples/catalog split](../docs/contribute/plans/examples-catalog-split.md)) -- not the curated, task-oriented tree. See [`../examples/README.md`](../examples/README.md) for that.

Everything below is calibration material: one case per compatibility
mechanism, driving the FP-rate, tier-accuracy, mutation, and full-catalog
gates. It is an encyclopedia of ABI pitfalls, not a tutorial — the published,
navigable version is the
[Compatibility Catalog](../docs/reference/examples/index.md), which indexes
the same cases by rule, scenario kind, ecosystem, operation, evidence level,
language, and verdict.

<!-- BEGIN GENERATED: catalog-headline (keep counts in sync with examples/ground_truth.json) -->
This directory contains **197 cases** (192 single-library + 5 multi-library bundle cases, the latter tracked under [ADR-023](../docs/contribute/adr/023-bundle-aware-multi-binary-analysis.md)) demonstrating real-world ABI/API break scenarios. Most cases are a minimal, compilable C/C++ example with:
<!-- END GENERATED: catalog-headline -->

- Paired `v1/` and `v2/` source + headers.
- A consumer `app.c` / `app.cpp` that demonstrates the actual failure at runtime.
- A per-case `README.md` explaining what breaks and why.

A minority of cases ship a committed fixture instead of a compilable `v1`/`v2`
pair, by design — see `catalog/CLAUDE.md`: the G20 audit/cross-source corpus
(cases 143–151, a `snapshot.abi.json`), and one L5 source-graph case,
[case190](cases/case190_public_inline_function_references_internal_constant/README.md)
(a hand-built `old.json`/`new.json` evidence pack, not a compiled program —
its README explains why no compiled example can currently prove it live).
Its siblings 187/188/189/191 are real compiled `v1`/`v2` pairs like the rest
of the catalog. Each fixture-only case's own README says so explicitly;
check `ground_truth.json`/the per-case README before assuming a `v1`/`v2`
source pair exists.

The catalog drives abicheck's benchmark and serves as an encyclopedia of ABI pitfalls. For conceptual background on what ABI stability means and how to reason about it, see [ABI/API Compatibility](../docs/learn/abi-api-handling.md).

> **Authoritative expected verdicts for benchmarking** live in [`ground_truth.json`](ground_truth.json).
> If a per-case README and `ground_truth.json` disagree, `ground_truth.json` is the source of truth.

---

## Verdict distribution

<!-- BEGIN GENERATED: verdict-distribution (keep counts in sync with examples/ground_truth.json) -->
| Verdict | Count | `checker_policy.py` set | Icon |
|---------|-------|-------------------------|------|
| BREAKING | 107 | `BREAKING_KINDS` | 🔴 |
| API_BREAK | 17 | `API_BREAK_KINDS` | 🟠 |
| COMPATIBLE_WITH_RISK | 31 | `RISK_KINDS` | 🟡 |
| COMPATIBLE (addition) | 9 | `ADDITION_KINDS` | 🟢 |
| COMPATIBLE (quality) | 21 | `QUALITY_KINDS` | 🟡 |
| NO_CHANGE | 7 | — | ✅ |
| Bundle (multi-binary) | 5 | see [ADR-023](../docs/contribute/adr/023-bundle-aware-multi-binary-analysis.md) | 🔵 |
<!-- END GENERATED: verdict-distribution -->

> **Verdict source of truth:** [`ground_truth.json`](ground_truth.json), which aligns with the 5-tier classification in [`abicheck/checker_policy.py`](../abicheck/checker_policy.py): `BREAKING_KINDS` → `API_BREAK_KINDS` → `RISK_KINDS` → `QUALITY_KINDS` → `ADDITION_KINDS`.

**Severity labels used in "Real Failure Demo" sections:**

- 🔴 **CRITICAL** — causes crash, wrong output, or silent data corruption
- 🟡 **INFORMATIONAL** — no immediate breakage; compromises future-proofing
- 🟡 **BAD PRACTICE** — library works today but mismanages the ABI contract
- ✅ **BASELINE** — no change; expected passing state

Some policy-escalated source/contract breaks (notably case30, case95, case109 — each an underlying API_BREAK fact escalated to BREAKING by default policy; see each case's `policy_note` in `ground_truth.json`) may keep identical runtime output for prebuilt binaries. For those, the demo shows: (1) binary still runs, and (2) recompilation against new headers fails or changes allowed behavior.

## Runtime Demos vs. abicheck Analysis

Each per-case README describes the intended ABI/API contract break, but there
are two distinct validation layers:

- **Runtime smoke:** build the old consumer app, run it with `libv1`, then
  substitute `libv2` under the old library name. This catches loader failures,
  crashes, and visible output changes without using abicheck analysis.
- **abicheck analysis:** build v1/v2 libraries and run `dump` + `compare` with a
  selected evidence mode.

The runtime smoke result is not always the same as the policy verdict. Some
examples are deliberately analysis-only: source/API breaks, bad-practice
contract cases, and evidence-limited cases may keep the old binary running while
still being valid `BREAKING`, `API_BREAK`, or `COMPATIBLE_WITH_RISK` examples.
When a case is runtime-observable, its README should explain the concrete
loader/runtime/output failure. When it is not runtime-observable, its README
should explain which analysis layer proves the issue instead.

The standard analysis modes are:

- `debug-headers`: debug binary + public headers (`L0,L1,L2`)
- `release-headers`: stock/release binary + public headers (`L0,L2`)
- `stripped-headers`: stripped binary + public headers (`L0,L2`)
- `build-source`: stock binary + headers + build/source evidence pack
  (`L0,L1,L2,L3,L4,L5`)

## Current Validation Status

`Examples Validation` is the CI workflow for this catalog. It runs on changes
that touch `examples/**`, `abicheck/**`, or the validate-example harness files.
Commands below use `PYTHONPATH=.`.

| Check | Command | Executed where | Scope | Result | Status |
|---|---|---|---:|---|---|
| Build/autodiscovery | `python -m pytest tests/test_example_autodiscovery.py -v --tb=short -m integration` | CI Linux, gcc/clang | 209 integration items | gcc: 149 passed / 55 skipped / 5 xfailed; clang: 149 passed / 54 skipped / 6 xfailed | Green default single-library build lane. `case115_bit_int_width_changed` needs a `_BitInt`-capable CastXML-bundled Clang; a sandbox with an older bundled Clang (unrelated to the fix in this catalog) sees it fail there instead of building — see `docs/contribute/examples-validation-runbook.md` |
| Full example proof matrix | `validation/scripts/collect_full_example_matrix.py` over CI artifacts + dedicated bundle/G20/L3-L5/BTF proofs | CI aggregation | 197 catalog cases | 197/197 COVERED; 196 direct; 0 FAILED / 0 UNRESOLVED | Canonical full-catalog status; a lane-local `SKIP` is accepted only when a dedicated proof covers that case |
| Default/debug verdicts | `PYTHONPATH=. python tests/validate_examples.py --toolchain {gcc,clang} --json` | CI Linux, gcc/clang | 197 catalog cases | gcc: 153 PASS / 5 XFAIL / 39 SKIP; clang: 153 PASS / 6 XFAIL / 38 SKIP | Green default/debug verdict lane |
| Runtime smoke | `PYTHONPATH=. python validation/scripts/run_example_runtime_smoke.py --json` | Linux proof run | 197 catalog cases | 88 DEMONSTRATED / 69 NO_RUNTIME_SIGNAL / 1 BASELINE_SIGNAL / 39 SKIP | Passing; no BUILD_ERROR. The runner now compares each app's baseline exit code against a per-case `runtime_baseline_exit` in `ground_truth.json` (default 0) instead of hardcoding zero, so apps that deliberately return a computed value (e.g. case111's `ets(42).local()` returning `42`) are no longer misread as a broken baseline. `case06_visibility` is the one remaining, intentionally-unwhitelisted case — see "Known validation gaps" below |
| Release headers | `python tests/validate_examples.py --artifact-variant release-headers --json` | CI Linux artifact | 197 catalog cases | 146 PASS / 5 XFAIL / 46 SKIP | Informational; the false-risk regression on `case61_var_added` (`exported_object_alignment_reduced`) is fixed — CastXML now resolves a variable's natural type alignment as declared-alignment corroboration even without an explicit `alignas` override |
| Stripped headers | `python tests/validate_examples.py --artifact-variant stripped-headers --json` | CI Linux artifact | 197 catalog cases | 141 PASS / 5 FAIL / 5 XFAIL / 46 SKIP | Informational; reduced-evidence signal-loss backlog (below) |
| Build/source proof | `python tests/validate_examples.py case01 case04 case98 case105 case122 case129 case130 case131 case132 case133 --artifact-variant build-source --json` | CI Linux artifact | 10 representative cases | 10 PASS | Required release proof; includes L3 C++ floor and L4 concept/template regressions. Not full L3-L5 catalog coverage — see "Known validation gaps" |

Counts above are from the most recent full catalog run this table was refreshed against; re-run
the `Examples Validation` workflow and update this table whenever the catalog size or a lane's
pass/fail/skip mix changes, so it doesn't silently drift out of sync with `ground_truth.json`
the way it previously did (stale at a 169-case catalog for several releases).

### Known validation gaps

- **`expected_kinds`/`expected_absent_kinds` are checked but not yet blocking, and every
  case that ever mismatched has now been individually triaged.** `tests/validate_examples.py`
  parses the real compare output's change kinds and checks them against
  `expected_kinds`/`expected_absent_kinds`, surfaced per-case as `kinds_strict` — previously only
  the final verdict string was asserted, so a case could PASS with the right severity for the
  wrong detector reason. A prior full-catalog run found 19 such cases; each was independently
  re-derived (per-case: is the expected kind semantically correct, is the fixture actually
  constructed to trigger it, is a generic detected kind sufficient, is this a detector bug, a
  fixture bug, or a metadata bug) rather than blanket-implementing 19 detectors:
  - **6 were metadata bugs** — `expected_kinds` named the wrong (or an overly specific) kind for
    what the fixture actually demonstrates; corrected in `ground_truth.json`:
    `case65_symbol_version_removed` (`symbol_version_defined_removed` → `symbol_version_node_removed`,
    the two are deliberately deduplicated to the more specific kind), `case77_detail_templated_base_changed`
    (→ `internal_template_leaks_via_public_api`, the templated sibling of the non-template kind),
    `case80_pimpl_shared_to_unique` (→ `typedef_base_changed` + `struct_size_changed`; its README's
    claim that `type_field_type_changed` fires on `impl_` was factually wrong), `case94_empty_tag_gained_state`
    (→ `type_field_added_compatible`, the correct classification for a non-polymorphic field append),
    `case126_sycl_device_impl_ptr` (→ `struct_size_changed`, the DWARF-side kind that actually proves
    the break; the AST-side `type_size_changed` correctly declines given null layout evidence), and
    `case23_pure_virtual_added` (→ `func_virtual_became_pure`, not `func_pure_virtual_added` — the
    fixture's method was already virtual, not newly added).
  - **1 was a fixture bug**, fixed in source: `case23_pure_virtual_added`'s v2 library defined no
    out-of-line `Processor::process()` body at all, so v2's binary had no symbol to pair against
    v1's for the pure-virtual-transition comparison; added a body (legal for a pure virtual function,
    reachable only via an explicit qualified call, so it changes no runtime behavior) so the two
    versions' `Processor::process()` can be compared.
  - **Genuine, root-caused detector gaps** are documented in `ground_truth.json` via a
    `known_kind_gap` (the specific missing/wrong kind) + `known_kind_gap_note` (the grounded root
    cause, cited to file:line) on each case, and surfaced by `validate_examples.py`'s `kinds_strict`
    as `documented-mismatch` (a triaged, tracked gap) rather than a bare `mismatch` (an unexplained
    one). A prior pass triaged 13 such cases (`case06_visibility`, `case39_var_const`,
    `case66_language_linkage_changed`, `case72_covariant_return_changed`,
    `case74_detail_base_class_changed`, `case75_detail_embedded_by_value`,
    `case76_detail_pimpl_vtable_changed`, `case79_missing_template_instantiation`,
    `case82_sycl_overload_set_removed`, `case87_default_template_arg_changed`,
    `case88_cpo_kind_changed`, `case116_atomic_qualifier_changed`,
    `case141_versioned_symbol_scheme`); the CastXML schema-completeness/Clang-parity work in PR #582
    subsequently closed all but one of those — `ground_truth.json` is the live count (grep it for
    `known_kind_gap` rather than trusting a number here), and as of this writing
    `case66_language_linkage_changed` is the only case still carrying one: castxml parses its v2
    header in C mode (the header alone gives no C++ signal) and its pseudo-Itanium `mangled` guess
    for a C-mode Variable/Function doesn't match either side's real export — see the case's
    `known_kind_gap_note` for the full root cause. The verdict is still correct via a generic
    `func_removed`; only the dedicated `func_language_linkage_changed` kind is missing.
  - `case59_func_became_inline` and (clang-only) `case115_bit_int_width_changed` no longer/don't
    reproduce as kind mismatches in this pass (the former is fixed upstream; the latter is a
    local castxml/`_BitInt` parsing limitation in this environment, not evaluated) — case membership
    here is toolchain-sensitive, so treat the 13 above as the latest triaged set, not a fixed list.
  - Setting `ABICHECK_STRICT_KINDS=1` (see `tests/check_validate_results.py`) makes any *new*,
    untriaged `mismatch` blocking while leaving the 13 documented `documented-mismatch` gaps
    non-blocking — implementing fixes for those 13 remains future work, not something this pass
    attempted (per the guidance above: triage first, then decide case by case whether a fix is
    warranted). The full example matrix (`validation/scripts/collect_full_example_matrix.py`)
    surfaces both `kind_mismatch_cases` (untriaged) and `documented_kind_gap_cases` (triaged)
    separately at the matrix level.
- **Verdicts are matched exactly — there is no `API_BREAK`/`COMPATIBLE` normalization.**
  `tests/validate_examples.py` compares the actual verdict to `expected` verbatim; an
  earlier `_normalize_verdict` helper that treated the two as equivalent has been removed.
  The one declared escape hatch is a case-level `known_gap`: it only turns a verdict
  mismatch into `XFAIL` (not a silent PASS) when `ground_truth.json` explicitly records
  the gap, and the full example matrix additionally requires a case's own `source_smoke`
  oracle to have proven the canonical verdict before crediting it as `COVERED` — see
  `case111_enumerable_thread_specific_lambda_ambiguity`, the catalog's one case covered
  this way instead of by a direct detector/CLI match (`docs/contribute/examples-validation-runbook.md`).
- **Build/source coverage is a 10-case lane, not every L3/L4/L5 catalog entry —**
  **but it is every entry that lane *can* prove.** `--artifact-variant build-source`
  needs a real compilable `v1`/`v2` pair; of the catalog's L3/L4/L5 cases, only 7
  are `single-library`-owned (`case98`, `case105`, `case122`, `case130`-`case133`)
  and all 7 are in `BUILD_SOURCE_PROOF_CASES` (plus `case01`/`case04`/`case129` as
  L0/L1 regression smoke for the variant itself). The other 15 L3-L5 cases are
  `g20`/`l3l4l5`/`reconcile`-owned: they ship committed snapshot fixtures instead
  of compilable sources by design (some, like `case160`-`162`'s L5 source-graph
  deltas, can't be derived deterministically from a real build) and are proven by
  their own dedicated fixture tests (`test_g20_catalog.py`, `test_l3l4l5_examples.py`,
  `test_diff_reconcile.py`) — see the full example matrix's `SPECIAL_PROOFS`.
  `test_review_comment_regressions.py::test_build_source_proof_cases_cover_every_l3plus_single_library_case`
  gates this so a newly-added `single-library` L3+ case can't silently miss the smoke.
- **`case06_visibility`'s runtime baseline is intentionally left unwhitelisted.**
  Its `app.c` doesn't fit the runtime-smoke harness's baseline-then-swap model
  — it `dlopen`s both `./libv1.so` and `./libv2.so` by name in a single run,
  and its exit code 1 is overloaded: it fires both for the intended
  demonstration (v2 correctly hides `internal_helper`) *and* for a real,
  unrelated regression (v1 unexpectedly failing to export it). A single
  `runtime_baseline_exit` value can't distinguish those two conditions, so
  whitelisting exit 1 would mask the second one — see the case's README for
  the full explanation. It stays `BASELINE_SIGNAL`, which per policy is
  visible but not CI-blocking.
- **11 cases regressed to a macOS-only `NO_CHANGE`/wrong-verdict result starting at commit
  `71b4f624e2b10d53ee662b555907280baad0982a` (PR #555), Linux (gcc and clang) unaffected:**
  `case22_method_const_changed`, `case47_inline_to_outlined`, `case71_inline_namespace_moved`,
  `case82_sycl_overload_set_removed`, `case85_internal_template_signature_changed`,
  `case88_cpo_kind_changed`, `case99_experimental_graduated`,
  `case100_experimental_removed_without_replacement`, `case101_inline_namespace_version_bumped`,
  `case110_concurrent_unordered_map_api_drift`, `case166_ref_qualifier_added`. Investigated at
  length (CI history bisection to the exact commit, a full review of that PR's diff, and
  Linux+clang reproduction attempts under both the narrow and full integration test suites)
  without finding a mechanism that survives scrutiny against the actual code paths involved —
  see each case's `known_gap` in `ground_truth.json` for the investigation notes. Diagnosing
  further needs a real macOS shell to inspect the actual dump output, which wasn't available;
  marked `known_gap_platforms: ["macos"]` (XFAIL, not silently skipped) to unblock CI rather
  than guess at a fix. Root-causing and re-tightening these is tracked as follow-up work.

Default/debug skips are not accepted as green coverage. They are cases outside
the default single-library debug lane: G20 audit/cross-source snapshots, L3/L4/L5
build/source-only fixtures, bundle/release cases, BTF, or host feature gaps. The
catalog keeps them in `ground_truth.json`, and dedicated tests cover those
families.

The repository-wide completion gate is not an individual row above. Follow the
[full example validation runbook](../docs/contribute/examples-validation-runbook.md)
to aggregate compiler, runtime, bundle, and dedicated proof artifacts. Full
success means one `COVERED` row per current ground-truth entry, with no
`UNRESOLVED` or `FAILED` rows. Trusted source-smoke fixtures require the explicit
`ABICHECK_TRUSTED_SOURCE_SMOKE_RUN=1` opt-in documented there.

Current stripped-header signal-loss cases: `case103_toolchain_flag_drift`,
`case117_no_unique_address`, `case129_struct_return_convention`,
`case60_base_class_position_changed`, and `case69_trivial_to_nontrivial`.

Release and stripped full-catalog lanes remain reported-only plus false-positive
guarded. The fixed ten-case build/source proof is blocking. A complete
build/source run over every applicable L3-L5 case remains an extended/manual
validation path because source replay is expensive.

Recent build/source and ABI-mode examples:

| Case | Default/debug | Release | Stripped | Build/source |
|---|---|---|---|---|
| `case129_struct_return_convention` | PASS (`BREAKING`) | PASS (`BREAKING`) | FAIL (`COMPATIBLE`) | PASS (`BREAKING`) |
| `case130_exceptions_mode_flip` | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) |
| `case131_rtti_mode_flip` | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) |
| `case132_threadsafe_statics_flip` | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) |
| `case133_tls_model_flip` | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) |

Current mode-specific backlog: stripped headers under-classifies
`case103_toolchain_flag_drift`, `case117_no_unique_address`,
`case129_struct_return_convention`, `case60_base_class_position_changed`, and
`case69_trivial_to_nontrivial`; default/debug and release-header modes
classify those catalog cases correctly.

Expected non-pass buckets are already represented in `ground_truth.json`:

- XFAIL: `case105`, `case111`, `case122`, `case64`, `case98` (gcc); additionally
  `case103`, `case180` (clang only) — each carries a `known_gap` explaining why
  debug-headers can't reach the canonical verdict. case105/case122/case98 are
  the catalog's flagship examples of a *higher* evidence tier (L3/L4) closing
  the gap; case111 is the flagship example of the opposite case — a scenario
  proven true by its own `source_smoke` oracle with **no** evidence tier that
  currently catches it (a genuine, unfixed detector gap, not an evidence-depth
  limitation).
- SKIP: `case115`, `case121`, and bundle cases `case84`, `case90`, `case91`,
  `case92`, `case93`

---

## Case index

<!-- BEGIN GENERATED: case-index (scripts/gen_examples_docs.py --readme) -->
| # | Case | Category | abicheck verdict |
|---|------|----------|-----------------|
| [01](cases/case01_symbol_removal/README.md) | Symbol Removal | Breaking | 🔴 BREAKING |
| [02](cases/case02_param_type_change/README.md) | Parameter Type Change | Breaking | 🔴 BREAKING |
| [03](cases/case03_compat_addition/README.md) | Compatible Addition (New Export) | Addition | 🟢 COMPATIBLE |
| [04](cases/case04_no_change/README.md) | No Change | No Change | ✅ NO_CHANGE |
| [05](cases/case05_soname/README.md) | Missing SONAME | Quality | 🟢 COMPATIBLE (bad practice) |
| [06](cases/case06_visibility/README.md) | Symbol Visibility Leak | Breaking | 🔴 BREAKING (bad practice) |
| [07](cases/case07_struct_layout/README.md) | Struct Layout Change | Breaking | 🔴 BREAKING |
| [08](cases/case08_enum_value_change/README.md) | Enum Value Change | Breaking | 🔴 BREAKING |
| [09](cases/case09_cpp_vtable/README.md) | C++ Vtable Change | Breaking | 🔴 BREAKING |
| [10](cases/case10_return_type/README.md) | Return Type Change | Breaking | 🔴 BREAKING |
| [11](cases/case11_global_var_type/README.md) | Global Variable Type Change | Breaking | 🔴 BREAKING |
| [12](cases/case12_function_removed/README.md) | Function Removed from Shared Library | Breaking | 🔴 BREAKING |
| [13](cases/case13_symbol_versioning/README.md) | Symbol Versioning Script | Quality | 🟢 COMPATIBLE |
| [14](cases/case14_cpp_class_size/README.md) | C++ Class Size Change | Breaking | 🔴 BREAKING |
| [15](cases/case15_noexcept_change/README.md) | `noexcept` Removed | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [16](cases/case16_inline_to_non_inline/README.md) | Inline → Non-inline (ODR / Symbol Appearance) | Addition | 🟢 COMPATIBLE |
| [17](cases/case17_template_abi/README.md) | Template Instantiation ABI Change | Breaking | 🔴 BREAKING |
| [18](cases/case18_dependency_leak/README.md) | Dependency ABI Leak | Breaking | 🔴 BREAKING (bad practice) |
| [19](cases/case19_enum_member_removed/README.md) | Enum Member Removed | Breaking | 🔴 BREAKING |
| [20](cases/case20_enum_member_value_changed/README.md) | Enum Member Value Changed | Breaking | 🔴 BREAKING |
| [21](cases/case21_method_became_static/README.md) | Method Became Static | Breaking | 🔴 BREAKING |
| [22](cases/case22_method_const_changed/README.md) | Method Const Qualifier Changed | Breaking | 🔴 BREAKING |
| [23](cases/case23_pure_virtual_added/README.md) | Virtual Method Became Pure Virtual | Breaking | 🔴 BREAKING |
| [24](cases/case24_union_field_removed/README.md) | Union Field Removed | Breaking | 🔴 BREAKING |
| [25](cases/case25_enum_member_added/README.md) | Enum Member Added | Addition | 🟢 COMPATIBLE |
| [26](cases/case26_union_field_added/README.md) | Union Field Added (Size Grows) | Breaking | 🔴 BREAKING |
| [26b](cases/case26b_union_field_added_compatible/README.md) | Union Field Added (No Size Change) | Addition | 🟢 COMPATIBLE |
| [27](cases/case27_symbol_binding_weakened/README.md) | Symbol Binding Weakened (GLOBAL → WEAK) | Quality | 🟢 COMPATIBLE |
| [28](cases/case28_typedef_opaque/README.md) | Typedef and Opaque Type Changes | Breaking | 🔴 BREAKING |
| [29](cases/case29_ifunc_transition/README.md) | GNU IFUNC Transition | Quality | 🟢 COMPATIBLE |
| [30](cases/case30_field_qualifiers/README.md) | Field Qualifier Changes (const, volatile) | Breaking | 🔴 BREAKING |
| [31](cases/case31_enum_rename/README.md) | Enum Member Rename | API Break | 🟠 API_BREAK |
| [32](cases/case32_param_defaults/README.md) | Parameter Default Value Changes (C++) | API Break | 🟠 API_BREAK |
| [33](cases/case33_pointer_level/README.md) | Pointer Level Change | Breaking | 🔴 BREAKING |
| [34](cases/case34_access_level/README.md) | Access Level Changed | API Break | 🟠 API_BREAK |
| [35](cases/case35_field_rename/README.md) | Field Rename | API Break | 🟠 API_BREAK |
| [36](cases/case36_anon_struct/README.md) | Anonymous Struct/Union Change | Breaking | 🔴 BREAKING |
| [37](cases/case37_base_class/README.md) | Base Class Changes | Breaking | 🔴 BREAKING |
| [38](cases/case38_virtual_methods/README.md) | Virtual Method Changes | Breaking | 🔴 BREAKING |
| [39](cases/case39_var_const/README.md) | Variable Const Change | Breaking | 🔴 BREAKING |
| [40](cases/case40_field_layout/README.md) | Field Layout Changes | Breaking | 🔴 BREAKING |
| [41](cases/case41_type_changes/README.md) | Type-Level Changes | Breaking | 🔴 BREAKING |
| [42](cases/case42_type_alignment_changed/README.md) | Type Alignment Changed (standalone alignas) | Breaking | 🔴 BREAKING |
| [43](cases/case43_base_class_member_added/README.md) | Base Class Member Added | Breaking | 🔴 BREAKING |
| [44](cases/case44_cyclic_type_member_added/README.md) | Cyclic Type Member Added | Breaking | 🔴 BREAKING |
| [45](cases/case45_multi_dim_array_change/README.md) | Multi-Dimensional Array Element Type Change | Breaking | 🔴 BREAKING |
| [46](cases/case46_pointer_chain_type_change/README.md) | Pointer Chain Type Change | Breaking | 🔴 BREAKING |
| [47](cases/case47_inline_to_outlined/README.md) | Inline Method Moved Out-of-Line | Addition | 🟢 COMPATIBLE |
| [48](cases/case48_leaf_struct_through_pointer/README.md) | Leaf Struct Change Propagated Through Pointer | Breaking | 🔴 BREAKING |
| [49](cases/case49_executable_stack/README.md) | Executable Stack (GNU_STACK RWX) | Quality | 🟢 COMPATIBLE (bad practice) |
| [50](cases/case50_soname_inconsistent/README.md) | SONAME Inconsistent (Wrong Major Version) | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [51](cases/case51_protected_visibility/README.md) | Protected Visibility (DEFAULT to PROTECTED) | Quality | 🟢 COMPATIBLE |
| [52](cases/case52_rpath_leak/README.md) | RPATH Leak (Hardcoded Build Directory) | Quality | 🟢 COMPATIBLE (bad practice) |
| [53](cases/case53_namespace_pollution/README.md) | Namespace Pollution (Generic Symbol Names) | Breaking | 🔴 BREAKING (bad practice) |
| [54](cases/case54_used_reserved_field/README.md) | Used Reserved Field | Quality | 🟢 COMPATIBLE |
| [55](cases/case55_type_kind_changed/README.md) | Type Kind Changed (struct → union) | Breaking | 🔴 BREAKING |
| [56](cases/case56_struct_packing_changed/README.md) | Struct Packing Changed (pragma pack) | Breaking | 🔴 BREAKING |
| [57](cases/case57_enum_underlying_size_changed/README.md) | Enum Underlying Size Changed | Breaking | 🔴 BREAKING |
| [58](cases/case58_var_removed/README.md) | Global Variable Removed | Breaking | 🔴 BREAKING |
| [59](cases/case59_func_became_inline/README.md) | Function Became Inline (outlined → inline) | Breaking | 🔴 BREAKING |
| [60](cases/case60_base_class_position_changed/README.md) | Base Class Position Changed (Multiple Inheritance Reorder) | Breaking | 🔴 BREAKING |
| [61](cases/case61_var_added/README.md) | Global Variable Added | Addition | 🟢 COMPATIBLE |
| [62](cases/case62_type_field_added_compatible/README.md) | Type Field Added (Compatible — Opaque Struct) | Addition | 🟢 COMPATIBLE |
| [63](cases/case63_bitfield_changed/README.md) | Bitfield Width Changed | Breaking | 🔴 BREAKING |
| [64](cases/case64_calling_convention_changed/README.md) | Calling Convention Changed | Breaking | 🔴 BREAKING |
| [65](cases/case65_symbol_version_removed/README.md) | Symbol Version Removed | Breaking | 🔴 BREAKING |
| [66](cases/case66_language_linkage_changed/README.md) | Language Linkage Changed (extern "C" removed) | Breaking | 🔴 BREAKING |
| [67](cases/case67_tls_var_size_changed/README.md) | TLS Variable Size Changed | Breaking | 🔴 BREAKING |
| [68](cases/case68_virtual_method_added/README.md) | Virtual Method Added to Non-Virtual Class | Breaking | 🔴 BREAKING |
| [69](cases/case69_trivial_to_nontrivial/README.md) | Trivially Copyable to Non-Trivial (Calling Convention Change) | Breaking | 🔴 BREAKING |
| [70](cases/case70_flexible_array_member_changed/README.md) | Flexible Array Member Element Type Changed | Breaking | 🔴 BREAKING |
| [71](cases/case71_inline_namespace_moved/README.md) | Inline Namespace Moved | Breaking | 🔴 BREAKING |
| [72](cases/case72_covariant_return_changed/README.md) | Covariant Return Type Changed | Breaking | 🔴 BREAKING |
| [73](cases/case73_typedef_underlying_changed/README.md) | Typedef Underlying Type Changed | Breaking | 🔴 BREAKING |
| [74](cases/case74_detail_base_class_changed/README.md) | Internal `detail::` base class layout change leaks via public API | Breaking | 🔴 BREAKING |
| [75](cases/case75_detail_embedded_by_value/README.md) | Internal `detail::` Struct Embedded by Value | Breaking | 🔴 BREAKING |
| [76](cases/case76_detail_pimpl_vtable_changed/README.md) | Internal `detail::` Polymorphic Base Vtable Change | Breaking | 🔴 BREAKING |
| [77](cases/case77_detail_templated_base_changed/README.md) | Internal `detail::` Templated Base Class Layout Change | Breaking | 🔴 BREAKING |
| [78](cases/case78_task_arena_attach_tag/README.md) | `task_arena::attach` Tag Type Replaces Enum | Breaking | 🔴 BREAKING |
| [79](cases/case79_missing_template_instantiation/README.md) | Missing Template Instantiation in Shipped Binary | Breaking | 🔴 BREAKING |
| [80](cases/case80_pimpl_shared_to_unique/README.md) | Pimpl Alias Switched from `shared_ptr` to `unique_ptr` | Breaking | 🔴 BREAKING |
| [81](cases/case81_serialization_tag_reassigned/README.md) | Serialization Tag ID Reassigned | Breaking | 🔴 BREAKING |
| [82](cases/case82_sycl_overload_set_removed/README.md) | SYCL Overload Set Removed (DPC++ Build Withdrawn) | Breaking | 🔴 BREAKING |
| [83](cases/case83_cpu_dispatch_isa_dropped/README.md) | CPU-dispatch ISA family dropped | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [84](cases/case84_bundle_soname_skew/README.md) | Multi-Library Bundle SONAME Skew | Bundle | 🔵 BUNDLE (bad practice) |
| [85](cases/case85_internal_template_signature_changed/README.md) | Internal Template Signature Changed | Breaking | 🔴 BREAKING |
| [86](cases/case86_tag_struct_renamed/README.md) | Tag Struct Renamed (empty class re-mangling) | Breaking | 🔴 BREAKING |
| [87](cases/case87_default_template_arg_changed/README.md) | Default Template Argument Changed | Breaking | 🔴 BREAKING |
| [88](cases/case88_cpo_kind_changed/README.md) | CPO kind changed (BREAKING) | Breaking | 🔴 BREAKING |
| [89](cases/case89_inline_accessor_renamed_pimpl_member/README.md) | Inline Accessor References Renamed Pimpl Member | Breaking | 🔴 BREAKING |
| [90](cases/case90_bundle_intra_dep_removed/README.md) | Bundle — Intra-Bundle Removed Symbol | Bundle | 🔵 BUNDLE |
| [91](cases/case91_bundle_intra_signature_drift/README.md) | Bundle — Intra-Bundle extern-C Signature Drift | Bundle | 🔵 BUNDLE |
| [92](cases/case92_bundle_provider_changed/README.md) | Bundle — Symbol Provider Migration | Bundle | 🔵 BUNDLE |
| [93](cases/case93_bundle_manifest_drift/README.md) | Bundle — Instantiation Manifest Drift | Bundle | 🔵 BUNDLE |
| [94](cases/case94_empty_tag_gained_state/README.md) | Empty Tag Gained State | Breaking | 🔴 BREAKING |
| [95](cases/case95_allocator_nested_typedef_removed/README.md) | Allocator Nested-Typedef Removed | Breaking | 🔴 BREAKING |
| [96](cases/case96_hidden_friend_removed/README.md) | Hidden Friend Operator Removed | API Break | 🟠 API_BREAK |
| [97](cases/case97_api_depends_on_consumer_env/README.md) | API Depends on Consumer Environment | Breaking | 🔴 BREAKING |
| [98](cases/case98_cxx_standard_floor_raised/README.md) | C++ Standard Floor Raised | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [99](cases/case99_experimental_graduated/README.md) | Experimental to Stable Graduation (Compatible) | Addition | 🟢 COMPATIBLE |
| [100](cases/case100_experimental_removed_without_replacement/README.md) | Experimental Declaration Removed Without Replacement | Breaking | 🔴 BREAKING |
| [101](cases/case101_inline_namespace_version_bumped/README.md) | Inline Namespace Version Bumped | Breaking | 🔴 BREAKING |
| [102](cases/case102_frozen_runtime_signature_changed/README.md) | Frozen Runtime Signature Changed | Breaking | 🔴 BREAKING |
| [103](cases/case103_toolchain_flag_drift/README.md) | Toolchain Flag Drift | Quality | 🟢 COMPATIBLE (bad practice) |
| [104](cases/case104_glibcxx_dual_abi_flip/README.md) | libstdc++ Dual-ABI Flip | Breaking | 🔴 BREAKING (bad practice) |
| [105](cases/case105_concept_tightening/README.md) | Concept Tightening (C++20) | API Break | 🟠 API_BREAK |
| [106](cases/case106_ctor_became_explicit/README.md) | Conversion Operator Became `explicit` | API Break | 🟠 API_BREAK |
| [107](cases/case107_task_scheduler_init_removed/README.md) | `task_scheduler_init` Removed (historical ABI break) | Breaking | 🔴 BREAKING |
| [108](cases/case108_task_class_removed/README.md) | `task` Class Removed (historical ABI break — vtable angle) | Breaking | 🔴 BREAKING |
| [109](cases/case109_flow_graph_policy_renames/README.md) | flow::graph Policy Tag Renames | Breaking | 🔴 BREAKING |
| [110](cases/case110_concurrent_unordered_map_api_drift/README.md) | concurrent_unordered_map API Drift | Breaking | 🔴 BREAKING |
| [111](cases/case111_enumerable_thread_specific_lambda_ambiguity/README.md) | enumerable_thread_specific Lambda-Init Ambiguity | API Break | 🟠 API_BREAK (bad practice) |
| [112](cases/case112_lp64_ilp64/README.md) | LP64 → ILP64 Integer-Model Switch (oneMKL MKL_INT 32→64) | Breaking | 🔴 BREAKING |
| [113](cases/case113_abi_tag_changed/README.md) | ABI-tag set change ([abi:cxx11] lost on a single symbol) | Breaking | 🔴 BREAKING |
| [114](cases/case114_char8t_migration/README.md) | char8_t Migration (C++20 char-family → char8_t) | Breaking | 🔴 BREAKING |
| [115](cases/case115_bit_int_width_changed/README.md) | _BitInt(N) Width Change (C23 64 → 128) | Breaking | 🔴 BREAKING |
| [116](cases/case116_atomic_qualifier_changed/README.md) | _Atomic Qualifier Added (C11) | Breaking | 🔴 BREAKING |
| [117](cases/case117_no_unique_address/README.md) | [[no_unique_address]] Layout Overlay (no dedicated ChangeKind) | Breaking | 🔴 BREAKING |
| [118](cases/case118_internal_struct_field_added_scoped/README.md) | Internal Struct Gains a Field (Non-Public, Scoped) | No Change | ✅ NO_CHANGE |
| [119](cases/case119_internal_struct_field_removed_scoped/README.md) | Internal Struct Loses a Field (Non-Public, Scoped) | No Change | ✅ NO_CHANGE |
| [120](cases/case120_internal_struct_reordered_scoped/README.md) | Internal Struct Fields Reordered (Non-Public, Scoped) | No Change | ✅ NO_CHANGE |
| [121](cases/case121_kernel_btf_struct_field_added/README.md) | Kernel BTF Struct Field Growth | Breaking | 🔴 BREAKING |
| [122](cases/case122_template_signature_uninstantiated/README.md) | Uninstantiated Template Signature Change | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [123](cases/case123_default_argument_removed/README.md) | Default Argument Removed | API Break | 🟠 API_BREAK |
| [124](cases/case124_header_constant_value_changed/README.md) | Header Constant Value Changed | API Break | 🟠 API_BREAK |
| [125](cases/case125_class_became_final/README.md) | Class Became `final` | API Break | 🟠 API_BREAK |
| [126](cases/case126_sycl_device_impl_ptr/README.md) | SYCL `device` Impl Pointer — `shared_ptr` → Raw Pointer | Breaking | 🔴 BREAKING |
| [127](cases/case127_data_object_size_changed/README.md) | Exported Data Object Size Changed | Breaking | 🔴 BREAKING (bad practice) |
| [128](cases/case128_symbol_binding_strengthened/README.md) | Symbol Binding Strengthened (Weak → Global) | Quality | 🟢 COMPATIBLE |
| [129](cases/case129_struct_return_convention/README.md) | Struct-Return Convention Change | Breaking | 🔴 BREAKING |
| [130](cases/case130_exceptions_mode_flip/README.md) | Exceptions Mode Flip (`-fno-exceptions`) | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [131](cases/case131_rtti_mode_flip/README.md) | RTTI Mode Flip (`-fno-rtti`) | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [132](cases/case132_threadsafe_statics_flip/README.md) | Thread-Safe Statics Mode Flip | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [133](cases/case133_tls_model_flip/README.md) | TLS Model Flip | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [134](cases/case134_relro_weakened/README.md) | RELRO Weakened | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [135](cases/case135_stack_canary_removed/README.md) | Stack Canary Removed | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [136](cases/case136_executable_stack_removed/README.md) | Executable Stack Removed (the fix direction) | Quality | 🟢 COMPATIBLE |
| [137](cases/case137_runpath_changed/README.md) | DT_RUNPATH Changed | Quality | 🟢 COMPATIBLE |
| [138](cases/case138_needed_added/README.md) | DT_NEEDED Added | Quality | 🟢 COMPATIBLE |
| [139](cases/case139_symbol_version_node_removed/README.md) | Symbol Version Node Removed | Breaking | 🔴 BREAKING |
| [140](cases/case140_empty_base_optimization_lost/README.md) | Empty Base Optimization Lost (base subobject moved) | Breaking | 🔴 BREAKING |
| [141](cases/case141_versioned_symbol_scheme/README.md) | Versioned-Symbol Scheme (library-wide rename) | Breaking | 🔴 BREAKING (bad practice) |
| [142](cases/case142_vtable_slot_count_binary_only/README.md) | Vtable Slot Count Changed (detected from a stripped binary) | Breaking | 🔴 BREAKING |
| [143](cases/case143_audit_accidental_export/README.md) | Accidental Export (Single-Release Audit) | Quality | 🟢 COMPATIBLE (bad practice) |
| [144](cases/case144_audit_private_header_leak/README.md) | Private Header Leak (Single-Release Audit) | Quality | 🟢 COMPATIBLE (bad practice) |
| [145](cases/case145_audit_unversioned_export/README.md) | Unversioned Export Under a Versioning Scheme (Audit, Pure L0) | Quality | 🟢 COMPATIBLE (bad practice) |
| [146](cases/case146_audit_rtti_for_internal/README.md) | RTTI Exported for an Internal Type (Single-Release Audit) | Quality | 🟢 COMPATIBLE (bad practice) |
| [147](cases/case147_scan_depth_ladder/README.md) | Depth Ladder — the Same Input Answered at Increasing Depth | Quality | 🟢 COMPATIBLE (bad practice) |
| [148](cases/case148_xcheck_header_build_mismatch/README.md) | Header Build-Context Mismatch (Cross-Source Flagship) | API Break | 🟠 API_BREAK |
| [149](cases/case149_xcheck_odr_variant/README.md) | ODR Type Variant (Cross-Source, L4 Layout ↔ Layout) | API Break | 🟠 API_BREAK |
| [150](cases/case150_xcheck_export_public_pair/README.md) | Bidirectional Export ↔ Declaration Pair | Quality | 🟢 COMPATIBLE (bad practice) |
| [151](cases/case151_xcheck_provider_matrix/README.md) | Provider-Agreement Matrix (Corroboration Grows With Evidence) | Quality | 🟢 COMPATIBLE (bad practice) |
| [152](cases/case152_enum_size_flag_flip/README.md) | _enum_size_flag_flip — Enum-size flag flip (`-fshort-enums`) | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [153](cases/case153_struct_packing_flip/README.md) | _struct_packing_flip — Struct-packing mode flip (`-fpack-struct`) | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [154](cases/case154_lto_mode_flip/README.md) | _lto_mode_flip — LTO mode flip (`-flto`) | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [155](cases/case155_char_signedness_flip/README.md) | _char_signedness_flip — Plain-`char` signedness flip (`-fsigned-char` ↔ `-funsigned-char`) | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [156](cases/case156_public_macro_removed/README.md) | _public_macro_removed — Public macro removed | API Break | 🟠 API_BREAK |
| [157](cases/case157_inline_function_removed/README.md) | Inline Function Removed | API Break | 🟠 API_BREAK |
| [158](cases/case158_public_typedef_removed/README.md) | Public Typedef Removed | API Break | 🟠 API_BREAK |
| [160](cases/case160_public_api_internal_dep_added/README.md) | Public API Gains an Internal Dependency | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [161](cases/case161_target_dependency_added/README.md) | New Inter-Target Build/Link Dependency | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [162](cases/case162_symbol_source_owner_changed/README.md) | Exported Symbol's Declaring File Moved | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [163](cases/case163_python_kwarg_renamed/README.md) | Python Keyword Argument Renamed (Stub-Only API Break) | API Break | 🟠 API_BREAK |
| [164](cases/case164_preproc_conditional_field/README.md) | Preprocessor-Conditional Field (Build-Context False Positive) | No Change | ✅ NO_CHANGE |
| [165](cases/case165_polymorphic_nonvirtual_dtor/README.md) | Polymorphic Type Without a Virtual Destructor (New Anti-Pattern) | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [166](cases/case166_ref_qualifier_added/README.md) | Method Ref-Qualifier Added (`str()` → `str() &`) | Breaking | 🔴 BREAKING |
| [167](cases/case167_base_became_virtual/README.md) | Base Class Became Virtual (`: public Device` → `: public virtual Device`) | Breaking | 🔴 BREAKING |
| [168](cases/case168_virtual_method_devirtualized/README.md) | Virtual Method Devirtualized (`flush()` leaves the vtable) | Breaking | 🔴 BREAKING |
| [169](cases/case169_overload_added/README.md) | Overload Added to a Previously Unique Function | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [170](cases/case170_env_runtime_floor_raised/README.md) | Runtime Floor Raised (glibc Relink Drift) | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [171](cases/case171_static_tls_introduced/README.md) | Static TLS Introduced | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [172](cases/case172_vtable_thunk_offset_changed/README.md) | Vtable Thunk Offset Changed (detected from a stripped binary) | Breaking | 🔴 BREAKING |
| [173](cases/case173_vtt_slot_count_changed/README.md) | VTT Slot Count Changed (detected from a stripped binary) | Breaking | 🔴 BREAKING |
| [174](cases/case174_secondary_vtable_group_changed/README.md) | Secondary Vtable Group Changed | Breaking | 🔴 BREAKING |
| [175](cases/case175_kabi_crc_changed/README.md) | kABI CRC Changed | Breaking | 🔴 BREAKING |
| [176](cases/case176_kabi_symbol_namespace_changed/README.md) | kABI Export Namespace Changed | Breaking | 🔴 BREAKING |
| [177](cases/case177_long_double_abi_changed/README.md) | long double ABI Changed | Breaking | 🔴 BREAKING |
| [178](cases/case178_unnamed_type_in_public_abi/README.md) | Unnamed Type Leaks Into the Public ABI | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [179](cases/case179_cet_protection_weakened/README.md) | CET Protection Weakened | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [180](cases/case180_symbol_binding_lost_unique/README.md) | Symbol Binding Lost GNU_UNIQUE | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [181](cases/case181_xcheck_public_to_internal_dependency/README.md) | Public API Reaches an Internal Declaration | Quality | 🟢 COMPATIBLE (bad practice) |
| [182](cases/case182_accidental_export_removed_still_breaking/README.md) | Accidental Export Removed — Still Breaking Under Public-Header Scoping | Breaking | 🔴 BREAKING (bad practice) |
| [183](cases/case183_internal_version_node_churn/README.md) | Internal ELF symbol-version node churn | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [184](cases/case184_internal_enum_churn_scoped/README.md) | Internal Enum Churn, Scoped Out by Private-Header Origin | No Change | ✅ NO_CHANGE |
| [185](cases/case185_inherited_override_reuses_slot/README.md) | Inherited override reuses the base's vtable slot | Addition | 🟢 COMPATIBLE |
| [186](cases/case186_c_api_pointee_const_abi_neutral/README.md) | C API Pointee const-Qualification Is ABI-Neutral | No Change | ✅ NO_CHANGE |
| [187](cases/case187_public_struct_private_field_type/README.md) | Public Struct Field Retyped to an Internal Type | Breaking | 🔴 BREAKING (bad practice) |
| [188](cases/case188_public_class_private_base_class/README.md) | Public Class Gains a Private Base Class | Breaking | 🔴 BREAKING (bad practice) |
| [189](cases/case189_public_function_private_parameter_type/README.md) | Public Function Parameter Retyped to an Internal Type | Breaking | 🔴 BREAKING (bad practice) |
| [190](cases/case190_public_inline_function_references_internal_constant/README.md) | Public Inline Function References Internal Constant | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [191](cases/case191_header_only_graph_field_type/README.md) | Public Struct Gains a Field of a Private Type (Header-Only Graph) | Breaking | 🔴 BREAKING (bad practice) |
| [192](cases/case192_call_graph_break_survives_suppression/README.md) | Call-Graph-Reachable Break Survives Suppression | Breaking | 🔴 BREAKING |
| [193](cases/case193_ordinary_exported_fn_call_not_reachable/README.md) | Ordinary Exported Function's Internal Call Is Not Public-Reachable | Breaking | 🔴 BREAKING |
| [194](cases/case194_header_graph_rename_reconciled/README.md) | Internal Dependency Target Renamed, Safely Reconciled | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [195](cases/case195_header_graph_ambiguous_rename_not_reconciled/README.md) | Ambiguous Simultaneous Rename, Correctly Not Reconciled | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [196](cases/case196_header_graph_move_reconciled/README.md) | Declaration Reconciled as Moved Across a Compound Edit | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [197](cases/case197_header_graph_identity_reconciled/README.md) | Declaration Reconciled as Identity-Reconciled (Header Unchanged) | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
<!-- END GENERATED: case-index -->

---

## Running the catalog

### Validate all cases against ground truth

```bash
pytest tests/test_abi_scenarios.py -v
```

The CI job **Validate all examples** runs this over the whole catalog on every push.

### Build and explore a single case

```bash
cd catalog/cases/case01_symbol_removal
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so

abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h
# Verdict: BREAKING (symbol 'helper' was removed)
```

Every case directory includes an `app.c` or `app.cpp` that demonstrates the runtime failure. See the **Real Failure Demo** section in each case's `README.md` for copy-paste build instructions.

### CMake build (all cases)

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build
```

---

## Related documentation

- **Pinned 74-case cross-tool accuracy table** (all configurations, FP/FN): [`../README.md#validation-snapshot`](../README.md#validation-snapshot)
- **Per-case accuracy matrix and methodology:** [Tool Comparison & Benchmarks](../docs/reference/tool-comparison.md)
- **What counts as an ABI break (with code):** [ABI/API Compatibility](../docs/learn/abi-api-handling.md)
- **Dependency ABI leaks** (case 18 background): [`case18_dependency_leak/README.md`](cases/case18_dependency_leak/README.md)
- **Local build & snapshot workflow:** [Local Compare](../docs/user-guide/local-compare.md)
