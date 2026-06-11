# SYCL `test/abi` coverage map

This page maps the upstream **Intel DPC++ / `intel/llvm`** ABI test suite at
[`sycl/test/abi/`](https://github.com/intel/llvm/tree/sycl/sycl/test/abi) onto
abicheck's detectors, and records which scenarios abicheck catches, by what
mechanism, and at what [evidence tier](../concepts/evidence-and-detectability.md).

The two suites are complementary in *form*:

- Upstream uses **single-build golden checks** — it compiles the SYCL headers,
  dumps record layouts (`-fdump-record-layouts`), vtable layouts, or the
  exported-symbol list, and `FileCheck`s the result against a checked-in
  expectation.
- abicheck does a **two-snapshot diff** — it compares an *old* and a *new*
  build of the library and reports what changed.

They overlap on *which regressions they catch*, not on *how*. Where upstream
catches "this layout is wrong vs. the golden", abicheck catches "this layout
changed vs. the previous release".

## Coverage matrix

| Upstream test | What it guards | abicheck mechanism | Catches a regression? | Min tier |
|---------------|----------------|--------------------|-----------------------|----------|
| `sycl_symbols_linux.dump`, `sycl_symbols_windows.dump` | Exact exported-symbol set (ELF + PE) | Exported-symbol add/remove diff (`diff_symbols`, `diff_platform`) | ✅ Yes | L0 |
| `layout_accessors_device.cpp`, `layout_accessors_host.cpp` | Accessor record layout (offsets, `sizeof`, `align`) | `type_size_changed` / `struct_field_offset_changed` / `struct_alignment_changed` (DWARF or headers) | ✅ Yes | L1 |
| `layout_array.cpp`, `layout_buffer.cpp`, `layout_image.cpp`, `layout_span.cpp`, `layout_vec.cpp`, `layout_nd_range_view.cpp`, `layout_property_holder.cpp`, `layout_compile_time_kernel_info.cpp`, `layout_host_kernel_ref.cpp`, `layout_tls_code_loc_t.cpp` | Record layout of the named class | `type_size_changed`, `struct_field_offset_changed`, `base_class_offset_changed`, `tail_padding_reuse_changed` | ✅ Yes | L1 |
| `layout_handler.cpp` | `sycl::handler` layout (`sizeof=176`, member offsets) | same layout-diff kinds | ✅ Yes | L1 |
| `layout_exception.cpp` | `sycl::exception` layout (polymorphic) | layout-diff **plus** `vtable_slot_count_changed` / `rtti_inheritance_changed` at L0 | ✅ Yes | L0 (vtable/RTTI) / L1 (fields) |
| `vtable.cpp` | Virtual-table layout | `type_vtable_changed` (DWARF) **and** `vtable_slot_count_changed` (L0, from `_ZTV` size) | ✅ Yes | L0 |
| `symbol_size_alignment.cpp` | `sizeof`/`alignof` of public types | `type_size_changed`, `type_alignment_changed`, `symbol_size_changed` | ✅ Yes | L0/L1 |
| `preview_lib_marker.cpp` | Presence of the preview-ABI marker symbol | Symbol presence/absence diff | ⚠️ Indirect (as a symbol add/remove, not understood *as* a preview marker) | L0 |
| `abi_crossing_type_traits.cpp` | Type-trait values across the ABI boundary | Only insofar as it manifests as a layout/symbol change | ⚠️ Partial | L1 |
| `sycl_abi_neutrality_test.cpp`, `sycl_classes_abi_neutral_test.cpp` | **Rule:** no SYCL class may embed `std::string`/`std::list` by value (dual-ABI hazard) | `public_api_exposes_stl_by_value` (RISK) flags STL-by-value; a *new* embed also shows as `type_size_changed`. No single-build "neutrality rule" lint. | ⚠️ Partial — see gap below | L1/L2 |

## Worked example: PR #20821

The `shared_ptr<device_impl>` → raw-pointer change in
[intel/llvm#20821](https://github.com/intel/llvm/pull/20821) is captured as
example [`case126_sycl_device_impl_ptr`](../examples/case126_sycl_device_impl_ptr.md).
abicheck reports it as `type_size_changed sycl::device` (16 → 8 bytes) — the
**root cause**. Note that upstream's `sycl_symbols_*.dump` guard could *not* see
this directly (no mangled name changed), which is why it only caught the
downstream Windows symbol churn and needed follow-up PRs (#20902, #21028).

## What abicheck recovers **without DWARF** (L0, symbols only)

A common worry is that a stripped release with no debug info and no headers
defeats layout analysis. It does not defeat all of it: the Itanium C++ ABI
encodes several layout facts in the *sizes* of the objects it emits into
`.dynsym`, which abicheck decodes in `diff_elf_layout.py`:

| Signal | Source | What it reveals | Kind |
|--------|--------|-----------------|------|
| Exported-symbol set | `.dynsym` names | Added/removed/renamed functions & objects (the `.dump`-equivalent) | `func_removed`, `func_removed_elf_only`, … |
| Object symbol size | `st_size` of data symbols | `sizeof` of exported global objects | `symbol_size_changed` |
| **Vtable slot count** | `st_size` of `_ZTV<class>` (`slots ≈ size/ptr − 2`) | A virtual method was added/removed/reordered | `vtable_slot_count_changed` |
| **Inheritance shape** | `st_size` of `_ZTI<class>` (2 words = no base, 3 = single base, ≥4 = multiple/virtual) | Base-class set changed | `rtti_inheritance_changed` |
| Mangled-name signature | demangled `.dynsym` entries | Parameter/return types & the owning class of each method | feeds the symbol diff |

The vtable and RTTI signals are the important addition: a virtual-method change
or a base-class change need **not** rename any mangled symbol, yet it resizes
the class's `_ZTV`/`_ZTI` object — so abicheck sees it even on a fully stripped
library. This is the binary-only analogue of upstream's `vtable.cpp` and the
inheritance half of the `layout_*` goldens.

**What still needs DWARF, headers, or a golden:** exact member *offsets* and the
`sizeof` of a type that is *not* backed by a sized data symbol (e.g. a class only
ever passed by pointer). At L0 those fall back to `layout_unverifiable` (RISK,
non-escalating) rather than a false "compatible".

## Known gaps

1. **ABI-neutrality rule lint.** `sycl_classes_abi_neutral_test.cpp` asserts an
   invariant on a *single* build ("no SYCL class embeds `std::string`/`std::list`").
   abicheck only diffs two builds, so it flags a *newly introduced* embed (as a
   layout change / `public_api_exposes_stl_by_value`) but has no standalone
   "this one build violates the neutrality rule" lint. Tracked as a possible
   single-snapshot lint lane.
2. **Golden layouts for named classes.** abicheck catches a *regression* from
   the previous release, not a layout that is wrong on first introduction.
   A curated golden-layout fixture for the named SYCL classes would close that.
3. **Preview-marker semantics.** `preview_lib_marker.cpp` is understood only as a
   generic symbol presence/absence, not as a preview-ABI toggle.
