# ABI Scenario Catalog

<!-- BEGIN GENERATED: catalog-headline (keep counts in sync with examples/ground_truth.json) -->
This directory contains **143 cases** (138 single-library + 5 multi-library bundle cases, the latter tracked under [ADR-023](../docs/development/adr/023-bundle-aware-multi-binary-analysis.md)) demonstrating real-world ABI/API break scenarios. Each case is a minimal, compilable C/C++ example with:
<!-- END GENERATED: catalog-headline -->

- Paired `v1/` and `v2/` source + headers.
- A consumer `app.c` / `app.cpp` that demonstrates the actual failure at runtime.
- A per-case `README.md` explaining what breaks and why.

The catalog drives abicheck's benchmark and serves as an encyclopedia of ABI pitfalls. For conceptual background on what ABI stability means and how to reason about it, see [ABI/API Handling & Recommendations](../docs/concepts/abi-api-handling.md).

> **Authoritative expected verdicts for benchmarking** live in [`ground_truth.json`](ground_truth.json).
> If a per-case README and `ground_truth.json` disagree, `ground_truth.json` is the source of truth.

---

## Verdict distribution

<!-- BEGIN GENERATED: verdict-distribution (keep counts in sync with examples/ground_truth.json) -->
| Verdict | Count | `checker_policy.py` set | Icon |
|---------|-------|-------------------------|------|
| BREAKING | 92 | `BREAKING_KINDS` | 🔴 |
| API_BREAK | 8 | `API_BREAK_KINDS` | 🟠 |
| COMPATIBLE_WITH_RISK | 9 | `RISK_KINDS` | 🟡 |
| COMPATIBLE (addition) | 10 | `ADDITION_KINDS` | 🟢 |
| COMPATIBLE (quality) | 13 | `QUALITY_KINDS` | 🟡 |
| NO_CHANGE | 6 | — | ✅ |
| Bundle (multi-binary) | 5 | see [ADR-023](../docs/development/adr/023-bundle-aware-multi-binary-analysis.md) | 🔵 |
<!-- END GENERATED: verdict-distribution -->

> **Verdict source of truth:** [`ground_truth.json`](ground_truth.json), which aligns with the 5-tier classification in [`abicheck/checker_policy.py`](../abicheck/checker_policy.py): `BREAKING_KINDS` → `API_BREAK_KINDS` → `RISK_KINDS` → `QUALITY_KINDS` → `ADDITION_KINDS`.

**Severity labels used in "Real Failure Demo" sections:**

- 🔴 **CRITICAL** — causes crash, wrong output, or silent data corruption
- 🟡 **INFORMATIONAL** — no immediate breakage; compromises future-proofing
- 🟡 **BAD PRACTICE** — library works today but mismanages the ABI contract
- ✅ **BASELINE** — no change; expected passing state

Some policy-escalated source/contract breaks (notably case30, case35) may keep identical runtime output for prebuilt binaries. For those, the demo shows: (1) binary still runs, and (2) recompilation against new headers fails or changes allowed behavior.

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
| Build/autodiscovery | `pytest tests/test_example_autodiscovery.py -v --tb=short -m integration` | CI Linux, Python 3.14 | 129 runnable single-library cases | 118 passed / 5 xfailed / 6 skipped | Passing |
| Default/debug verdicts | `python tests/validate_examples.py --json` + `python tests/check_validate_results.py` | CI Linux, blocking gate | 134 catalog cases | 122 PASS / 5 XFAIL / 7 SKIP | Passing; no FAIL/ERROR |
| Runtime smoke | `python validation/scripts/run_example_runtime_smoke.py --json` | CI Linux artifact | 134 catalog cases | 70 DEMONSTRATED / 47 NO_RUNTIME_SIGNAL / 7 BASELINE_SIGNAL / 10 SKIP | Passing; no BUILD_ERROR/BASELINE_ERROR |
| Release headers smoke | `python tests/validate_examples.py case01 case04 case129 case130 case131 case132 case133 --artifact-variant release-headers --json` | CI Linux artifact | 7 representative cases | 7 PASS | Informational, clean |
| Stripped headers smoke | `python tests/validate_examples.py case01 case04 case129 case130 case131 case132 case133 --artifact-variant stripped-headers --json` | CI Linux artifact | 7 representative cases | 6 PASS / 1 FAIL | Informational; `case129` loses stripped-only signal |
| Build/source smoke | `python tests/validate_examples.py case01 case04 case129 case130 case131 case132 case133 --artifact-variant build-source --json` | CI Linux artifact | 7 representative cases | 7 PASS | Informational, clean |

The full release/stripped/build-source matrix is not a blocking CI gate; CI
keeps those modes to the representative smoke set so catalog changes do not make
ordinary pull requests wait on the full extended matrix.

Recent build/source and ABI-mode examples:

| Case | Default/debug | Release | Stripped | Build/source |
|---|---|---|---|---|
| `case129_struct_return_convention` | PASS (`BREAKING`) | PASS (`BREAKING`) | FAIL (`COMPATIBLE`) | PASS (`BREAKING`) |
| `case130_exceptions_mode_flip` | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) |
| `case131_rtti_mode_flip` | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) |
| `case132_threadsafe_statics_flip` | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) |
| `case133_tls_model_flip` | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) | PASS (`COMPATIBLE_WITH_RISK`) |

Current mode-specific backlog: the representative stripped-headers smoke
under-classifies `case129_struct_return_convention` as `COMPATIBLE`; default,
release, and build/source modes classify it as `BREAKING`.

Expected non-pass buckets are already represented in `ground_truth.json`:

- XFAIL: `case105`, `case111`, `case64`, `case78`, `case97`
- SKIP: `case115`, `case121`, and bundle cases `case84`, `case90`, `case91`,
  `case92`, `case93`

---

## Case index

<!-- BEGIN GENERATED: case-index (scripts/gen_examples_docs.py --readme) -->
| # | Case | Category | abicheck verdict |
|---|------|----------|-----------------|
| [01](case01_symbol_removal/README.md) | Symbol Removal | Breaking | 🔴 BREAKING |
| [02](case02_param_type_change/README.md) | Parameter Type Change | Breaking | 🔴 BREAKING |
| [03](case03_compat_addition/README.md) | Compatible Addition (New Export) | Addition | 🟢 COMPATIBLE |
| [04](case04_no_change/README.md) | No Change | No Change | ✅ NO_CHANGE |
| [05](case05_soname/README.md) | Missing SONAME | Quality | 🟢 COMPATIBLE (bad practice) |
| [06](case06_visibility/README.md) | Symbol Visibility Leak | Breaking | 🔴 BREAKING (bad practice) |
| [07](case07_struct_layout/README.md) | Struct Layout Change | Breaking | 🔴 BREAKING |
| [08](case08_enum_value_change/README.md) | Enum Value Change | Breaking | 🔴 BREAKING |
| [09](case09_cpp_vtable/README.md) | C++ Vtable Change | Breaking | 🔴 BREAKING |
| [10](case10_return_type/README.md) | Return Type Change | Breaking | 🔴 BREAKING |
| [11](case11_global_var_type/README.md) | Global Variable Type Change | Breaking | 🔴 BREAKING |
| [12](case12_function_removed/README.md) | Function Removed from Shared Library | Breaking | 🔴 BREAKING |
| [13](case13_symbol_versioning/README.md) | Symbol Versioning Script | Quality | 🟢 COMPATIBLE |
| [14](case14_cpp_class_size/README.md) | C++ Class Size Change | Breaking | 🔴 BREAKING |
| [15](case15_noexcept_change/README.md) | `noexcept` Changed | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [16](case16_inline_to_non_inline/README.md) | Inline → Non-inline (ODR / Symbol Appearance) | Addition | 🟢 COMPATIBLE |
| [17](case17_template_abi/README.md) | Template Instantiation ABI Change | Breaking | 🔴 BREAKING |
| [18](case18_dependency_leak/README.md) | Dependency ABI Leak | Breaking | 🔴 BREAKING (bad practice) |
| [19](case19_enum_member_removed/README.md) | Enum Member Removed | Breaking | 🔴 BREAKING |
| [20](case20_enum_member_value_changed/README.md) | Enum Member Value Changed | Breaking | 🔴 BREAKING |
| [21](case21_method_became_static/README.md) | Method Became Static | Breaking | 🔴 BREAKING |
| [22](case22_method_const_changed/README.md) | Method Const Qualifier Changed | Breaking | 🔴 BREAKING |
| [23](case23_pure_virtual_added/README.md) | Virtual Method Became Pure Virtual | Breaking | 🔴 BREAKING |
| [24](case24_union_field_removed/README.md) | Union Field Removed | Breaking | 🔴 BREAKING |
| [25](case25_enum_member_added/README.md) | Enum Member Added | Addition | 🟢 COMPATIBLE |
| [26](case26_union_field_added/README.md) | Union Field Added | Breaking | 🔴 BREAKING |
| [26b](case26b_union_field_added_compatible/README.md) | Union Field Added (No Size Change) | Addition | 🟢 COMPATIBLE |
| [27](case27_symbol_binding_weakened/README.md) | Symbol Binding Weakened (GLOBAL → WEAK) | Quality | 🟢 COMPATIBLE |
| [28](case28_typedef_opaque/README.md) | Typedef and Opaque Type Changes | Breaking | 🔴 BREAKING |
| [29](case29_ifunc_transition/README.md) | GNU IFUNC Transition | Quality | 🟢 COMPATIBLE |
| [30](case30_field_qualifiers/README.md) | Field Qualifier Changes (const, volatile) | Breaking | 🔴 BREAKING |
| [31](case31_enum_rename/README.md) | Enum Member Rename | API Break | 🟠 API_BREAK |
| [32](case32_param_defaults/README.md) | Parameter Default Value Changes (C++) | API Break | 🟠 API_BREAK |
| [33](case33_pointer_level/README.md) | - Pointer Level Change | Breaking | 🔴 BREAKING |
| [34](case34_access_level/README.md) | Access Level Changed | API Break | 🟠 API_BREAK |
| [35](case35_field_rename/README.md) | - Field Rename | Breaking | 🔴 BREAKING |
| [36](case36_anon_struct/README.md) | - Anonymous Struct/Union Change | Breaking | 🔴 BREAKING |
| [37](case37_base_class/README.md) | - Base Class Changes | Breaking | 🔴 BREAKING |
| [38](case38_virtual_methods/README.md) | Virtual Method Changes | Breaking | 🔴 BREAKING |
| [39](case39_var_const/README.md) | Variable Const Change | Breaking | 🔴 BREAKING |
| [40](case40_field_layout/README.md) | Field Layout Changes | Breaking | 🔴 BREAKING |
| [41](case41_type_changes/README.md) | Type-Level Changes | Breaking | 🔴 BREAKING |
| [42](case42_type_alignment_changed/README.md) | Type Alignment Changed (standalone alignas) | Breaking | 🔴 BREAKING |
| [43](case43_base_class_member_added/README.md) | Base Class Member Added | Breaking | 🔴 BREAKING |
| [44](case44_cyclic_type_member_added/README.md) | Cyclic Type Member Added | Breaking | 🔴 BREAKING |
| [45](case45_multi_dim_array_change/README.md) | Multi-Dimensional Array Element Type Change | Breaking | 🔴 BREAKING |
| [46](case46_pointer_chain_type_change/README.md) | Pointer Chain Type Change | Breaking | 🔴 BREAKING |
| [47](case47_inline_to_outlined/README.md) | Inline Function Moved to Outlined | Addition | 🟢 COMPATIBLE |
| [48](case48_leaf_struct_through_pointer/README.md) | Leaf Struct Change Propagated Through Pointer | Breaking | 🔴 BREAKING |
| [49](case49_executable_stack/README.md) | Executable Stack (GNU_STACK RWX) | Quality | 🟢 COMPATIBLE (bad practice) |
| [50](case50_soname_inconsistent/README.md) | SONAME Inconsistent (Wrong Major Version) | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [51](case51_protected_visibility/README.md) | Protected Visibility (DEFAULT to PROTECTED) | Quality | 🟢 COMPATIBLE |
| [52](case52_rpath_leak/README.md) | RPATH Leak (Hardcoded Build Directory) | Quality | 🟢 COMPATIBLE (bad practice) |
| [53](case53_namespace_pollution/README.md) | Namespace Pollution (Generic Symbol Names) | Breaking | 🔴 BREAKING (bad practice) |
| [54](case54_used_reserved_field/README.md) | Used Reserved Field | Quality | 🟢 COMPATIBLE |
| [55](case55_type_kind_changed/README.md) | Type Kind Changed (struct → union) | Breaking | 🔴 BREAKING |
| [56](case56_struct_packing_changed/README.md) | Struct Packing Changed (pragma pack) | Breaking | 🔴 BREAKING |
| [57](case57_enum_underlying_size_changed/README.md) | Enum Underlying Size Changed | Breaking | 🔴 BREAKING |
| [58](case58_var_removed/README.md) | Global Variable Removed | Breaking | 🔴 BREAKING |
| [59](case59_func_became_inline/README.md) | Function Became Inline (outlined → inline) | Breaking | 🔴 BREAKING |
| [60](case60_base_class_position_changed/README.md) | Base Class Position Changed (Multiple Inheritance Reorder) | Breaking | 🔴 BREAKING |
| [61](case61_var_added/README.md) | Global Variable Added | Addition | 🟢 COMPATIBLE |
| [62](case62_type_field_added_compatible/README.md) | Type Field Added (Compatible — Opaque Struct) | Addition | 🟢 COMPATIBLE |
| [63](case63_bitfield_changed/README.md) | Bitfield Width Changed | Breaking | 🔴 BREAKING |
| [64](case64_calling_convention_changed/README.md) | Calling Convention Changed | Breaking | 🔴 BREAKING |
| [65](case65_symbol_version_removed/README.md) | Symbol Version Removed | Breaking | 🔴 BREAKING |
| [66](case66_language_linkage_changed/README.md) | Language Linkage Changed (extern "C" removed) | Breaking | 🔴 BREAKING |
| [67](case67_tls_var_size_changed/README.md) | TLS Variable Size Changed | Breaking | 🔴 BREAKING |
| [68](case68_virtual_method_added/README.md) | Virtual Method Added to Non-Virtual Class | Breaking | 🔴 BREAKING |
| [69](case69_trivial_to_nontrivial/README.md) | Trivially Copyable to Non-Trivial (Calling Convention Change) | Breaking | 🔴 BREAKING |
| [70](case70_flexible_array_member_changed/README.md) | Flexible Array Member Element Type Changed | Breaking | 🔴 BREAKING |
| [71](case71_inline_namespace_moved/README.md) | Inline Namespace Moved | Breaking | 🔴 BREAKING |
| [72](case72_covariant_return_changed/README.md) | Covariant Return Type Changed | Breaking | 🔴 BREAKING |
| [73](case73_typedef_underlying_changed/README.md) | Typedef Underlying Type Changed | Breaking | 🔴 BREAKING |
| [74](case74_detail_base_class_changed/README.md) | Internal `detail::` base class layout change leaks via public API | Breaking | 🔴 BREAKING |
| [75](case75_detail_embedded_by_value/README.md) | Internal `detail::` impl struct embedded by value | Breaking | 🔴 BREAKING |
| [76](case76_detail_pimpl_vtable_changed/README.md) | Internal `detail::` polymorphic base vtable change | Breaking | 🔴 BREAKING |
| [77](case77_detail_templated_base_changed/README.md) | Internal `detail::` *templated* base class layout change | Breaking | 🔴 BREAKING |
| [78](case78_task_arena_attach_tag/README.md) | task_arena::attach Tag Type Replaces Enum | Breaking | 🔴 BREAKING |
| [79](case79_missing_template_instantiation/README.md) | Missing template instantiation in shipped binary | Breaking | 🔴 BREAKING |
| [80](case80_pimpl_shared_to_unique/README.md) | Pimpl alias changed from `shared_ptr` to `unique_ptr` | Breaking | 🔴 BREAKING |
| [81](case81_serialization_tag_reassigned/README.md) | Serialization tag ID reassigned | Breaking | 🔴 BREAKING |
| [82](case82_sycl_overload_set_removed/README.md) | SYCL overload set removed (DPC++ build withdrawn) | Breaking | 🔴 BREAKING |
| [83](case83_cpu_dispatch_isa_dropped/README.md) | CPU-dispatch ISA family dropped | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [84](case84_bundle_soname_skew/README.md) | Multi-library bundle SONAME skew | Bundle | 🔵 BUNDLE (bad practice) |
| [85](case85_internal_template_signature_changed/README.md) | internal function-template signature leaks via public API (BREAKING) | Breaking | 🔴 BREAKING |
| [86](case86_tag_struct_renamed/README.md) | Tag struct renamed (empty class re-mangling) | Breaking | 🔴 BREAKING |
| [87](case87_default_template_arg_changed/README.md) | Default template argument changed | Breaking | 🔴 BREAKING |
| [88](case88_cpo_kind_changed/README.md) | CPO kind changed (BREAKING) | Breaking | 🔴 BREAKING |
| [89](case89_inline_accessor_renamed_pimpl_member/README.md) | Inline accessor references renamed pimpl member | Breaking | 🔴 BREAKING |
| [90](case90_bundle_intra_dep_removed/README.md) | Bundle — intra-bundle removed symbol | Bundle | 🔵 BUNDLE |
| [91](case91_bundle_intra_signature_drift/README.md) | Bundle — intra-bundle extern-C signature drift | Bundle | 🔵 BUNDLE |
| [92](case92_bundle_provider_changed/README.md) | Bundle — symbol provider migration | Bundle | 🔵 BUNDLE |
| [93](case93_bundle_manifest_drift/README.md) | Bundle — instantiation manifest drift | Bundle | 🔵 BUNDLE |
| [94](case94_empty_tag_gained_state/README.md) | Empty Tag Gained State | Breaking | 🔴 BREAKING |
| [95](case95_allocator_nested_typedef_removed/README.md) | Allocator Nested-Typedef Removed | Breaking | 🔴 BREAKING |
| [96](case96_hidden_friend_removed/README.md) | Hidden Friend Operator Removed | API Break | 🟠 API_BREAK |
| [97](case97_api_depends_on_consumer_env/README.md) | public API depends on consumer build environment (RISK) | Breaking | 🔴 BREAKING |
| [98](case98_cxx_standard_floor_raised/README.md) | C++ standard floor raised (per-binary: NO_CHANGE) | No Change | ✅ NO_CHANGE |
| [99](case99_experimental_graduated/README.md) | experimental → stable graduation (compatible) | Addition | 🟢 COMPATIBLE |
| [100](case100_experimental_removed_without_replacement/README.md) | experimental:: removed without replacement (API break) | Breaking | 🔴 BREAKING |
| [101](case101_inline_namespace_version_bumped/README.md) | inline namespace version bumped (BREAKING) | Breaking | 🔴 BREAKING |
| [102](case102_frozen_runtime_signature_changed/README.md) | Frozen Runtime Signature Changed (oneTBB `detail::r1` shape) | Breaking | 🔴 BREAKING |
| [103](case103_toolchain_flag_drift/README.md) | Toolchain flag drift (`toolchain_flag_drift`) | Quality | 🟢 COMPATIBLE (bad practice) |
| [104](case104_glibcxx_dual_abi_flip/README.md) | libstdc++ dual-ABI flip (`glibcxx_dual_abi_flip_detected`) | Breaking | 🔴 BREAKING (bad practice) |
| [105](case105_concept_tightening/README.md) | Concept Tightening (C++20) | Addition | 🟢 COMPATIBLE (bad practice) |
| [106](case106_ctor_became_explicit/README.md) | Conversion Operator Became `explicit` | API Break | 🟠 API_BREAK |
| [107](case107_task_scheduler_init_removed/README.md) | `task_scheduler_init` Removed (historical ABI break) | Breaking | 🔴 BREAKING |
| [108](case108_task_class_removed/README.md) | `task` Class Removed (historical ABI break — vtable angle) | Breaking | 🔴 BREAKING |
| [109](case109_flow_graph_policy_renames/README.md) | flow::graph Policy Tag Renames | Breaking | 🔴 BREAKING |
| [110](case110_concurrent_unordered_map_api_drift/README.md) | concurrent_unordered_map API Drift | Breaking | 🔴 BREAKING |
| [111](case111_enumerable_thread_specific_lambda_ambiguity/README.md) | enumerable_thread_specific Lambda-Init Ambiguity | Addition | 🟢 COMPATIBLE (bad practice) |
| [112](case112_lp64_ilp64/README.md) | LP64 → ILP64 integer-model switch (oneMKL MKL_INT 32→64) | Breaking | 🔴 BREAKING |
| [113](case113_abi_tag_changed/README.md) | ABI-tag set change ([abi:cxx11] lost on a single symbol) | Breaking | 🔴 BREAKING |
| [114](case114_char8t_migration/README.md) | char8_t migration (C++20 char-family → char8_t) | Breaking | 🔴 BREAKING |
| [115](case115_bit_int_width_changed/README.md) | _BitInt(N) width change (C23 64 → 128) | Breaking | 🔴 BREAKING |
| [116](case116_atomic_qualifier_changed/README.md) | _Atomic qualifier added (C11) | Breaking | 🔴 BREAKING |
| [117](case117_no_unique_address/README.md) | [[no_unique_address]] layout overlay (no new ChangeKind) | Breaking | 🔴 BREAKING |
| [118](case118_internal_struct_field_added_scoped/README.md) | Internal struct gains a field (non-public, scoped) | No Change | ✅ NO_CHANGE |
| [119](case119_internal_struct_field_removed_scoped/README.md) | Internal struct loses a field (non-public, scoped) | No Change | ✅ NO_CHANGE |
| [120](case120_internal_struct_reordered_scoped/README.md) | Internal struct fields reordered (non-public, scoped) | No Change | ✅ NO_CHANGE |
| [121](case121_kernel_btf_struct_field_added/README.md) | Kernel BTF struct grows a field (out-of-tree module break) | Breaking | 🔴 BREAKING |
| [122](case122_template_signature_uninstantiated/README.md) | Uninstantiated Template Signature Change (documented gap) | No Change | ✅ NO_CHANGE |
| [123](case123_default_argument_removed/README.md) | Default Argument Removed | API Break | 🟠 API_BREAK |
| [124](case124_header_constant_value_changed/README.md) | Header Constant Value Changed | API Break | 🟠 API_BREAK |
| [125](case125_class_became_final/README.md) | Class Became `final` | API Break | 🟠 API_BREAK |
| [126](case126_sycl_device_impl_ptr/README.md) | SYCL `device` impl pointer — `shared_ptr` → raw pointer | Breaking | 🔴 BREAKING |
| [127](case127_data_object_size_changed/README.md) | Exported Data Object Size Change | Breaking | 🔴 BREAKING (bad practice) |
| [128](case128_symbol_binding_strengthened/README.md) | Symbol Binding Strengthened (Weak → Global) | Quality | 🟢 COMPATIBLE |
| [129](case129_struct_return_convention/README.md) | Struct-Return Convention Change | Breaking | 🔴 BREAKING |
| [130](case130_exceptions_mode_flip/README.md) | Exceptions Mode Flip (`-fno-exceptions`) | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [131](case131_rtti_mode_flip/README.md) | RTTI Mode Flip (`-fno-rtti`) | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [132](case132_threadsafe_statics_flip/README.md) | Thread-Safe Statics Mode Flip (`-fno-threadsafe-statics`) | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [133](case133_tls_model_flip/README.md) | TLS Model Flip (`-ftls-model`) | Risk | 🟡 COMPATIBLE_WITH_RISK |
| [134](case134_relro_weakened/README.md) | RELRO Weakened | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [135](case135_stack_canary_removed/README.md) | Stack Canary Removed | Risk | 🟡 COMPATIBLE_WITH_RISK (bad practice) |
| [136](case136_executable_stack_removed/README.md) | Executable Stack Removed (the fix direction) | Quality | 🟢 COMPATIBLE |
| [137](case137_runpath_changed/README.md) | DT_RUNPATH Changed | Quality | 🟢 COMPATIBLE |
| [138](case138_needed_added/README.md) | DT_NEEDED Added | Quality | 🟢 COMPATIBLE |
| [139](case139_symbol_version_node_removed/README.md) | Symbol Version Node Removed | Breaking | 🔴 BREAKING |
| [140](case140_empty_base_optimization_lost/README.md) | Empty Base Optimization Lost (base subobject moved) | Breaking | 🔴 BREAKING |
| [141](case141_versioned_symbol_scheme/README.md) | Versioned-Symbol Scheme (library-wide rename) | Breaking | 🔴 BREAKING (bad practice) |
| [142](case142_vtable_slot_count_binary_only/README.md) | Vtable Slot Count Changed (detected from a stripped binary) | Breaking | 🔴 BREAKING |
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
cd examples/case01_symbol_removal
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so

abicheck compare libv1.so libv2.so --old-header v1.h --new-header v2.h
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
- **What counts as an ABI break (with code):** [ABI/API Handling & Recommendations](../docs/concepts/abi-api-handling.md)
- **Dependency ABI leaks** (case 18 background): [`case18_dependency_leak/README.md`](case18_dependency_leak/README.md)
- **Local build & snapshot workflow:** [Local Compare](../docs/user-guide/local-compare.md)
