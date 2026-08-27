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


"""ADR-061 D9 taxonomy: platform/binary-format-level ChangeKind entries.

ELF/PE/Mach-O container facts, DWARF/debug-info presence, symbol-table
*representation* (binding, ELF visibility, versioning), hardening flags
(RELRO, stack canary, CET/BTI, PIE), toolchain-mode ABI traits
(exceptions/RTTI/TLS model, calling convention, vector ABI), symbol
versioning, kernel ABI (kABI) facts, and the SYCL plugin-interface ABI --
everything that is a fact about the *binary artifact's* format or the
platform ABI it targets, as opposed to a fact about the source-level
declaration that produced it.

Categorized by which detector module actually produces each kind (verified
against the real ``ChangeKind.X`` construction sites in ``diff_platform.py``
and its siblings -- ``diff_platform_elf_dynamic.py``,
``diff_platform_elf_symbols.py``, ``diff_versioning.py``,
``versioned_symbol_scheme.py``, ``diff_kabi.py``, ``diff_sycl.py``,
``stack_binding_diff.py`` -- not by which flat ``change_registry_*.py``
sibling an entry happened to live in for pure line-count reasons before
this migration.
"""

from __future__ import annotations

from .registry import ChangeKindMeta, Verdict

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta

PLATFORM_ENTRIES: list[ChangeKindMeta] = [
    _E("abi_surface_explosion", _C,
       impact="Public ABI surface grew or shrank dramatically (e.g. lost "
              "-fvisibility=hidden). This is a configuration/packaging signal, not "
              "a per-symbol break, but may indicate an unintended visibility regression.",
       description_template="ABI surface {detail} dramatically: {old} → {new} exported symbols ({name}); check -fvisibility=hidden and version scripts"),
    _E("abi_tag_changed", _B,
       impact="The Itanium ABI-tag set on a symbol changed (e.g. it gained or "
              "lost `[abi:cxx11]` / a `[[gnu::abi_tag]]`). The mangled name "
              "encodes the tag, so old binaries reference a symbol that no "
              "longer exists under that name. Distinct from a mass dual-ABI "
              "flip: this is a per-symbol tag change.",
       description_template="ABI-tag set changed for '{name}': {detail}. The mangled name encodes the tag, so the old symbol ({old}) no longer exists under that name ({new})."),
    _E("allocator_replacement_added", _R,
       impact="The library newly exports a global operator new/delete "
              "replacement. Once loaded, it interposes allocation for the "
              "whole process: objects allocated before load (or by other "
              "DSOs' inlined allocators) can be freed by the replacement — a "
              "mismatched-allocator heap corruption hazard.",
       description_template="Global allocator replacement introduced: {detail}"),
    _E("allocator_replacement_removed", _R,
       impact="The library stopped exporting its global operator new/delete "
              "replacement. Consumers whose allocations previously routed "
              "through the replacement now silently get the default "
              "allocator; memory pools, tracking, or alignment guarantees the "
              "replacement provided disappear.",
       description_template="Global allocator replacement removed: {detail}"),
    _E("bind_now_disabled", _R,
       impact="DT_BIND_NOW/DF_BIND_NOW/DF_1_NOW was dropped: symbol binding "
              "reverts from eager (all relocations resolved at load) to lazy. "
              "Unresolved symbols that used to fail fast at load time now "
              "crash at first call, and full RELRO's GOT protection no longer "
              "applies in practice.",
       description_template="Eager binding (BIND_NOW) disabled"),
    _E("bit_int_width_changed", _B,
       impact="A public use of C23 `_BitInt(N)` changed its width N between "
              "versions, or a field/param type changed to/from `_BitInt(N)`. "
              "The bit width determines the storage size and calling-convention "
              "treatment, so old code reads/writes the value with the wrong "
              "width.",
       description_template="_BitInt change on {name}: {detail} ({old} → {new}). The bit width determines storage size and ABI treatment."),
    _E("branch_protection_improved", _C,
       impact="An AArch64 branch-protection feature (BTI/PAC) was added to "
              ".note.gnu.property — a hardening improvement. Informational.",
       description_template="Branch protection improved: {old} → {new}"),
    _E("branch_protection_weakened", _R,
       impact="An AArch64 branch-protection feature (BTI and/or PAC) was dropped "
              "from .note.gnu.property. Like CET, BTI enforcement is process-wide, "
              "so a single non-BTI DSO weakens the guarantee for the whole link "
              "map. RISK by default; gated to break by the security policy.",
       description_template="Branch protection weakened: {old} → {new}"),
    _E("cet_protection_improved", _C,
       impact="An x86 CET feature (IBT/SHSTK) was added to .note.gnu.property — "
              "a hardening improvement. Informational.",
       description_template="CET protection improved: {old} → {new}"),
    _E("cet_protection_weakened", _R,
       impact="An x86 CET control-flow-protection feature (IBT and/or SHSTK) was "
              "dropped from .note.gnu.property. CET is enforced per link map: a "
              "single non-IBT DSO disables indirect-branch tracking for the whole "
              "process, so weakening it silently lowers the runtime hardening of "
              "every consumer. RISK by default; the shipped security policy gates "
              "it to break.",
       description_template="CET protection weakened: {old} → {new}"),
    _E("char8t_migration", _B,
       impact="A public parameter, return, or field type changed between a "
              "char-family spelling (char / unsigned char) and C++20 `char8_t`. "
              "`char8_t` is a distinct type that participates in overload "
              "resolution and name mangling, so the mangled symbol changes and "
              "old binaries fail to resolve it.",
       description_template="char8_t migration ({detail}) on {name}: {old} → {new}. char8_t is a distinct C++20 type that changes overload identity and name mangling."),
    _E("char_signedness_changed", _R,
       impact="The signedness of a plain `char` changed between builds "
              "(-fsigned-char ↔ -funsigned-char; the default is target-dependent). "
              "`char`, `signed char` and `unsigned char` are three distinct types, "
              "so a plain-`char` parameter or member reinterprets the same bytes "
              "with the opposite sign, silently changing comparisons and value "
              "range in consumer code recompiled against the other setting. Symbol "
              "names are unchanged, so only the captured build flag exposes it. "
              "Build consumers with the matching char signedness."),
    _E("common_symbol_risk", _C,
       impact="An exported symbol is a tentative definition (STT_COMMON); "
              "its final address and merge behavior across translation "
              "units is decided by the linker, which can differ across "
              "toolchains/link orders. Not itself a break, but a source of "
              "non-determinism worth being aware of.",
       description_template="Exported STT_COMMON symbol: {name} (resolution depends on linker/loader)"),
    _E("compat_version_changed", _B,
       impact="Mach-O compatibility version changed; dylibs linked against old version may fail to load.",
       description_template="compatibility version changed: {old} → {new}"),
    _E("dt_relr_introduced", _R,
       impact="The linker enabled packed relative relocations (DT_RELR, "
              "`-z pack-relative-relocs`; default on some distros since "
              "binutils 2.38). A DT_RELR binary requires glibc ≥ 2.36 (or an "
              "equivalent loader) — older dynamic loaders refuse to load it. "
              "glibc marks this with a synthetic GLIBC_ABI_DT_RELR version "
              "requirement. Rebuild with `-z nopack-relative-relocs` to keep "
              "supporting older runtimes.",
       description_template="Packed relative relocations introduced (DT_RELR): requires glibc >= 2.36 or equivalent loader"),
    _E("dt_relr_removed", _C,
       impact="Packed relative relocations (DT_RELR) were dropped; the binary "
              "loads on older dynamic loaders again. Slightly larger relocation "
              "tables, no compatibility cost.",
       description_template="Packed relative relocations removed (DT_RELR): loader floor lowered"),
    _E("dwarf_info_missing", _C,
       impact="The new binary carries no DWARF debug info — this detector "
              "only checks whether DWARF is present, not why it's absent, "
              "so this fires whether the binary was never compiled with "
              "-g or was compiled with -g and then stripped. Either way, "
              "DWARF-derived layout comparisons couldn't run for it — but "
              "layout isn't necessarily unchecked entirely: a separate "
              "detector compares layout from header-AST evidence "
              "independently of DWARF and still runs when both sides "
              "carry it, so a normal header-plus-binary comparison can "
              "still catch a layout change here. Only an ELF/DWARF-only "
              "comparison (no header evidence) loses coverage entirely. "
              "Not itself an ABI break; ensure debug info is present and "
              "re-scan to restore full DWARF-derived coverage.",
       description_template="New binary has no DWARF debug info — struct/enum layout comparison was skipped. Ensure debug info is present (compiled in or supplied separately) to enable."),
    _E("dynamic_loading_flags_changed", _R,
       impact="DF_1_NODELETE / DF_1_NOOPEN / DF_1_ORIGIN toggled in "
              "DT_FLAGS_1. These flags change the dlopen/dlclose contract: "
              "NODELETE pins the library in memory (dlclose becomes a no-op), "
              "NOOPEN forbids loading via dlopen entirely, ORIGIN changes "
              "$ORIGIN-relative path resolution. Plugin hosts and consumers "
              "relying on the previous behaviour break at runtime.",
       description_template="Dynamic loading flags changed: {detail}"),
    _E("elf_abi_flags_changed", _B,
       impact="The ELF e_flags ABI-selecting bits changed — the float ABI "
              "(hard/soft-float), EABI version, or base ISA differs between "
              "versions. Object code compiled against the old convention passes "
              "floating-point arguments in the wrong registers/stack slots, "
              "silently corrupting calls. Artifact-proven from e_flags; the "
              "flag-level FLOAT_ABI_CHANGED (L3) stays the explanatory signal.",
       description_template="ELF ABI flags changed: {old} → {new}"),
    _E("elf_class_changed", _B,
       impact="The ELF class changed between 32-bit and 64-bit. Pointer width, "
              "type sizes, and the calling convention all differ; no consumer "
              "built against one class can use the other.",
       description_template="ELF class changed: {old}-bit → {new}-bit"),
    _E("elf_endianness_changed", _B,
       impact="The ELF data encoding (EI_DATA) flipped between little- and "
              "big-endian. The two binaries target different byte orders and "
              "cannot be loaded by the same consumers — every multi-byte value "
              "is reinterpreted.",
       description_template="ELF endianness changed: {old} → {new}"),
    _E("elf_init_fini_changed", _R,
       impact="The presence of load/unload-time code (DT_INIT/DT_FINI/"
              "DT_INIT_ARRAY/DT_FINI_ARRAY) changed. Gaining constructors "
              "means code now runs on dlopen before any API call — new "
              "failure modes and ordering constraints; losing destructors "
              "means cleanup consumers relied on no longer happens at "
              "dlclose/exit.",
       description_template="ELF init/fini sections changed: {detail}"),
    _E("elf_machine_changed", _B,
       impact="The ELF e_machine (target architecture) changed. The two inputs "
              "are different-architecture binaries — nothing about their ABI is "
              "comparable, and a consumer built for one cannot load the other. "
              "The ELF-side analogue of PE_MACHINE_CHANGED / MACHO_CPU_TYPE_CHANGED.",
       description_template="ELF machine changed: {old} → {new} — different target architecture"),
    _E("elf_osabi_changed", _R,
       impact="The ELF EI_OSABI (target OS ABI) changed (e.g. SYSV ↔ GNU/Linux ↔ "
              "FreeBSD). This can alter the meaning of OS-specific symbol types "
              "and relocations; consumers may resolve or load differently. RISK.",
       description_template="ELF OS ABI changed: {old} → {new}"),
    _E("enum_size_flag_changed", _R,
       impact="The enum storage-size policy was toggled between builds "
              "(-fshort-enums ↔ default). With -fshort-enums the compiler picks the "
              "smallest integer type that holds an enum's range instead of a full "
              "int, so an enum member of a public struct, an enum-typed parameter, "
              "or an enum return value changes size and (as a struct member) shifts "
              "every field after it. Symbol names are unchanged, so a symbol-only "
              "check is blind; the artifact/type diff confirms any concrete layout "
              "break. Build all consumers with the matching -fshort-enums setting."),
    _E("enum_underlying_size_changed", _B,
       impact="Enum underlying type changed (e.g. int→long); affects ABI of functions passing enums by value.",
       description_template="Enum underlying type size changed: {name} ({old} → {new} bytes)"),
    _E("exceptions_mode_changed", _R,
       impact="C++ exception support was toggled between builds (-fexceptions ↔ "
              "-fno-exceptions). The two modes are not link-compatible: an "
              "exception thrown in -fexceptions code that unwinds through a frame "
              "compiled with -fno-exceptions is undefined behaviour (it calls "
              "std::terminate at best), and -fno-exceptions changes the codegen "
              "and emitted cleanup/EH tables of every public inline that uses "
              "throw/try/catch. If the public API exposes exception types or "
              "throwing inlines, rebuild all consumers in the matching mode."),
    _E("executable_stack", _C,
       impact="Library has executable stack (PT_GNU_STACK RWE); NX protection disabled — security risk.",
       description_template="Executable stack detected: library linked with -Wl,-z,execstack — NX protection disabled (security risk)"),
    _E("executable_stack_removed", _C,
       impact="Executable stack removed (PT_GNU_STACK RWE→RW); NX protection restored — a hardening improvement, not a regression.",
       description_template="Executable stack removed: library now uses a non-executable stack — NX protection restored (good practice)"),
    _E("exported_object_alignment_reduced", _R,
       impact="An exported data object's address alignment dropped. Consumers "
              "that copy-relocate the object (non-PIC executables) allocated "
              "space with the old alignment guarantee, and code compiled "
              "against the old headers may use aligned loads (SIMD) that now "
              "fault or fall back to slow paths.",
       description_template="Exported object alignment reduced: {name} ({old} → {new} bytes)"),
    _E("float_abi_changed", _R,
       impact="The floating-point calling convention changed between builds "
              "(-mfloat-abi=soft/softfp/hard; the default is target-dependent). On "
              "ARM the float ABI decides whether floating-point arguments and "
              "returns travel in FP registers (hard) or core registers/memory "
              "(soft), so a function taking or returning a float/double is called "
              "with an incompatible convention across the boundary — a silent "
              "corruption or crash. Build the whole stack with one float ABI."),
    _E("fortify_source_weakened", _R,
       impact="_FORTIFY_SOURCE fortified libc wrappers no longer referenced; compile-time/runtime buffer-overflow checks were dropped.",
       description_template="FORTIFY_SOURCE weakened: fortified libc wrappers (*_chk) no longer referenced"),
    _E("frame_register_changed", _B,
       impact="The dominant canonical-frame-address register recorded in "
              "the function's CFI (.eh_frame/.debug_frame) changed — e.g. "
              "rbp vs. rsp, commonly from a `-fomit-frame-pointer` "
              "rebuild. A tool that reads the real CFI (a standard "
              "DWARF-aware debugger or unwinder) walks the new frame "
              "correctly regardless, since the new CFI describes it; only "
              "a tool that assumes a frame-pointer chain instead of "
              "reading CFI, or one working from stale/cached unwind "
              "information for this function, can misinterpret the new "
              "convention. Ordinary calls into the function are "
              "unaffected either way.",
       policy_overrides={"plugin_abi": _C}),
    _E("func_deleted_elf_fallback", _B,
       impact="The exported symbol vanished from the dynamic symbol table "
              "with no explicit `= delete`/removal marker in the header "
              "the diff could otherwise attribute it to; an already-"
              "linked consumer calling it fails to resolve the symbol at "
              "load time.",
       description_template="Symbol disappeared from ELF .dynsym without explicit deletion marker: {name} — was exported in old library, absent in new library's dynamic symbol table while header still declares it"),
    _E("func_visibility_protected_changed", _C,
       impact="Symbol visibility changed to STV_PROTECTED. The symbol remains exported and "
              "is still resolvable by external consumers. Interposition via LD_PRELOAD no "
              "longer works for calls originating inside the library itself — intentional "
              "by the library author. Existing compiled consumers are unaffected.",
       description_template="ELF symbol visibility changed: {name} ({old} → {new}); symbol still exported, interposition semantics changed"),
    _E("glibcxx_dual_abi_flip_detected", _C,
       impact="Mass symbol churn detected that matches a libstdc++ dual ABI toggle "
              "(_GLIBCXX_USE_CXX11_ABI). Individual removed/added symbols are likely "
              "caused by this single root cause rather than intentional API changes.",
       description_template="libstdc++ dual ABI flip detected ({detail}): {name} churned symbols contain CXX11 ABI markers; likely caused by _GLIBCXX_USE_CXX11_ABI toggle"),
    _E("hash_style_removed", _R,
       impact="A symbol-hash table style present in the old binary was dropped "
              "(ld --hash-style default drift): SysV `.hash` and/or GNU "
              "`.gnu.hash`. Dynamic loaders and tools that only support the "
              "dropped style (very old glibc, some non-GNU loaders, MIPS "
              "toolchains for `.hash`) can no longer resolve symbols from this "
              "library.",
       description_template="Symbol hash table style removed: {old} → {new}"),
    _E("ifunc_introduced", _C,
       impact="IFUNC resolver indirection added; transparent to well-behaved callers.",
       description_template="Symbol became GNU_IFUNC: {name}"),
    _E("ifunc_removed", _C,
       impact="IFUNC removed; transparent to callers.",
       description_template="Symbol no longer GNU_IFUNC: {name}"),
    _E("imported_symbol_added", _R,
       impact="The binary gained an undefined (imported) symbol — a new "
              "obligation the consumer's link environment must satisfy at load "
              "time. If none of the loaded dependencies provide it, the dynamic "
              "linker fails with an unresolved-symbol error. Weak imports are "
              "exempt (they resolve to null instead of failing).",
       description_template="New imported symbol: {name}{detail}"),
    _E("imported_symbol_removed", _C,
       impact="The binary dropped an undefined (imported) symbol — one fewer "
              "external obligation. Existing consumers are unaffected.",
       description_template="Imported symbol no longer required: {name}{detail}"),
    _E("inline_namespace_moved", _B,
       impact="Symbols moved to a different inline namespace (e.g. v1:: → v2::); "
              "mangled names change so old binaries fail to resolve the symbols.",
       description_template="Inline namespace move detected: {detail} symbols appear to have moved between inline namespace versions (e.g. ::v1:: → ::v2::); mangled names changed"),
    _E("integer_model_changed", _B,
       impact="A large fraction of public integer parameters/returns flipped "
              "width together (e.g. int→long, int32_t→int64_t), or a public "
              "integer typedef changed its underlying size. This is the "
              "signature of an LP64↔ILP64 model switch (e.g. a BLAS-style "
              "`INT` typedef built for the 32-bit vs 64-bit integer interface). "
              "Every caller "
              "passes/reads integers with the wrong width; arguments and array "
              "indices are silently truncated or sign-extended.",
       description_template="Integer model changed ({new}): {detail}. This is the signature of an LP64↔ILP64 switch (e.g. oneMKL's 32-bit vs 64-bit MKL_INT interface); every caller passes/reads integers with the wrong width."),
    _E("interpreter_changed", _R,
       impact="The ELF program interpreter (PT_INTERP) path changed. For an "
              "executable this repoints which dynamic linker runs it; a wrong "
              "or missing path fails at exec time with a cryptic ENOENT.",
       description_template="ELF interpreter changed: {old} → {new}"),
    _E("kabi_crc_changed", _B,
       impact="A kernel-exported symbol's genksyms CRC changed. Even though the "
              "symbol still exists, CONFIG_MODVERSIONS embeds the old CRC in "
              "out-of-tree modules and the loader rejects the module ('disagrees "
              "about version of symbol') — the type signature behind the symbol "
              "changed.",
       description_template="Kernel symbol CRC changed: {name} ({old} → {new}) — modversions will reject the module"),
    _E("kabi_export_type_changed", _A,
       impact="A kernel-exported symbol changed between EXPORT_SYMBOL and "
              "EXPORT_SYMBOL_GPL. A non-GPL module that used a symbol now marked "
              "GPL-only can no longer link against it — a license-gated "
              "availability break for that class of consumer.",
       description_template="Kernel symbol export type changed: {name} ({old} → {new})"),
    _E("kabi_symbol_added", _C, is_addition=True,
       impact="A new kernel-exported symbol appeared; existing modules are unaffected.",
       description_template="New kernel-exported symbol: {name}"),
    _E("kabi_symbol_namespace_changed", _B,
       impact="A kernel-exported symbol gained or moved its export namespace "
              "(EXPORT_SYMBOL_NS*). A module that does not declare the matching "
              "MODULE_IMPORT_NS() fails to load, so a gained/changed namespace is a "
              "load-time break for existing modules.",
       description_template="Kernel symbol namespace changed: {name} ({old} → {new})"),
    _E("kabi_symbol_removed", _B,
       impact="A kernel-exported symbol (EXPORT_SYMBOL*) was removed from "
              "Module.symvers. Out-of-tree modules that reference it fail to load "
              "with 'Unknown symbol'.",
       description_template="Kernel-exported symbol removed: {name}"),
    _E("library_version_downgraded", _R,
       impact="The embedded library version regressed (PE VS_FIXEDFILEINFO "
              "FileVersion or Mach-O LC_ID_DYLIB current_version). Installers "
              "and side-by-side logic that compare file versions may refuse "
              "to replace the file or silently keep the older copy, and a "
              "downgrade usually signals a mispackaged artifact.",
       description_template="Library version downgraded: {old} → {new}"),
    _E("long_double_abi_changed", _B,
       impact="A function's `long double` parameter or return representation "
              "changed — e.g. ppc64 migrating IBM double-double ↔ IEEE binary128, "
              "or `-mlong-double-64` shrinking 80-bit x87 to 64-bit. The source "
              "signature is unchanged, but the floating-point format differs, so "
              "old binaries pass/return the value in the wrong size and bit layout, "
              "silently corrupting it. Detected from the Itanium long-double "
              "mangling token (`e`/`g`/`u9__ieee128`) on a removed↔added pair, or "
              "from the `long double` DWARF byte size on a persisting symbol.",
       description_template="long double ABI changed: {detail} — floating-point representation differs (symbol {old} → {new})"),
    _E("lto_mode_changed", _R,
       impact="Link-time optimization was toggled between builds (-flto ↔ no LTO, "
              "or with -fwhole-program-vtables). LTO changes cross-TU inlining and "
              "can devirtualize or drop vtable/typeinfo emission the linker would "
              "otherwise keep, so the emitted symbol set and inlined public-inline "
              "bodies can differ from a non-LTO build of the same source. A risk "
              "signal to review; the artifact diff proves any concrete symbol/layout "
              "break. Prefer a single LTO policy across the library and consumers."),
    _E("macho_cpu_type_changed", _B,
       impact="A Mach-O architecture slice that used to ship is gone (e.g. a universal "
              "x86_64+arm64 dylib dropped its x86_64 slice, or x86_64 → arm64). Existing "
              "clients built for the removed architecture can no longer link against or load "
              "the dylib. Adding slices (single-arch → universal) is not flagged.",
       description_template="Mach-O architecture slice removed: {detail} no longer present ({old} → {new}); existing clients of the dropped arch can no longer load the dylib"),
    _E("macho_filetype_changed", _B,
       impact="The Mach-O filetype changed (e.g. MH_DYLIB → MH_BUNDLE). A "
              "dylib can be linked against at build time; a bundle can only "
              "be dlopen()ed. Consumers that link the old file kind cannot "
              "use the new one at all.",
       description_template="Mach-O filetype changed: {old} → {new}"),
    _E("macho_linkage_flags_changed", _R,
       impact="Mach-O header linkage flags flipped (MH_TWOLEVEL two-level "
              "namespace, MH_WEAK_DEFINES, MH_BINDS_TO_WEAK, "
              "MH_NO_REEXPORTED_DYLIBS). Symbol resolution semantics change: "
              "flat vs two-level lookup can rebind symbols to different "
              "providers, and weak-definition coalescing behaviour differs.",
       description_template="Mach-O linkage flags changed: {detail}"),
    _E("macho_reexport_changed", _R,
       impact="A re-exported dylib (LC_REEXPORT_DYLIB) was repointed to a "
              "different target. The umbrella's exported surface is now "
              "sourced from a different library — symbols may resolve to "
              "different implementations or disappear on systems where the "
              "new target differs.",
       description_template="Re-exported dylib repointed: {old} → {new}"),
    _E("needed_added", _C,
       impact="New shared library dependency; may not be available on target systems."),
    _E("needed_order_changed", _R,
       impact="The DT_NEEDED dependency list was reordered while the set of "
              "dependencies stayed the same. The System V ABI's dynamic linker "
              "searches dependencies breadth-first in DT_NEEDED order, so a "
              "pure reorder can silently change which DSO wins the lookup for "
              "a non-versioned symbol defined in more than one dependency. Not "
              "proven breaking on its own — pair with a runtime binding check "
              "to confirm an actual provider changed.",
       description_template="DT_NEEDED order changed: {old} → {new}"),
    _E("needed_removed", _C,
       impact="Dependency removed; should be transparent to consumers."),
    _E("os_deployment_floor_raised", _R,
       impact="The minimum OS/kernel version the binary declares was raised "
              "(Mach-O LC_BUILD_VERSION minos, PE MajorSubsystemVersion, or "
              "ELF NT_GNU_ABI_TAG kernel floor). Consumers on OS versions in "
              "the dropped range can no longer load or run the library even "
              "though its symbol surface is unchanged.",
       description_template="OS deployment floor raised: {old} → {new}"),
    _E("pe_forwarder_changed", _B,
       impact="A DLL export forwarder was repointed to a different target (DLL!Symbol). The "
              "effective implementation behind the exported name changed; dependent binaries get "
              "different — and possibly missing — behaviour at load time.",
       description_template="export '{name}' forwarder changed: {old} → {new}"),
    _E("pe_hardening_improved", _C,
       impact="The DLL gained exploit-mitigation bits in DllCharacteristics. "
              "A hardening improvement; existing consumers are unaffected.",
       description_template="PE hardening improved: gained {detail}"),
    _E("pe_hardening_weakened", _R,
       impact="The DLL lost exploit-mitigation bits in "
              "OPTIONAL_HEADER.DllCharacteristics (NX_COMPAT/DEP, "
              "DYNAMIC_BASE/ASLR, HIGH_ENTROPY_VA, GUARD_CF). Loading this "
              "DLL weakens the mitigation posture of every process that maps "
              "it — the PE counterpart of the ELF RELRO/PIE/canary "
              "regressions.",
       description_template="PE hardening weakened: lost {detail}"),
    _E("pe_import_load_mode_changed", _R,
       impact="An imported DLL function moved between the eager import table "
              "(IMAGE_DIRECTORY_ENTRY_IMPORT, resolved at process load) and the "
              "delay-load table (IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT, resolved "
              "on first call). The two have different failure-timing "
              "contracts: an eager import that fails aborts the process at "
              "load; a delay import that fails surfaces only when the "
              "consumer first calls it — a deployment/error-handling risk "
              "even though the DLL and symbol both still exist.",
       description_template="Import load mode changed for '{name}': {old} → {new}"),
    _E("pe_machine_changed", _B,
       impact="PE machine/architecture changed (e.g. AMD64 → ARM64); the DLL is a different "
              "architecture and cannot be loaded by existing clients.",
       description_template="PE machine/architecture changed: {old} → {new}"),
    _E("pe_ordinal_retargeted", _B,
       impact="A consumer imports this DLL function purely by ordinal number "
              "(no name in its import table). The DLL still exports that "
              "ordinal, but it now names a *different* function than it did in "
              "the old library — PE ordinals are commonly auto-assigned and "
              "reused when the export table shifts, so an ordinal-only "
              "consumer silently calls the wrong function with no link or load "
              "error.",
       description_template="PE export ordinal retargeted: {name} named '{old}' in the old library, now names '{new}' — a consumer that imports by ordinal silently calls a different function"),
    _E("pie_disabled", _R,
       impact="Position-independent executable disabled; the image loads at a fixed address, defeating ASLR.",
       description_template="PIE disabled: executable is no longer position-independent (ASLR defeated)"),
    _E("platform_baseline_floor_raised", _R,
       impact="The binary's own maximum required symbol-version tag exceeds a "
              "declared platform-baseline promise (e.g. a manylinux wheel tag "
              "such as `manylinux_2_27`, or an explicit `--env-matrix` "
              "`runtime_floors` declaration). Unlike a runtime-floor *raise* "
              "between releases, this fires on a single artifact's own "
              "requirement — the classic 'works on my box, `GLIBC_2.x not "
              "found` on the user's older system' failure a manylinux tag "
              "exists to prevent. Rebuild against the older sysroot/glibc the "
              "tag promises, or lower the declared floor if the promise "
              "itself changed.",
       description_template="Platform-baseline floor exceeded for {detail}: binary requires {new}, declared baseline promises at most {old} (required by: {name})"),
    _E("protected_visibility_changed", _R,
       impact="ELF symbol visibility changed between DEFAULT and PROTECTED. For data "
              "symbols this can break copy relocations; for functions it changes "
              "interposition semantics. The symbol remains exported.",
       description_template="Data symbol visibility changed: {name} ({old} → {new}); may break copy relocations"),
    _E("relro_weakened", _R,
       impact="RELRO protection weakened (e.g. full→partial); the GOT is no longer fully read-only, widening the GOT-overwrite attack surface.",
       description_template="RELRO weakened: {old} → {new}"),
    _E("rpath_changed", _C,
       impact="The binary's RPATH (its own runtime library search path) "
              "changed; this can change which copy of a dependency gets "
              "loaded at runtime, but doesn't affect the library's own "
              "exported ABI.",
       description_template="RPATH changed: {old} → {new}"),
    _E("rpath_type_changed", _R,
       impact="The library-search tag type flipped between DT_RPATH and "
              "DT_RUNPATH (ld --enable-new-dtags default drift). The two have "
              "different lookup semantics: DT_RPATH applies to the whole "
              "dependency subtree and takes precedence over LD_LIBRARY_PATH, "
              "while DT_RUNPATH applies only to the object's direct dependencies "
              "and is overridden by LD_LIBRARY_PATH. Transitive dependencies or "
              "environment overrides that resolved before may now resolve "
              "differently (or not at all).",
       description_template="Library search tag type changed: {old} → {new} (lookup semantics differ)"),
    _E("rtti_mode_changed", _R,
       impact="C++ RTTI support was toggled between builds (-frtti ↔ -fno-rtti). "
              "-fno-rtti omits typeinfo for polymorphic types, so dynamic_cast / "
              "typeid against those types, and cross-DSO exception matching that "
              "relies on RTTI identity, can fail to link or silently misbehave "
              "when one side was built with RTTI and the other without. If the "
              "public API exposes polymorphic types or dynamic_cast/typeid in "
              "inlines, rebuild consumers in the matching mode."),
    _E("runpath_changed", _C,
       impact="The binary's RUNPATH (a lower-priority runtime library search "
              "path, consulted after LD_LIBRARY_PATH) changed; this can "
              "change which copy of a dependency gets loaded at runtime, but "
              "doesn't affect the library's own exported ABI.",
       description_template="RUNPATH changed: {old} → {new}"),
    _E("sanitizer_mode_changed", _R,
       impact="The sanitizer set changed between builds (-fsanitize=). Sanitizers "
              "instrument code and change object layout — AddressSanitizer adds "
              "redzones around globals and stack objects and swaps in an "
              "interceptor allocator, and the runtime must match — so a library "
              "and a consumer built with different -fsanitize= settings are not "
              "compatible. Ship sanitized builds only for testing, and match the "
              "sanitizer set across the library and its consumers."),
    _E("soname_bump_recommended", _C,
       impact="Binary-incompatible changes detected but SONAME was not bumped. "
              "Consumers linked against the current SONAME will encounter runtime "
              "failures. Recommended: bump the SONAME to signal the ABI break.",
       description_template="{name} binary-incompatible change(s) detected but {detail}. Consumers linked against {old} will encounter runtime failures. Recommended: bump SONAME to signal the ABI break."),
    _E("soname_bump_unnecessary", _C,
       impact="SONAME was bumped but no binary-incompatible changes were detected. "
              "This forces all consumers to relink unnecessarily. Consider whether "
              "the bump was intentional.",
       description_template="SONAME changed from {old} to {new} but no binary-incompatible changes were detected. This forces all consumers to relink unnecessarily. Consider whether the bump was intentional."),
    _E("soname_changed", _R,
       impact="SONAME changed. Already-compiled consumers record the old SONAME "
              "in DT_NEEDED and can fail to load unless the old SONAME remains "
              "available. The exported ABI surface may still be compatible, but "
              "deployment action is required."),
    _E("soname_missing", _C,
       impact="Library has no SONAME; package managers and ldconfig cannot track versions.",
       description_template="Old library has no SONAME (bad practice — packaging/ldconfig will fail); new library correctly defines SONAME {new}"),
    _E("stack_canary_removed", _R,
       impact="Stack-smashing protector (-fstack-protector) no longer referenced; stack-buffer overflows are no longer detected at runtime.",
       description_template="Stack canary removed: -fstack-protector no longer referenced"),
    _E("static_tls_introduced", _R,
       impact="The library set DF_STATIC_TLS: it now uses the static "
              "(initial-exec / local-exec) TLS model. Such a library can no "
              "longer be reliably dlopen()ed — the dynamic loader may fail with "
              "'cannot allocate memory in static TLS block' when the process's "
              "static TLS surplus is exhausted. Link-time consumers are "
              "unaffected, so this defaults to RISK; gate it to break via the "
              "plugin/security policy if the library is meant to be dlopen-loadable. "
              "The flag-level TLS_MODEL_CHANGED (L3) explains which build flag "
              "caused it; this kind proves the artifact effect.",
       description_template="Static-TLS model introduced (DF_STATIC_TLS set): the library may no longer be reliably dlopen()ed"),
    _E("static_tls_removed", _C,
       impact="DF_STATIC_TLS was cleared: the library returned to the dynamic "
              "TLS model and is dlopen-friendly again. Informational improvement.",
       description_template="Static-TLS model removed (DF_STATIC_TLS cleared) — dlopen-friendly again"),
    _E("struct_packing_mode_changed", _R,
       impact="The default struct-packing/alignment policy changed between builds "
              "(-fpack-struct / MSVC /Zp, or a differing pack width). Reducing the "
              "packing alignment removes padding, so every member offset and the "
              "type's size can change without any source or symbol change. Consumers "
              "compiled against the old packing read fields at stale offsets. The "
              "artifact/type diff proves the concrete offset break; this localizes "
              "the flag that caused it. Build consumers with the matching packing."),
    _E("struct_return_convention_changed", _B,
       impact="The aggregate (struct/class/union) return convention changed for a "
              "public function — e.g. a small struct that was returned in registers "
              "is now returned via a hidden caller-provided pointer (sret), or vice "
              "versa (-freg-struct-return ↔ -fpcc-struct-return, or a "
              "triviality/size change that crosses the register-return threshold). "
              "Callers and callee disagree on where the result lives, so the return "
              "value is read from the wrong location — silent corruption or a crash. "
              "Proven from DWARF/ABI facts, so BREAKING; the flag-only signal stays "
              "as the generic abi_relevant_build_flag_changed (RISK).",
       policy_overrides={"plugin_abi": _C}),
    _E("sycl_backend_driver_req_changed", _R,
       impact="Minimum backend driver version requirement increased; may fail on systems with "
              "older drivers (e.g., Level Zero, OpenCL ICD).",
       description_template="Minimum driver requirement for {name} backend changed from {old} to {new}."),
    _E("sycl_implementation_changed", _B,
       impact="SYCL implementation changed (e.g., DPC++ to AdaptiveCpp); "
              "entirely different runtime ABI, plugin interface, and binary layout. "
              "All SYCL consumers must be rebuilt.",
       description_template="SYCL implementation changed from {old} to {new}; entirely different runtime ABI."),
    _E("sycl_pi_entrypoint_added", _C, is_addition=True,
       impact="New PI entry point added to dispatch table; existing plugins are unaffected.",
       description_template="{detail} entry point '{name}' added to plugin '{new}'."),
    _E("sycl_pi_entrypoint_removed", _B,
       impact="Required PI entry point removed from plugin dispatch table; runtime calls to "
              "this function will crash or return PI_ERROR_UNKNOWN.",
       description_template="{detail} entry point '{name}' removed from plugin '{old}'; runtime calls to this function will fail."),
    _E("sycl_pi_version_changed", _B,
       impact="PI interface version changed; runtime rejects plugins compiled against the old "
              "PI version. All backend plugins must be rebuilt or upgraded.",
       description_template="PI interface version changed from {old} to {new}; backend plugins compiled against the old version may be rejected at runtime."),
    _E("sycl_plugin_added", _C, is_addition=True,
       impact="New backend plugin available; broadens hardware support.",
       description_template="Backend plugin '{name}' ({detail}) added; new {new} backend support available."),
    _E("sycl_plugin_removed", _B,
       impact="Backend plugin removed from distribution; applications targeting this backend "
              "will fail at runtime with PI_ERROR_DEVICE_NOT_FOUND.",
       description_template="Backend plugin '{name}' ({detail}) removed; applications targeting the {old} backend will fail at runtime."),
    _E("sycl_plugin_search_path_changed", _R,
       impact="Plugin discovery path changed; plugins may not be found at runtime unless "
              "deployment configuration is updated.",
       description_template="SYCL plugin search paths changed; plugins may not be found at runtime without deployment configuration update."),
    _E("sycl_runtime_version_changed", _C,
       impact="SYCL runtime version changed; informational. Actual binary breaks are detected "
              "by symbol/type diff of the runtime library.",
       description_template="SYCL runtime version changed from {old} to {new}."),
    _E("symbol_binding_became_unique", _R,
       impact="An exported symbol's binding became STB_GNU_UNIQUE. GNU-unique "
              "symbols are enforced as process-wide unique by the dynamic loader, "
              "and a library that defines one becomes non-unloadable — dlclose() "
              "is inhibited for it. Changes loader semantics for consumers that "
              "rely on unloading. RISK.",
       description_template="Symbol binding became GNU_UNIQUE: {name} — inhibits dlclose() on this library"),
    _E("symbol_binding_changed", _C,
       impact="GLOBAL→WEAK binding lets interposers override unexpectedly; old code may get wrong implementation.",
       description_template="Symbol binding changed: {name} ({old} → {new})"),
    _E("symbol_binding_lost_unique", _R,
       impact="An exported symbol's binding was STB_GNU_UNIQUE and is no longer. "
              "The process-wide ODR-uniqueness guarantee that consumers may have "
              "relied on (a single shared instance of an inline/template static "
              "across all DSOs) is gone; duplicate per-DSO instances may reappear. "
              "RISK.",
       description_template="Symbol binding lost GNU_UNIQUE: {name} — process-wide uniqueness guarantee removed"),
    _E("symbol_binding_strengthened", _C,
       impact="WEAK→GLOBAL binding; safe upgrade, interposition still possible via LD_PRELOAD.",
       description_template="Symbol binding changed: {name} ({old} → {new})"),
    _E("symbol_elf_visibility_changed", _C,
       impact="ELF symbol visibility (st_other) changed (e.g. DEFAULT→PROTECTED). "
              "Symbol is still exported but interposition via LD_PRELOAD may stop working.",
       description_template="ELF visibility changed: {name} ({old} → {new})"),
    _E("symbol_leaked_from_dependency_changed", _R,
       impact="Symbol originates from a dependency library (e.g. libstdc++, libgcc) that leaked "
              "into this library's public ABI surface. The symbol changed between versions — "
              "existing consumers are unlikely to be affected directly, but the leak itself is a "
              "library quality issue. Apply -fvisibility=hidden to prevent accidental ABI surface "
              "enlargement from dependencies."),
    _E("symbol_moved_version_node", _R,
       impact="Symbol moved from one version node to another (e.g. LIBFOO_1.0 → "
              "LIBFOO_2.0). Applications linked against the old version node will "
              "not find this symbol at the expected version. This is typically "
              "intentional during a major release.",
       description_template="Symbol {name} moved from version node {old} to {new}. Applications linked against {old} will not find this symbol at the expected version. This is typically intentional during a major release."),
    _E("symbol_type_changed", _B,
       impact="Symbol type changed (e.g. FUNC→OBJECT); callers using wrong calling convention.",
       description_template="Symbol type changed: {name} ({old} → {new})"),
    _E("symbol_version_alias_changed", _R,
       impact="Default symbol version alias changed (e.g. foo@@VER_1.0 → foo@@VER_2.0). "
              "Old binaries requesting the previous default version may get a link or "
              "load error if the old version alias is not retained."),
    _E("symbol_version_defined_added", _C,
       impact="New symbol version defined; transparent to existing consumers.",
       description_template="Symbol version definition added: {new}"),
    _E("symbol_version_defined_removed", _B,
       impact="Defined symbol version removed; old binaries requesting that version get link error.",
       description_template="Symbol version removed: {old}"),
    _E("symbol_version_node_removed", _B,
       impact="A version node (e.g. LIBFOO_1.0) was entirely removed from the "
              "version script. Applications linked against symbols under that "
              "version node will get unresolved symbol errors at load time.",
       description_template="Version node {name} was entirely removed from the version script. Symbols previously under this node: {detail}. Applications linked against {name} will get unresolved symbol errors."),
    _E("symbol_version_required_added", _R,
       impact="Requires a newer symbol version than old system provides; may fail to load on older systems.",
       description_template="New symbol version requirement: {name} (from {detail})"),
    _E("symbol_version_required_added_compat", _C,
       impact="New version requirement added but older than existing max; safe on current systems.",
       description_template="New symbol version requirement: {name} (from {detail}) — not newer than previous max, backward-compatible"),
    _E("symbol_version_required_removed", _C,
       impact="Version requirement dropped; broadens compatibility.",
       description_template="Symbol version requirement removed: {name} (from {detail})"),
    _E("symbolic_binding_mode_changed", _R,
       impact="DT_SYMBOLIC/DF_SYMBOLIC was toggled. When set, the object "
              "resolves its own references against its own definitions first, "
              "before the global symbol scope — a lookup-precedence change "
              "that can silently stop honoring an LD_PRELOAD or another "
              "library's intended interposition of a symbol this object also "
              "defines.",
       description_template="Symbolic binding mode changed: {old} → {new}"),
    _E("text_relocation_introduced", _R,
       impact="DF_TEXTREL/DT_TEXTREL was gained: the dynamic loader must write "
              "into the (nominally read-only, shared) text segment to apply "
              "relocations. This defeats W^X and page-level text-segment "
              "sharing across processes, and on hardened systems the loader "
              "may refuse to load the object at all.",
       description_template="Text relocations introduced (DF_TEXTREL/DT_TEXTREL set): the loader must write into the text segment, defeating W^X and text-segment sharing"),
    _E("text_relocation_removed", _C,
       impact="DF_TEXTREL/DT_TEXTREL was dropped; the text segment stays "
              "read-only and shared again. A hardening improvement.",
       description_template="Text relocations removed (DF_TEXTREL/DT_TEXTREL cleared): text segment is read-only/shared again"),
    _E("threadsafe_statics_mode_changed", _R,
       impact="Thread-safe initialization of function-local statics was toggled "
              "(-fno-threadsafe-statics ↔ default). With -fno-threadsafe-statics "
              "the compiler omits the __cxa_guard acquire/release calls around a "
              "local static's first-use initialization, so a public inline holding "
              "a function-local static, compiled in different modes across TUs, has "
              "mismatched guard expectations — a data race or double-init on "
              "concurrent first use."),
    _E("time64_abi_changed", _B,
       impact="The time64/large-file ABI flipped: time_t/off_t-family public "
              "typedefs changed width together (glibc `_TIME_BITS=64` / "
              "`_FILE_OFFSET_BITS=64`, available since glibc 2.34, sometimes "
              "flipped by a toolchain or distro default on 32-bit targets). "
              "Every public function or struct carrying one of these typedefs "
              "changed layout — old binaries pass 32-bit values where the new "
              "library reads 64-bit ones (or vice versa). The per-symbol breaking "
              "findings share this single root cause; align _TIME_BITS/"
              "_FILE_OFFSET_BITS across the library and its consumers.",
       description_template="time64/LFS ABI flip detected: {detail}"),
    _E("tls_model_changed", _R,
       impact="The thread-local storage model changed between builds "
              "(-ftls-model=, or -fextern-tls-init ↔ -fno-extern-tls-init). The "
              "TLS access sequence (and, with -fextern-tls-init, whether a wrapper "
              "function mediates access to a dynamically-initialized thread_local "
              "from another TU) differs, so consumers built against the old model "
              "can use the wrong access pattern for an exported thread_local."),
    _E("tls_var_size_changed", _B,
       impact="Exported thread-local (TLS) variable size changed; consumers using copy "
              "relocations or direct TLS access will read/write out of bounds.",
       description_template="TLS variable size changed: {name} ({old} → {new} bytes)"),
    _E("toolchain_flag_drift", _C,
       impact="Compiler flags differ between versions; may cause subtle ABI mismatches."),
    _E("value_abi_trait_changed", _B,
       impact="A type's calling-convention-relevant triviality/copy-"
              "semantics trait changed (the DWARF-derived heuristic for "
              "whether a value type is 'trivial enough' to pass in "
              "registers per the platform ABI). On SysV AMD64 this kind "
              "means the register-vs-hidden-pointer return mechanism did "
              "NOT flip (see struct_return_convention_changed for that "
              "case). On any other target (AArch64, i386, mixed-arch, "
              "...) this detector's model is SysV-AMD64-only, so this "
              "kind covers every trait change there — including one that "
              "did flip the actual return mechanism; it's simply unknown "
              "there, not ruled out. A caller compiled against the old "
              "trait should be treated as at risk.",
       policy_overrides={"plugin_abi": _C}),
    _E("vector_abi_changed", _B,
       impact="Vector-function (SIMD clone) ABI selection changed (-mveclibabi/-fveclib/-vecabi); vectorized call variants resolve to a different ABI, so callers of the vector entry points pass/return data in the wrong registers.",
       policy_overrides={"plugin_abi": _C}),
    _E("version_script_missing", _C,
       impact="Library exports symbols without a version script. This is a common "
              "oversight that prevents fine-grained symbol versioning and makes "
              "future ABI evolution harder to manage.",
       description_template="Library exports {detail} symbol(s) without a version script. This is a common oversight that prevents fine-grained symbol versioning and makes future ABI evolution harder to manage. Consider adding a version script (--version-script=libfoo.map)."),
    _E("versioned_symbol_scheme_detected", _R,
       impact="Most removed symbols reappear as added symbols differing only by a "
              "version token in the name (e.g. ICU 'u_strlen_75' -> 'u_strlen_78', "
              "or a GNU symbol-version node bump). The large removed/added churn is "
              "likely a library-wide versioned-symbol scheme, not independent API "
              "removals — review against the library's versioning convention; a "
              "suppression preset can scope these renames to compatible."),
    _E("visibility_leak", _C,
       impact="Internal symbols exported without -fvisibility=hidden; namespace pollution risk.",
       description_template="Old library exports {detail} internal-looking symbol(s) without -fvisibility=hidden (bad practice — accidental ABI surface enlargement): {name}"),
    _E("vtable_symbol_identity_changed", _R,
       impact="Vtable or typeinfo symbol identity changed (e.g. via visibility or "
              "version-script changes) while class layout is stable. Cross-DSO RTTI "
              "comparison and exception handling may silently fail."),
    _E("wchar_model_changed", _R,
       impact="The -fshort-wchar compiler flag drifted between builds. GCC and "
              "Clang document that objects built with and without "
              "-fshort-wchar are not binary compatible: the flag switches "
              "wchar_t between the platform default (commonly 4-byte signed on "
              "Linux/macOS) and a 2-byte unsigned type. Any public function "
              "parameter, return value, or struct field carrying wchar_t "
              "changes size and signedness with no symbol-level signal, so a "
              "symbol-only check is blind to it.",
       description_template="wchar_t model changed: {old} → {new}. Objects built with and without -fshort-wchar are not binary compatible for any public wchar_t parameter, field, or return value."),
    _E("whole_program_vtables_mode_changed", _R,
       impact="Whole-program vtable optimization was toggled between builds "
              "(-fwhole-program-vtables, typically with LTO). It lets the linker "
              "devirtualize calls and elide or rewrite vtable/typeinfo emission "
              "across translation units under a closed-world assumption, so mixing "
              "a build that assumed whole-program visibility with a consumer that "
              "extends a class or overrides a virtual can dispatch to the wrong "
              "slot. If the public API exposes polymorphic types, build the library "
              "and its consumers with the matching setting."),
    _E("writable_executable_segment", _R,
       impact="A loadable segment is now both writable and executable (W^X violation); injected code in that page becomes executable.",
       description_template="Writable + executable segment introduced (W^X violation)"),
    _E("x86_isa_baseline_raised", _R,
       impact="GNU_PROPERTY_X86_ISA_1_NEEDED gained a micro-architecture "
              "level (e.g. x86-64-v2 → x86-64-v3): the library now requires "
              "newer CPU instructions unconditionally. Consumers on older CPUs "
              "that could run the previous build get SIGILL or a loader "
              "rejection.",
       description_template="x86-64 ISA baseline raised: {old} → {new}"),
]
