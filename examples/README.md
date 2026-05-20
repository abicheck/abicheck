# ABI Scenario Catalog

This directory contains **98 published cases** (`01–76` + `26b` + `77, 79–84, 86–87, 89, 94, 95, 96, 105–112`) plus **4 multi-library bundle cases** (`90–93`, tracked separately under [ADR-023](../docs/development/adr/023-bundle-aware-multi-binary-analysis.md)) demonstrating real-world ABI/API break scenarios. Each case is a minimal, compilable C/C++ example with:

- Paired `v1/` and `v2/` source + headers.
- A consumer `app.c` / `app.cpp` that demonstrates the actual failure at runtime.
- A per-case `README.md` explaining what breaks and why.

The catalog drives abicheck's benchmark and serves as an encyclopedia of ABI pitfalls. For conceptual background on what ABI stability means and how to reason about it, see [ABI Breaks Explained](../docs/concepts/abi-breaks-explained.md).

> **Authoritative expected verdicts for benchmarking** live in [`ground_truth.json`](ground_truth.json).
> If a per-case README and `ground_truth.json` disagree, `ground_truth.json` is the source of truth.

---

## Verdict distribution

| Verdict | Count | `checker_policy.py` set | Icon |
|---------|-------|-------------------------|------|
| BREAKING | 72 | `BREAKING_KINDS` | 🔴 |
| API_BREAK | 4 | `API_BREAK_KINDS` | 🟠 |
| COMPATIBLE_WITH_RISK | 2 | `RISK_KINDS` | 🟡 |
| COMPATIBLE (addition) | 9 | `ADDITION_KINDS` | 🟢 |
| COMPATIBLE (quality) | 9 | `QUALITY_KINDS` | 🟡 |
| NO_CHANGE | 2 | — | ✅ |

> **Verdict source of truth:** [`ground_truth.json`](ground_truth.json), which aligns with the 5-tier classification in [`abicheck/checker_policy.py`](../abicheck/checker_policy.py): `BREAKING_KINDS` → `API_BREAK_KINDS` → `RISK_KINDS` → `QUALITY_KINDS` → `ADDITION_KINDS`.

**Severity labels used in "Real Failure Demo" sections:**

- 🔴 **CRITICAL** — causes crash, wrong output, or silent data corruption
- 🟡 **INFORMATIONAL** — no immediate breakage; compromises future-proofing
- 🟡 **BAD PRACTICE** — library works today but mismanages the ABI contract
- ✅ **BASELINE** — no change; expected passing state

Some policy-escalated source/contract breaks (notably case30, case35) may keep identical runtime output for prebuilt binaries. For those, the demo shows: (1) binary still runs, and (2) recompilation against new headers fails or changes allowed behavior.

---

## Case index

| # | Case | Category | abicheck verdict |
|---|------|----------|-----------------|
| [01](case01_symbol_removal/README.md) | Symbol Removal | Breaking | BREAKING 🔴 |
| [02](case02_param_type_change/README.md) | Param Type Change | Breaking | BREAKING 🔴 |
| [03](case03_compat_addition/README.md) | Compat Addition | Addition | COMPATIBLE 🟢 |
| [04](case04_no_change/README.md) | No Change | No Change | NO_CHANGE ✅ |
| [05](case05_soname/README.md) | Soname | Quality | COMPATIBLE 🟡 (bad practice) |
| [06](case06_visibility/README.md) | Visibility | Breaking | BREAKING 🔴 (bad practice) |
| [07](case07_struct_layout/README.md) | Struct Layout | Breaking | BREAKING 🔴 |
| [08](case08_enum_value_change/README.md) | Enum Value Change | Breaking | BREAKING 🔴 |
| [09](case09_cpp_vtable/README.md) | Cpp Vtable | Breaking | BREAKING 🔴 |
| [10](case10_return_type/README.md) | Return Type | Breaking | BREAKING 🔴 |
| [11](case11_global_var_type/README.md) | Global Var Type | Breaking | BREAKING 🔴 |
| [12](case12_function_removed/README.md) | Function Removed | Breaking | BREAKING 🔴 |
| [13](case13_symbol_versioning/README.md) | Symbol Versioning | Quality | COMPATIBLE 🟡 |
| [14](case14_cpp_class_size/README.md) | Cpp Class Size | Breaking | BREAKING 🔴 |
| [15](case15_noexcept_change/README.md) | Noexcept Change | Risk | COMPATIBLE_WITH_RISK 🟡 |
| [16](case16_inline_to_non_inline/README.md) | Inline To Non Inline | Addition | COMPATIBLE 🟢 |
| [17](case17_template_abi/README.md) | Template Abi | Breaking | BREAKING 🔴 |
| [18](case18_dependency_leak/README.md) | Dependency Leak | Breaking | BREAKING 🔴 (bad practice) |
| [19](case19_enum_member_removed/README.md) | Enum Member Removed | Breaking | BREAKING 🔴 |
| [20](case20_enum_member_value_changed/README.md) | Enum Member Value Changed | Breaking | BREAKING 🔴 |
| [21](case21_method_became_static/README.md) | Method Became Static | Breaking | BREAKING 🔴 |
| [22](case22_method_const_changed/README.md) | Method Const Changed | Breaking | BREAKING 🔴 |
| [23](case23_pure_virtual_added/README.md) | Pure Virtual Added | Breaking | BREAKING 🔴 |
| [24](case24_union_field_removed/README.md) | Union Field Removed | Breaking | BREAKING 🔴 |
| [25](case25_enum_member_added/README.md) | Enum Member Added | Addition | COMPATIBLE 🟢 |
| [26](case26_union_field_added/README.md) | Union Field Added | Breaking | BREAKING 🔴 |
| [26b](case26b_union_field_added_compatible/README.md) | Union Field Added Compatible | Addition | COMPATIBLE 🟢 |
| [27](case27_symbol_binding_weakened/README.md) | Symbol Binding Weakened | Quality | COMPATIBLE 🟡 |
| [28](case28_typedef_opaque/README.md) | Typedef Opaque | Breaking | BREAKING 🔴 |
| [29](case29_ifunc_transition/README.md) | Ifunc Transition | Quality | COMPATIBLE 🟡 |
| [30](case30_field_qualifiers/README.md) | Field Qualifiers | Breaking | BREAKING 🔴 |
| [31](case31_enum_rename/README.md) | Enum Rename | API Break | API_BREAK 🟠 |
| [32](case32_param_defaults/README.md) | Param Defaults | No Change | NO_CHANGE ✅ |
| [33](case33_pointer_level/README.md) | Pointer Level | Breaking | BREAKING 🔴 |
| [34](case34_access_level/README.md) | Access Level | API Break | API_BREAK 🟠 |
| [35](case35_field_rename/README.md) | Field Rename | Breaking | BREAKING 🔴 |
| [36](case36_anon_struct/README.md) | Anon Struct | Breaking | BREAKING 🔴 |
| [37](case37_base_class/README.md) | Base Class | Breaking | BREAKING 🔴 |
| [38](case38_virtual_methods/README.md) | Virtual Methods | Breaking | BREAKING 🔴 |
| [39](case39_var_const/README.md) | Var Const | Breaking | BREAKING 🔴 |
| [40](case40_field_layout/README.md) | Field Layout | Breaking | BREAKING 🔴 |
| [41](case41_type_changes/README.md) | Type Changes | Breaking | BREAKING 🔴 |
| [42](case42_type_alignment_changed/README.md) | Type Alignment Changed (alignas) | Breaking | BREAKING 🔴 |
| [43](case43_base_class_member_added/README.md) | Base Class Member Added | Breaking | BREAKING 🔴 |
| [44](case44_cyclic_type_member_added/README.md) | Cyclic Type Member Added | Breaking | BREAKING 🔴 |
| [45](case45_multi_dim_array_change/README.md) | Multi-Dim Array Element Type Change | Breaking | BREAKING 🔴 |
| [46](case46_pointer_chain_type_change/README.md) | Pointer Chain Type Change | Breaking | BREAKING 🔴 |
| [47](case47_inline_to_outlined/README.md) | Inline to Outlined | Addition | COMPATIBLE 🟢 |
| [48](case48_leaf_struct_through_pointer/README.md) | Leaf Struct Change Through Pointer | Breaking | BREAKING 🔴 |
| [49](case49_executable_stack/README.md) | Executable Stack (GNU_STACK RWX) | Quality | COMPATIBLE 🟡 (bad practice) |
| [50](case50_soname_inconsistent/README.md) | SONAME Inconsistent (Wrong Major) | Quality | COMPATIBLE 🟡 (bad practice) |
| [51](case51_protected_visibility/README.md) | Protected Visibility (DEFAULT→PROTECTED) | Quality | COMPATIBLE 🟡 |
| [52](case52_rpath_leak/README.md) | RPATH Leak (Hardcoded Build Dir) | Quality | COMPATIBLE 🟡 (bad practice) |
| [53](case53_namespace_pollution/README.md) | Namespace Pollution (Generic Names) | Breaking | BREAKING 🔴 |
| [54](case54_used_reserved_field/README.md) | Used Reserved Field | Quality | COMPATIBLE 🟡 |
| [55](case55_type_kind_changed/README.md) | Type Kind Changed (struct→union) | Breaking | BREAKING 🔴 |
| [56](case56_struct_packing_changed/README.md) | Struct Packing Changed (pragma pack) | Breaking | BREAKING 🔴 |
| [57](case57_enum_underlying_size_changed/README.md) | Enum Underlying Size Changed | Breaking | BREAKING 🔴 |
| [58](case58_var_removed/README.md) | Global Variable Removed | Breaking | BREAKING 🔴 |
| [59](case59_func_became_inline/README.md) | Function Became Inline (outlined→inline) | Breaking | BREAKING 🔴 |
| [60](case60_base_class_position_changed/README.md) | Base Class Position Changed (MI reorder) | Breaking | BREAKING 🔴 |
| [61](case61_var_added/README.md) | Global Variable Added | Addition | COMPATIBLE 🟢 |
| [62](case62_type_field_added_compatible/README.md) | Type Field Added (Opaque Struct) | Addition | COMPATIBLE 🟢 |
| [63](case63_bitfield_changed/README.md) | Bitfield Width Changed | Breaking | BREAKING 🔴 |
| [64](case64_calling_convention_changed/README.md) | Calling Convention Changed (ms_abi) | Breaking | BREAKING 🔴 |
| [65](case65_symbol_version_removed/README.md) | Symbol Version Removed (ELF) | Breaking | BREAKING 🔴 |
| [66](case66_language_linkage_changed/README.md) | Language Linkage Changed (extern "C") | Breaking | BREAKING 🔴 |
| [67](case67_tls_var_size_changed/README.md) | TLS Variable Size Changed | Breaking | BREAKING 🔴 |
| [68](case68_virtual_method_added/README.md) | Virtual Method Added (non-virtual → virtual) | Breaking | BREAKING 🔴 |
| [69](case69_trivial_to_nontrivial/README.md) | Trivially Copyable → Non-Trivial (calling convention) | Breaking | BREAKING 🔴 |
| [70](case70_flexible_array_member_changed/README.md) | Flexible Array Member Element Type Changed | Breaking | BREAKING 🔴 |
| [71](case71_inline_namespace_moved/README.md) | Inline Namespace Moved (v1→v2) | Breaking | BREAKING 🔴 |
| [72](case72_covariant_return_changed/README.md) | Covariant Return Type Changed (hierarchy insert) | Breaking | BREAKING 🔴 |
| [73](case73_typedef_underlying_changed/README.md) | Typedef Underlying Type Changed (int→void*) | Breaking | BREAKING 🔴 |
| [74](case74_detail_base_class_changed/README.md) | Internal `detail::` Base Class Layout Change (oneDAL-style leak) | Breaking | BREAKING 🔴 |
| [75](case75_detail_embedded_by_value/README.md) | Internal `detail::` Impl Embedded by Value | Breaking | BREAKING 🔴 |
| [76](case76_detail_pimpl_vtable_changed/README.md) | Internal `detail::` Polymorphic Base Vtable Change | Breaking | BREAKING 🔴 |
| [77](case77_detail_templated_base_changed/README.md) | Internal `detail::` Templated Base Class Layout Change | Breaking | BREAKING 🔴 |
| [79](case79_missing_template_instantiation/README.md) | Missing Template Instantiation in Shipped Binary | Breaking | BREAKING 🔴 |
| [80](case80_pimpl_shared_to_unique/README.md) | Pimpl Alias `shared_ptr` → `unique_ptr` | Breaking | BREAKING 🔴 |
| [81](case81_serialization_tag_reassigned/README.md) | Serialization Tag ID Reassigned (silent data corruption) | Breaking | BREAKING 🔴 |
| [82](case82_sycl_overload_set_removed/README.md) | SYCL Overload Set Removed (DPC++ build withdrawn) | Breaking | BREAKING 🔴 |
| [83](case83_cpu_dispatch_isa_dropped/README.md) | CPU-Dispatch ISA Family Dropped | Risk | COMPATIBLE_WITH_RISK 🟡 |
| [84](case84_bundle_soname_skew/README.md) | Multi-Library Bundle SONAME Skew | Breaking | BREAKING 🔴 (bad practice) |
| [86](case86_tag_struct_renamed/README.md) | Tag Struct Renamed (empty type re-mangling) | Breaking | BREAKING 🔴 |
| [87](case87_default_template_arg_changed/README.md) | Default Template Argument Changed | Breaking | BREAKING 🔴 |
| [89](case89_inline_accessor_renamed_pimpl_member/README.md) | Inline Accessor References Renamed Pimpl Member | Breaking | BREAKING 🔴 |
| [94](case94_empty_tag_gained_state/README.md) | Empty Tag Gained State (oneTBB partitioner shape) | Breaking | BREAKING 🔴 |
| [95](case95_allocator_nested_typedef_removed/README.md) | Allocator Nested-Typedef Removed (member_name suppression demo) | Breaking | BREAKING 🔴 |
| [96](case96_hidden_friend_removed/README.md) | Hidden Friend Operator Removed (castxml `befriending` detection) | API Break | API_BREAK 🟠 |
| [105](case105_concept_tightening/README.md) | Concept Tightening (C++20, known gap) | Addition | COMPATIBLE 🟢 (known gap) |
| [106](case106_ctor_became_explicit/README.md) | Conversion Operator Became `explicit` | API Break | API_BREAK 🟠 |
| [107](case107_task_scheduler_init_removed/README.md) | `task_scheduler_init` Removed (oneTBB 2021.1) | Breaking | BREAKING 🔴 |
| [108](case108_task_class_removed/README.md) | `task` Class Removed (oneTBB 2021.1) | Breaking | BREAKING 🔴 |
| [109](case109_flow_graph_policy_renames/README.md) | flow::graph Policy Tag Renames (oneTBB regression suite) | Breaking | BREAKING 🔴 |
| [110](case110_concurrent_unordered_map_api_drift/README.md) | concurrent_unordered_map API Drift (oneTBB regression suite) | Breaking | BREAKING 🔴 |
| [111](case111_enumerable_thread_specific_lambda_ambiguity/README.md) | enumerable_thread_specific Lambda-Init Ambiguity (oneTBB regression suite) | Addition | COMPATIBLE 🟢 (known gap) |
| [112](case112_task_arena_attach_tag/README.md) | task_arena::attach Tag Replaces Enum (oneTBB regression suite) | Breaking | BREAKING 🔴 |
| [120](case120_frozen_runtime_signature_changed/README.md) | Frozen Runtime Signature Changed (oneTBB `detail::r1` shape) | Breaking | BREAKING 🔴 |

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

- **Unified 77-case accuracy table** (all configurations, FP/FN): [`../README.md#validation-snapshot`](../README.md#validation-snapshot)
- **Per-case accuracy matrix and methodology:** [Tool Comparison & Benchmarks](../docs/reference/tool-comparison.md)
- **What counts as an ABI break (with code):** [ABI Breaks Explained](../docs/concepts/abi-breaks-explained.md)
- **Dependency ABI leaks** (case 18 background): [`case18_dependency_leak/README.md`](case18_dependency_leak/README.md)
- **Local build & snapshot workflow:** [Local Compare](../docs/user-guide/local-compare.md)
