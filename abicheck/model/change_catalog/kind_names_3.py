# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Third of three (3/3) of ChangeKind's (name, value) pairs (ADR-061 D9 / model-vs-policy split).

Split purely by original declaration-order position -- 3 of 3 roughly-equal
chunks by line count, never by taxonomy category -- specifically so the
assembled enum's member order is byte-identical to the single-class
definition this replaces (`model/change_catalog/kinds.py` concatenates all
three IN THIS ORDER via the functional `Enum()` API). A taxonomy-based split
was considered and rejected: nothing here needs it (this is the enum's own
declaration, not `ChangeKindMeta` metadata, which is already correctly
taxonomy-split across `symbols.py`/`types.py`/`platform.py`/`build.py`/
`source.py`), and reordering by taxonomy risks changing `list(ChangeKind)`
iteration order for no benefit.

Exists only because a single ~950-line class body exceeds this repository's
800-line production file-size cap once physically moved under `model/`
(`scripts/check_architecture.py`'s `new-file-size` check, which applies to
every file under `abicheck/` regardless of classification, and which
explicitly forbids adding a *new* file to `architecture/debt.yaml`'s
adoption-debt ledger -- confirmed by reading that check directly, not
assumed -- so a debt-ledger exemption was never an available shortcut here).
Leaf module: no internal imports, matching `model/change_catalog/registry.py`'s
own leaf contract.

Each entry is `(member_name, member_value, comment_or_None)`; `kinds.py`
reassembles them with `Enum(..., type=str)`. A `None` comment means the
original declaration carried no inline documentation for that member.
"""

from __future__ import annotations

KIND_NAMES_3: tuple[tuple[str, str, str | None], ...] = (
    (
        "DECLARATION_MOVED",
        "declaration_moved",
        "graph-reconciled: same entity, new declaring file → RISK",
    ),
    (
        "DECLARATION_IDENTITY_RECONCILED",
        "declaration_identity_reconciled",
        "graph-reconciled: both name and location evidence changed → RISK",
    ),
    (
        "EXPORTED_NOT_PUBLIC",
        "exported_not_public",
        '── Cross-source validation (ADR-035 D4 / G19.2) ──────────────────────── -- Emitted by the intra-version cross-source engine (buildsource/crosscheck.py) -- which diffs ONE merged snapshot\'s evidence sources against each other -- (binary exports ↔ header decls ↔ build flags ↔ include graph) — no baseline -- compare. Per ADR-035 D1/D4 these are "bad ABI hygiene" findings, never -- BREAKING on their own: they default to RISK or API_BREAK and are advisory -- (suppressible) until a check earns its FP-rate-gate corpus and is promoted. -- symbol exported by the binary but declared in no public header → RISK',
    ),
    (
        "PUBLIC_NOT_EXPORTED",
        "public_not_exported",
        "public header declares an export obligation the binary does not provide → RISK",
    ),
    (
        "HEADER_BUILD_CONTEXT_MISMATCH",
        "header_build_context_mismatch",
        "headers parsed without the build's ABI-relevant context → API_BREAK",
    ),
    (
        "PRIVATE_HEADER_LEAK",
        "private_header_leak",
        "a public header pulls in a private/non-installed header → RISK",
    ),
    (
        "HEADER_BINARY_CONTEXT_MISMATCH",
        "header_binary_context_mismatch",
        "P0 evidence-coherence audit — emitted directly by checker.compare() (not -- the crosscheck engine above; needs no L3/L4 evidence, only the always- -- available clang-L2-backend + DWARF dump path), when either side's -- AbiSnapshot.dwarf_layout_coherence == \"mismatch\": at least one record -- backfill_dwarf_layout() found a uniquely-named DWARF counterpart for -- but rejected as not corroborating (kind/field disagreement). Never -- BREAKING on its own — the uncorroborated record already stays header- -- only/incomplete rather than merged, so no incorrect layout data -- reaches the diff; this only flags that the analysis had a reduced- -- confidence spot. RISK, matching AC-008/009's evidence-coherence kinds. -- DWARF-vs-header-AST layout backfill found an uncorroborated record → RISK",
    ),
    (
        "ODR_TYPE_VARIANT",
        "odr_type_variant",
        "one type has divergent per-TU layouts (L4 ODR conflict) → API_BREAK",
    ),
    (
        "PUBLIC_TO_INTERNAL_DEPENDENCY",
        "public_to_internal_dependency",
        "public API reaches an internal (non-public) entity via the L5 graph → RISK",
    ),
    (
        "UNVERSIONED_EXPORTED_SYMBOL",
        "unversioned_exported_symbol",
        'Single-release hygiene audit (ADR-035 D8). Intra-version "bad ABI hygiene" -- surfaced from one build (no baseline) by the same cross-source engine. -- exported symbol carries no version though the library uses a version script → RISK',
    ),
    (
        "RTTI_FOR_INTERNAL_TYPE",
        "rtti_for_internal_type",
        "typeinfo/vtable exported for a type declared only in a private header → RISK",
    ),
    (
        "IDENTITY_COLLISION_DETECTED",
        "identity_collision_detected",
        "two distinct declarations (proven by differing clang USR) share one SourceEntity.identity() key → RISK",
    ),
    (
        "COMPILE_CONTEXT_CONFLICT",
        "compile_context_conflict",
        "L3 compile units of one build target carry conflicting ABI-relevant contexts (e.g. -frtti vs -fno-rtti, or a define bound to two values) that were silently aggregated → RISK (AC-008)",
    ),
    (
        "SOURCE_SURFACE_DSO_MISMATCH",
        "source_surface_dso_mismatch",
        "the linked L4 source surface maps to none of the analyzed binary's exports — it likely describes a different/shared DSO and needs per-DSO relink → RISK (AC-009)",
    ),
    (
        "STDLIB_IMPLEMENTATION_CHANGED",
        "stdlib_implementation_changed",
        "── Cross-implementation standard-library compatibility (D-stdlib) ─────── -- Emitted by the build-mode diff (diff_stdlib_impl.py) when the two -- snapshots were produced against *different standard-library -- implementations* — a third compatibility axis (alongside backward / -- forward) that the C++ standard does not guarantee. These are RISK, not -- BREAKING: when an embedded stdlib type's layout actually differs, the -- artifact/type diff emits the BREAKING size/offset finding separately; -- these kinds explain and localize the cause without escalating on their -- own (and stay silent when build-mode evidence is absent). -- libstdc++ ↔ libc++ ↔ MSVC STL → RISK",
    ),
    (
        "LIBCPP_ABI_VERSION_CHANGED",
        "libcpp_abi_version_changed",
        "_LIBCPP_ABI_VERSION 1 ↔ 2 → RISK",
    ),
    (
        "BASE_CLASS_OFFSET_CHANGED",
        "base_class_offset_changed",
        "── Fine-grained class-layout descriptor (layout-closure work) ─────────── -- Emitted by diff_layout.py from the optional layout fields on RecordType -- (base offsets, vptr offset, dsize/tail-padding, standard-layout / -- trivially-copyable traits). Each is guarded tri-state: skipped when either -- side lacks the evidence, so an evidence-tier downgrade never fabricates a -- finding. -- base subobject moved → this-ptr/field offsets shift → BREAKING",
    ),
    (
        "VPTR_INTRODUCED",
        "vptr_introduced",
        "first virtual added → vtable pointer prepended → all offsets shift → BREAKING",
    ),
    (
        "TRIVIALLY_COPYABLE_LOST",
        "trivially_copyable_lost",
        "type no longer trivially-copyable → pass-by-value/register ABI changes → BREAKING",
    ),
    (
        "STANDARD_LAYOUT_LOST",
        "standard_layout_lost",
        "type no longer standard-layout → offsetof/C-compat/tail-padding reuse changes → RISK",
    ),
    (
        "TAIL_PADDING_REUSE_CHANGED",
        "tail_padding_reuse_changed",
        "data-size (dsize) changed at stable sizeof → derived tail-padding reuse shifts → RISK",
    ),
    (
        "LAYOUT_UNVERIFIABLE",
        "layout_unverifiable",
        "layout could not be verified at this evidence tier (no debug info) → RISK, non-escalating",
    ),
    (
        "VTABLE_SLOT_COUNT_CHANGED",
        "vtable_slot_count_changed",
        "── Binary-only (no-DWARF / L0) C++ layout descriptors ─────────────────── -- Emitted by diff_elf_layout.py purely from .dynsym symbol sizes — no debug -- info, no headers. The Itanium C++ ABI encodes a class's vtable slot count -- in the size of its `_ZTV` vtable object and its inheritance shape in the -- size of its `_ZTI` typeinfo object, so a virtual-method or base-class -- change is observable even when the library ships fully stripped of DWARF. -- _ZTV size delta → virtual method add/remove/reorder → BREAKING",
    ),
    (
        "RTTI_INHERITANCE_CHANGED",
        "rtti_inheritance_changed",
        "_ZTI size delta → base-class set/shape changed → BREAKING",
    ),
    (
        "PYTHON_STABLE_ABI_VIOLATION",
        "python_stable_abi_violation",
        "── CPython extension modules (Cython / pybind11 / C-ext, abi3) ─────────── -- Emitted by diff_python.py for a stable-ABI (abi3 / Py_LIMITED_API) -- extension module. The compatibility contract for such a module is the set -- of CPython C-API symbols it IMPORTS from libpython, not its exports (G14). -- abi3 module gained an import outside the stable ABI (e.g. a private _Py* symbol) → won't load on a Limited-API interpreter → RISK",
    ),
    (
        "PYTHON_ABI3_DROPPED",
        "python_abi3_dropped",
        "module was abi3 (loads on all interpreters ≥ its floor) but the new build is version-specific → drops every other interpreter it used to support → RISK",
    ),
    (
        "PYTHON_GIL_ABI_CHANGED",
        "python_gil_abi_changed",
        "extension switched between the regular (GIL) and free-threaded (PEP 703, Py_GIL_DISABLED) CPython ABI → the two builds are not interchangeable, a consumer on the other interpreter can't load it → RISK",
    ),
    (
        "PYTHON_ABI3_FLOOR_RAISED",
        "python_abi3_floor_raised",
        "both builds are abi3 but the new one's declared cpXY-abi3 tag floor is higher (e.g. cp39-abi3 → cp310-abi3) → interpreters in the dropped range can no longer load it → RISK",
    ),
    (
        "STATIC_TLS_INTRODUCED",
        "static_tls_introduced",
        "── G23 Phase A — Linux ELF artifact facts ────────────────────────────── -- A1: DF_STATIC_TLS drift. A library that adopts the static (initial/local- -- exec) TLS model can no longer be reliably dlopen()ed. Artifact-provable -- from the binary, so it does not need an L3 build pack (the flag-level -- TLS_MODEL_CHANGED stays the explanatory L3 signal). -- → RISK (breaks dlopen consumers)",
    ),
    ("STATIC_TLS_REMOVED", "static_tls_removed", "→ COMPATIBLE (improvement)"),
    (
        "CET_PROTECTION_WEAKENED",
        "cet_protection_weakened",
        "A2: .note.gnu.property control-flow-protection drift. Dropping IBT/SHSTK -- (x86 CET) or BTI/PAC (AArch64) weakens the process-wide guarantee. -- IBT/SHSTK dropped → RISK",
    ),
    (
        "BRANCH_PROTECTION_WEAKENED",
        "branch_protection_weakened",
        "BTI/PAC dropped → RISK",
    ),
    (
        "CET_PROTECTION_IMPROVED",
        "cet_protection_improved",
        "IBT/SHSTK gained → COMPATIBLE",
    ),
    (
        "BRANCH_PROTECTION_IMPROVED",
        "branch_protection_improved",
        "BTI/PAC gained → COMPATIBLE",
    ),
    (
        "ELF_MACHINE_CHANGED",
        "elf_machine_changed",
        "A3: ELF identity / ABI-flags guard. The ELF-side counterpart to -- PE_MACHINE_CHANGED / MACHO_CPU_TYPE_CHANGED. ELF_ABI_FLAGS_CHANGED makes -- float-ABI drift artifact-proven (the flag-level FLOAT_ABI_CHANGED stays the -- explanatory L3 signal). -- e_machine differs → BREAKING",
    ),
    ("ELF_CLASS_CHANGED", "elf_class_changed", "32↔64-bit → BREAKING"),
    (
        "ELF_ABI_FLAGS_CHANGED",
        "elf_abi_flags_changed",
        "decoded float-ABI/EABI drift → BREAKING",
    ),
    ("ELF_OSABI_CHANGED", "elf_osabi_changed", "EI_OSABI differs → RISK"),
    (
        "SYMBOL_BINDING_BECAME_UNIQUE",
        "symbol_binding_became_unique",
        "A4: STB_GNU_UNIQUE binding transitions. Uniqueness is enforced process-wide -- and inhibits dlclose(); losing it removes an ODR-uniqueness guarantee. -- → RISK",
    ),
    ("SYMBOL_BINDING_LOST_UNIQUE", "symbol_binding_lost_unique", "→ RISK"),
    (
        "VTABLE_THUNK_OFFSET_CHANGED",
        "vtable_thunk_offset_changed",
        "── G23 Phase B1 — Itanium multi-inheritance vtable machinery (L0) ─────── -- Recovered from .dynsym thunk / VTT symbol names + sizes, no DWARF/headers. -- These catch multi-inheritance / virtual-base breaks that the primary-vtable -- _ZTV size diff (VTABLE_SLOT_COUNT_CHANGED) cannot see — e.g. a base reorder -- that shifts this-adjustment thunk offsets without changing the slot count. -- this-adjustment baked into old vtables now wrong → BREAKING",
    ),
    (
        "VTABLE_THUNK_SET_CHANGED",
        "vtable_thunk_set_changed",
        "a persisting method gained/lost a vtable thunk (secondary-base override) → BREAKING",
    ),
    (
        "VTT_SLOT_COUNT_CHANGED",
        "vtt_slot_count_changed",
        "_ZTT size delta → virtual-base construction scaffolding changed → BREAKING",
    ),
    (
        "SECONDARY_VTABLE_GROUP_CHANGED",
        "secondary_vtable_group_changed",
        "B2: L1 DWARF vtable-group reconstruction. The derived class's own base -- declaration list is unchanged, but a base's *polymorphism* changed (a base -- gained/lost virtuals), restructuring which bases own a secondary vtable -- group — a cross-type effect the per-type field/base diff cannot see. -- secondary vtable group added/removed/reordered → BREAKING",
    ),
    (
        "VIRTUAL_BASE_OFFSET_CHANGED",
        "virtual_base_offset_changed",
        "A same-set reorder of virtual bases shifts the virtual-base offset table, so -- this-pointer adjustments baked into old binaries land on the wrong subobject. -- vbase offset table reordered → BREAKING",
    ),
    (
        "UNNAMED_TYPE_IN_PUBLIC_ABI",
        "unnamed_type_in_public_abi",
        "── G23 Phase D — ecosystem detectors ─────────────────────────────────── -- D3: an exported symbol whose mangled name embeds an unnamed type — a lambda -- closure (`Ul…E_`) or an unnamed struct/enum (`Ut…_`). Their mangling is -- TU- and compiler-ordering-fragile (recompiling can renumber them), so -- exporting them is an ABI time bomb. Hygiene RISK, reported when newly -- introduced. -- → RISK",
    ),
    (
        "LONG_DOUBLE_ABI_CHANGED",
        "long_double_abi_changed",
        "D2: a function's `long double` parameter/return representation changed -- (ppc64 IEEE128 ↔ IBM double-double, or -mlong-double-64) — same source -- signature, different FP format. Detected from the Itanium long-double -- mangling token (e/g/u9__ieee128) on a removed↔added pair, or from the -- DWARF byte size on a persisting symbol. -- → BREAKING",
    ),
    (
        "KABI_SYMBOL_REMOVED",
        "kabi_symbol_removed",
        "D1: Linux kernel module ABI (kABI) facts from Module.symvers / genksyms. -- exported kernel symbol gone → BREAKING",
    ),
    (
        "KABI_CRC_CHANGED",
        "kabi_crc_changed",
        "genksyms CRC changed → modversions reject the module → BREAKING",
    ),
    (
        "KABI_SYMBOL_NAMESPACE_CHANGED",
        "kabi_symbol_namespace_changed",
        "export namespace gained/moved → module needs MODULE_IMPORT_NS → BREAKING",
    ),
    (
        "KABI_EXPORT_TYPE_CHANGED",
        "kabi_export_type_changed",
        "EXPORT_SYMBOL ↔ EXPORT_SYMBOL_GPL → API_BREAK",
    ),
    (
        "KABI_SYMBOL_ADDED",
        "kabi_symbol_added",
        "new exported kernel symbol → COMPATIBLE",
    ),
    (
        "PYTHON_API_FUNCTION_REMOVED",
        "python_api_function_removed",
        "── Python-level API of an extension module (G23) ───────────────────────── -- Emitted by diff_python_api.py from the Python-visible surface recovered -- from a `.pyi` type stub — the functions/classes/methods/signatures a -- consumer `import`s. Invisible to the C-ABI/export-table view: two builds -- can be binary-identical yet break every caller. These are source-level -- (API_BREAK) or behavioural-risk (RISK) findings, never binary breaks. -- a public top-level function disappeared from the module's Python API → callers importing it break → API_BREAK",
    ),
    (
        "PYTHON_API_FUNCTION_ADDED",
        "python_api_function_added",
        "a new public top-level function → additive, existing callers unaffected → COMPATIBLE",
    ),
    (
        "PYTHON_API_CLASS_REMOVED",
        "python_api_class_removed",
        "a public class disappeared from the module's Python API → callers referencing it break → API_BREAK",
    ),
    (
        "PYTHON_API_CLASS_ADDED",
        "python_api_class_added",
        "a new public class → additive → COMPATIBLE",
    ),
    (
        "PYTHON_API_METHOD_REMOVED",
        "python_api_method_removed",
        "a public method disappeared from a class that still exists → callers of it break → API_BREAK",
    ),
    (
        "PYTHON_API_METHOD_ADDED",
        "python_api_method_added",
        "a new public method on an existing class → additive → COMPATIBLE",
    ),
    (
        "PYTHON_API_PARAMETER_REMOVED",
        "python_api_parameter_removed",
        "a parameter was dropped from a function/method signature → callers passing it hit a TypeError → API_BREAK",
    ),
    (
        "PYTHON_API_PARAMETER_ADDED",
        "python_api_parameter_added",
        "a new *required* (no-default) parameter was added → every existing call now raises a missing-argument TypeError → API_BREAK",
    ),
    (
        "PYTHON_API_PARAMETER_RENAMED",
        "python_api_parameter_renamed",
        "a parameter was renamed → callers passing it by keyword hit an unexpected-keyword TypeError → API_BREAK",
    ),
    (
        "PYTHON_API_DEFAULT_REMOVED",
        "python_api_default_removed",
        "a parameter lost its default value → callers relying on the default now raise a missing-argument TypeError → API_BREAK",
    ),
    (
        "PYTHON_API_PARAMETER_TYPE_CHANGED",
        "python_api_parameter_type_changed",
        "a parameter's type annotation changed → type-checker/behavioural risk, not a hard runtime break → RISK",
    ),
    (
        "PYTHON_API_RETURN_TYPE_CHANGED",
        "python_api_return_type_changed",
        "a function/method's return annotation changed → callers may mishandle the result → RISK",
    ),
    (
        "PYTHON_API_PARAMETER_KIND_CHANGED",
        "python_api_parameter_kind_changed",
        "a parameter's binding changed — positional↔keyword-only, keyword→positional-only, or the positional order/position shifted — so existing call sites bind arguments differently even though the names are unchanged → API_BREAK",
    ),
    (
        "PYTHON_API_CALLABLE_KIND_CHANGED",
        "python_api_callable_kind_changed",
        "a callable's protocol changed — def↔async def (callers must/mustn't await), or method↔property / static↔class↔instance binding — so existing call/access sites break even with an unchanged parameter list → API_BREAK",
    ),
    (
        "PYTHON_API_OVERLOAD_REMOVED",
        "python_api_overload_removed",
        "an @overload signature variant was dropped from an overloaded function/method → typed callers that relied on that call shape lose it → API_BREAK",
    ),
    (
        "PYTHON_API_STUB_INVALID",
        "python_api_stub_invalid",
        "a shipped .pyi stub could not be parsed or exceeded safety limits → API_BREAK",
    ),
    (
        "RUNTIME_FLOOR_RAISED",
        "runtime_floor_raised",
        "── Toolchain / runtime environment drift (binutils & glibc skew) ──────── -- Artifacts of relinking on a different binutils or building against a -- different glibc/sysroot rather than a source-level interface change. -- The per-provider-lib synthesis of SYMBOL_VERSION_REQUIRED_ADDED noise: -- one headline finding naming the old→new deployment floor (e.g. -- GLIBC_2.28 → GLIBC_2.34) with the imported symbols that pulled it up. -- max required version node per provider lib rose → binary no longer loads on older runtimes → RISK",
    ),
    (
        "PLATFORM_BASELINE_FLOOR_RAISED",
        "platform_baseline_floor_raised",
        "A single binary's own required floor checked against a declared -- platform-baseline promise (e.g. a manylinux wheel tag), independent of -- whether the floor moved between old and new — unlike RUNTIME_FLOOR_RAISED -- (a two-snapshot delta), this fires even on a static/unchanged floor that -- simply exceeds what the artifact's own tag promises (G10). -- max required GLIBC_2.x/GLIBCXX_x/CXXABI_x exceeds the declared/derived platform-baseline floor → RISK",
    ),
    (
        "MUSLLINUX_GLIBC_DEPENDENCY_DETECTED",
        "musllinux_glibc_dependency_detected",
        "musllinux (PEP 656) wheels target Alpine's musl libc, which has no -- symbol-versioning namespace at all — a GLIBC_* requirement (or other -- direct glibc-loader/SONAME evidence) means the binary won't even -- resolve its dependencies there, not merely a version mismatch (G27, -- generalizes G10 beyond glibc-floor comparison to a musl compatibility -- yes/no check). GLIBCXX_*/CXXABI_* alone are NOT disqualifying — a musl -- system's own libstdc++ can legitimately carry such verneed entries; -- see diff_versioning.check_musllinux_glibc_dependency's docstring. -- binary tagged musllinux-compatible actually requires a glibc-versioned symbol → BREAKING",
    ),
    (
        "MACOS_DEPLOYMENT_TARGET_RAISED",
        "macos_deployment_target_raised",
        "A wheel's macosx_X_Y_<arch> platform tag promises a *maximum* macOS -- deployment target its binaries may require; the Mach-O -- LC_VERSION_MIN_MACOSX/LC_BUILD_VERSION load command carries the -- binary's own actual minimum (G27, the macOS half of G10's manylinux -- glibc-floor idea). -- binary's own Mach-O minimum OS exceeds the declared/derived macOS deployment-target floor → RISK",
    ),
    (
        "WHEEL_TAG_ARCHITECTURE_MISMATCH",
        "wheel_tag_architecture_mismatch",
        "A wheel's platform tag names exactly one CPU architecture -- (manylinux_2_17_x86_64, macosx_11_0_arm64, ...); the contained -- binary's own ELF e_machine/Mach-O cpu_type is the ground truth. A -- mismatch means the wheel cannot even be loaded on the architecture it -- claims to support — worse than a version-floor risk (G27, tied to the -- wheel tag's own claim, unlike G13's two-snapshot elf_machine_changed/ -- macho_cpu_type_changed). -- binary's recorded machine/cpu_type disagrees with the wheel tag's claimed architecture → BREAKING",
    ),
    (
        "WHEEL_RPATH_NOT_PORTABLE",
        "wheel_rpath_not_portable",
        "A wheel's binaries install to an unpredictable per-user path; any -- RPATH/RUNPATH entry that isn't $ORIGIN-relative is almost always a -- build-machine artifact that won't exist on the install target -- (auditwheel/delocate exist specifically to rewrite these) (G27). -- RPATH/RUNPATH carries a non-$ORIGIN-relative (absolute) entry in a declared wheel-verification context → RISK",
    ),
    (
        "WHEEL_CLOSURE_DEPENDENCY_VIOLATION",
        "wheel_closure_dependency_violation",
        "A DT_NEEDED entry matching auditwheel/delocate's vendored content-hash -- naming convention (G9's strip_vendor_hash pattern) with no -- $ORIGIN-relative RPATH/RUNPATH to ever find it — the vendored -- dependency isn't actually part of the resolvable closure (G27). -- vendored/hash-suffixed dependency with no $ORIGIN-relative RPATH/RUNPATH mechanism to find it → BREAKING",
    ),
    (
        "DT_RELR_INTRODUCED",
        "dt_relr_introduced",
        "Packed relative relocations (DT_RELR, `-z pack-relative-relocs`, -- binutils ≥ 2.38 default on some distros). A DT_RELR binary requires -- glibc ≥ 2.36 (or an equivalent loader) — glibc marks this with the -- synthetic GLIBC_ABI_DT_RELR verneed. -- → RISK (raises loader floor)",
    ),
    (
        "DT_RELR_REMOVED",
        "dt_relr_removed",
        "→ COMPATIBLE (broadens loader compatibility)",
    ),
    (
        "RPATH_TYPE_CHANGED",
        "rpath_type_changed",
        "DT_RPATH ↔ DT_RUNPATH flip (ld --enable-new-dtags default drift): -- DT_RPATH applies to the whole dependency subtree and ignores -- LD_LIBRARY_PATH; DT_RUNPATH applies only to direct deps and is -- overridden by LD_LIBRARY_PATH — same paths, different lookup semantics. -- → RISK",
    ),
    (
        "HASH_STYLE_REMOVED",
        "hash_style_removed",
        "A symbol-hash table style (.hash SysV / .gnu.hash GNU) present in the -- old binary is gone (ld --hash-style default drift). Loaders/tools that -- only support the dropped style can no longer resolve symbols. -- → RISK",
    ),
    (
        "TIME64_ABI_CHANGED",
        "time64_abi_changed",
        "time64/LFS ABI flip: time_t/off_t-family public typedefs flipped width -- together (_TIME_BITS=64 / _FILE_OFFSET_BITS=64, glibc ≥ 2.34 option) — -- one root cause behind mass parameter/field width churn on 32-bit targets. -- → BREAKING",
    ),
    (
        "IMPORTED_SYMBOL_ADDED",
        "imported_symbol_added",
        "── Coverage extension: dynamic-loader / import-surface facts ──────────── -- binary gained an undefined (imported) symbol — new obligation on the consumer's link environment → RISK",
    ),
    (
        "IMPORTED_SYMBOL_REMOVED",
        "imported_symbol_removed",
        "binary dropped an undefined (imported) symbol — one fewer external obligation → COMPATIBLE (quality)",
    ),
    (
        "INTERPRETER_CHANGED",
        "interpreter_changed",
        "PT_INTERP program interpreter path changed → RISK",
    ),
    (
        "BIND_NOW_DISABLED",
        "bind_now_disabled",
        "DT_BIND_NOW/DF_BIND_NOW/DF_1_NOW dropped — eager→lazy binding, unresolved symbols surface at call time instead of load time → RISK",
    ),
    (
        "ELF_ENDIANNESS_CHANGED",
        "elf_endianness_changed",
        "EI_DATA byte order flipped (LSB ↔ MSB) → BREAKING",
    ),
    (
        "X86_ISA_BASELINE_RAISED",
        "x86_isa_baseline_raised",
        "GNU_PROPERTY_X86_ISA_1_NEEDED gained a level (e.g. x86-64-v2 → v3) — old CPUs can no longer run the library → RISK",
    ),
    (
        "OS_DEPLOYMENT_FLOOR_RAISED",
        "os_deployment_floor_raised",
        "minimum OS/kernel floor raised (Mach-O minos, PE subsystem version, ELF NT_GNU_ABI_TAG) → RISK",
    ),
    (
        "DYNAMIC_LOADING_FLAGS_CHANGED",
        "dynamic_loading_flags_changed",
        "DF_1_NODELETE / DF_1_NOOPEN / DF_1_ORIGIN toggled — dlopen/dlclose contract changed → RISK",
    ),
    (
        "EXPORTED_OBJECT_ALIGNMENT_REDUCED",
        "exported_object_alignment_reduced",
        "exported data object's address alignment dropped — copy-relocation / aligned-access hazard → RISK",
    ),
    (
        "ELF_INIT_FINI_CHANGED",
        "elf_init_fini_changed",
        "DT_INIT/DT_FINI/DT_INIT_ARRAY/DT_FINI_ARRAY presence changed — load/unload-time code contract changed → RISK",
    ),
    (
        "ALLOCATOR_REPLACEMENT_ADDED",
        "allocator_replacement_added",
        "library newly exports global operator new/delete — hijacks allocation for the whole process → RISK",
    ),
    (
        "ALLOCATOR_REPLACEMENT_REMOVED",
        "allocator_replacement_removed",
        "library stopped exporting global operator new/delete — consumers relying on the replacement get the default allocator → RISK",
    ),
    (
        "PE_HARDENING_WEAKENED",
        "pe_hardening_weakened",
        "── Coverage extension: PE/COFF (Windows) ──────────────────────────────── -- DllCharacteristics lost NX/ASLR/CFG/HIGH_ENTROPY_VA hardening bits → RISK",
    ),
    (
        "PE_HARDENING_IMPROVED",
        "pe_hardening_improved",
        "DllCharacteristics gained hardening bits → COMPATIBLE (quality)",
    ),
    (
        "LIBRARY_VERSION_DOWNGRADED",
        "library_version_downgraded",
        "embedded library version regressed (PE VS_FIXEDFILEINFO / Mach-O LC_ID_DYLIB current_version) → RISK",
    ),
    (
        "MACHO_FILETYPE_CHANGED",
        "macho_filetype_changed",
        "── Coverage extension: Mach-O (macOS) ─────────────────────────────────── -- Mach-O filetype changed (e.g. MH_DYLIB → MH_BUNDLE): no longer linkable the same way → BREAKING",
    ),
    (
        "MACHO_LINKAGE_FLAGS_CHANGED",
        "macho_linkage_flags_changed",
        "MH_TWOLEVEL / MH_WEAK_DEFINES / MH_BINDS_TO_WEAK / MH_NO_REEXPORTED_DYLIBS flipped → RISK",
    ),
    (
        "MACHO_REEXPORT_CHANGED",
        "macho_reexport_changed",
        "LC_REEXPORT_DYLIB target repointed — same re-export slot now sourced from a different dylib → RISK",
    ),
    (
        "FUNC_VARIADIC_ADDED",
        "func_variadic_added",
        "── Coverage extension: language-level contracts ───────────────────────── -- function gained a C ellipsis (...) — variadic call convention differs (%al on SysV x86-64, stack on AArch64 Darwin) → BREAKING",
    ),
    (
        "FUNC_VARIADIC_REMOVED",
        "func_variadic_removed",
        "function lost its C ellipsis (...) — callers passing extra args break → BREAKING",
    ),
    (
        "FUNC_CONTRACT_ATTRIBUTE_ADDED",
        "func_contract_attribute_added",
        "function gained a semantic contract attribute (nonnull/noreturn/format/alloc_size/malloc/warn_unused_result/...) → RISK",
    ),
    (
        "FUNC_CONTRACT_ATTRIBUTE_REMOVED",
        "func_contract_attribute_removed",
        "function lost a semantic contract attribute callers may rely on → RISK",
    ),
    (
        "VAR_ALIGNMENT_CHANGED",
        "var_alignment_changed",
        "exported variable's declared alignment changed → BREAKING",
    ),
    (
        "FUNC_EXCEPTION_SPEC_CHANGED",
        "func_exception_spec_changed",
        "dynamic exception specification (throw(...)) changed in a way not covered by the noexcept kinds → RISK",
    ),
    (
        "ENUM_BECAME_SCOPED",
        "enum_became_scoped",
        "── CastXML schema-completeness (deprecation, scoped enums, override) ──── -- `enum class`/`enum struct` (C++11 scoped enumeration) transitions -- (header/castxml only). -- unscoped → scoped: unqualified enumerator lookup and implicit-int conversions stop compiling → API_BREAK",
    ),
    (
        "ENUM_LOST_SCOPED",
        "enum_lost_scoped",
        "scoped → unscoped: implicit-int conversions silently reappear → COMPATIBLE_WITH_RISK",
    ),
    (
        "FUNC_OVERRIDE_SPECIFIER_ADDED",
        "func_override_specifier_added",
        "Explicit C++11 `override` specifier on a virtual method (header/castxml -- only — distinct from FUNC_VIRTUAL_REMOVED/vtable-slot kinds, which -- already catch an actual dispatch break; this is the source-level -- self-documentation marker alone). -- gained `override` → COMPATIBLE (quality: self-documents an existing override relationship)",
    ),
    (
        "FUNC_OVERRIDE_SPECIFIER_REMOVED",
        "func_override_specifier_removed",
        "lost `override` while still virtual → COMPATIBLE_WITH_RISK (may signal the override relationship silently broke elsewhere)",
    ),
    (
        "FUNC_DEPRECATED_ADDED",
        "func_deprecated_added",
        '`[[deprecated]]`/`[[deprecated("msg")]]` transitions (header/castxml -- only). One pair per surface kind, matching the existing per-entity-kind -- convention (is_final on types, is_explicit on functions, ...). -- function gained [[deprecated]] → COMPATIBLE (quality: advance notice)',
    ),
    (
        "FUNC_DEPRECATED_REMOVED",
        "func_deprecated_removed",
        "function lost [[deprecated]] → COMPATIBLE (quality)",
    ),
    (
        "VAR_DEPRECATED_ADDED",
        "var_deprecated_added",
        "variable gained [[deprecated]] → COMPATIBLE (quality)",
    ),
    (
        "VAR_DEPRECATED_REMOVED",
        "var_deprecated_removed",
        "variable lost [[deprecated]] → COMPATIBLE (quality)",
    ),
    (
        "TYPE_DEPRECATED_ADDED",
        "type_deprecated_added",
        "class/struct/union gained [[deprecated]] → COMPATIBLE (quality)",
    ),
    (
        "TYPE_DEPRECATED_REMOVED",
        "type_deprecated_removed",
        "class/struct/union lost [[deprecated]] → COMPATIBLE (quality)",
    ),
    (
        "ENUM_DEPRECATED_ADDED",
        "enum_deprecated_added",
        "enum gained [[deprecated]] → COMPATIBLE (quality)",
    ),
    (
        "ENUM_DEPRECATED_REMOVED",
        "enum_deprecated_removed",
        "enum lost [[deprecated]] → COMPATIBLE (quality)",
    ),
    (
        "FIELD_DEPRECATED_ADDED",
        "field_deprecated_added",
        "struct/class field gained [[deprecated]] → COMPATIBLE (quality)",
    ),
    (
        "FIELD_DEPRECATED_REMOVED",
        "field_deprecated_removed",
        "struct/class field lost [[deprecated]] → COMPATIBLE (quality)",
    ),
    (
        "RUNTIME_SYMBOL_PROVIDER_CHANGED",
        "runtime_symbol_provider_changed",
        "── Composition compatibility (Wave A: runtime binding / loader / PE / wchar) ── -- a consumer's symbol reference resolves to a different provider DSO across environments → RISK",
    ),
    (
        "RUNTIME_WEAK_RESOLUTION_CHANGED",
        "runtime_weak_resolution_changed",
        "a weak symbol reference flipped between resolved and unresolved across environments → RISK",
    ),
    (
        "NEEDED_ORDER_CHANGED",
        "needed_order_changed",
        "DT_NEEDED entries reordered with the dependency set unchanged — can silently change which DSO wins a non-versioned lookup → RISK",
    ),
    (
        "SYMBOLIC_BINDING_MODE_CHANGED",
        "symbolic_binding_mode_changed",
        "DT_SYMBOLIC/DF_SYMBOLIC toggled — self-references resolve to own definitions before global scope → RISK",
    ),
    (
        "TEXT_RELOCATION_INTRODUCED",
        "text_relocation_introduced",
        "DF_TEXTREL/DT_TEXTREL gained — loader must write into the text segment, defeating W^X/text-segment sharing → RISK",
    ),
    (
        "TEXT_RELOCATION_REMOVED",
        "text_relocation_removed",
        "DF_TEXTREL/DT_TEXTREL dropped — text segment stays read-only/shared again → COMPATIBLE (quality)",
    ),
    (
        "PE_ORDINAL_RETARGETED",
        "pe_ordinal_retargeted",
        "a consumer's ordinal-only PE import now resolves to a different exported function → BREAKING",
    ),
    (
        "PE_IMPORT_LOAD_MODE_CHANGED",
        "pe_import_load_mode_changed",
        "an imported DLL function moved between eager (IAT) and delay-loaded → RISK",
    ),
    (
        "WCHAR_MODEL_CHANGED",
        "wchar_model_changed",
        "-fshort-wchar drift changes wchar_t size/signedness with no symbol-level signal → RISK",
    ),
    (
        "CONSUMER_REQUIRED_SYMBOL_REMOVED",
        "consumer_required_symbol_removed",
        "ADR-044 P2 item 1: promotes --used-by's (ADR-005/043) previously ad-hoc -- \"missing symbol\" string into a first-class, suppressible ChangeKind — a -- real consumer binary's own undefined-symbol table (ELF/PE/Mach-O) is -- empirical ground truth independent of any header/namespace reasoning. -- a real consumer binary's required dynamic symbol is no longer exported by the new library → BREAKING",
    ),
    (
        "NUMPY_CAPI_CONSUMPTION_ADDED",
        "numpy_capi_consumption_added",
        "── NumPy C-API compatibility envelope (G26) ────────────────────────────── -- The NumPy C-API is consumed through an indirect function-pointer table -- (_ARRAY_API/_UFUNC_API, populated by import_array()/import_ufunc()), not -- ordinary dynamic symbol imports — invisible to symbol-table diffing. -- Binary evidence: NumPy's generated import_array()/import_umath() shims -- embed stable literal strings a rodata scan recovers reliably. -- module gained a NumPy C-API dependency ordinary symbol diffing can't see → RISK",
    ),
    (
        "NUMPY_CAPI_CONSUMPTION_REMOVED",
        "numpy_capi_consumption_removed",
        "module dropped its NumPy C-API dependency → COMPATIBLE",
    ),
    (
        "NUMPY_TARGET_FLOOR_RAISED",
        "numpy_target_floor_raised",
        "the module's NumPy C-API usage now requires a newer minimum NumPy (NPY_TARGET_VERSION rose) → RISK",
    ),
    (
        "NUMPY_METADATA_UNDERSTATES_REQUIRED_VERSION",
        "numpy_metadata_understates_required_version",
        "wheel's declared numpy requirement is looser than the binary's own NumPy C-API target → RISK",
    ),
    (
        "NUMPY_ABI_MAJOR_INCOMPATIBLE",
        "numpy_abi_major_incompatible",
        "binary's NumPy C-API target crosses the 1.x/2.x ABI boundary above what the declared numpy requirement allows — a real import crash, not just a stale metadata claim → BREAKING",
    ),
)
