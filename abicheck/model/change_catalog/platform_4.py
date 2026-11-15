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
"""ADR-093 WS-1: entries of ``platform_1.py`` split out at the 800-line cap.

Whole entries moved unchanged from ``platform_1.py`` (each gains only its
``observable_at``). Registered in ``platform.py``.
"""

from __future__ import annotations

from .observability import ObservabilityCondition, Platform, observable
from .registry import (
    ChangeEntity,
    ChangeKindMeta,
    ChangeOperation,
    ContractSubject,
    Verdict,
)

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta
_ENT = ChangeEntity
_OP = ChangeOperation
_CS = ContractSubject

_OBS_B = observable(("B",))
_OBS_B_ELF = observable(
    ("B",), condition=ObservabilityCondition(platform=frozenset({Platform.ELF}))
)
_OBS_B_MACHO = observable(
    ("B",), condition=ObservabilityCondition(platform=frozenset({Platform.MACHO}))
)
_OBS_DH = observable(
    ("B", "D"),
    ("B", "H"),
    rationale="layout or type fact: DWARF and the public header both carry it",
)
_OBS_D = observable(
    ("B", "D"), rationale="underlying size set by the compiler: only DWARF records it"
)
_OBS_DBD = observable(
    ("B", "D"),
    ("B", "BD"),
    rationale="compiler-flag fact: the header never carries it; DWARF and build data do",
)

PLATFORM_ENTRIES_4: list[ChangeKindMeta] = [
    _E(
        "abi_tag_changed",
        _B,
        impact="The Itanium ABI-tag set on a symbol changed (e.g. it gained or "
        "lost `[abi:cxx11]` / a `[[gnu::abi_tag]]`). The mangled name "
        "encodes the tag, so old binaries reference a symbol that no "
        "longer exists under that name. Distinct from a mass dual-ABI "
        "flip: this is a per-symbol tag change.",
        description_template="ABI-tag set changed for '{name}': {detail}. The mangled name encodes the tag, so the old symbol ({old}) no longer exists under that name ({new}).",
        entity=_ENT.BINARY,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "allocator_replacement_added",
        _R,
        impact="The library newly exports a global operator new/delete "
        "replacement. Once loaded, it interposes allocation for the "
        "whole process: objects allocated before load (or by other "
        "DSOs' inlined allocators) can be freed by the replacement — a "
        "mismatched-allocator heap corruption hazard.",
        description_template="Global allocator replacement introduced: {detail}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.ADDED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "allocator_replacement_removed",
        _R,
        impact="The library stopped exporting its global operator new/delete "
        "replacement. Consumers whose allocations previously routed "
        "through the replacement now silently get the default "
        "allocator; memory pools, tracking, or alignment guarantees the "
        "replacement provided disappear.",
        description_template="Global allocator replacement removed: {detail}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.REMOVED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "bind_now_disabled",
        _R,
        impact="DT_BIND_NOW/DF_BIND_NOW/DF_1_NOW was dropped: symbol binding "
        "reverts from eager (all relocations resolved at load) to lazy. "
        "Unresolved symbols that used to fail fast at load time now "
        "crash at first call, and full RELRO's GOT protection no longer "
        "applies in practice.",
        description_template="Eager binding (BIND_NOW) disabled",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "bit_int_width_changed",
        _B,
        impact="A public use of C23 `_BitInt(N)` changed its width N between "
        "versions, or a field/param type changed to/from `_BitInt(N)`. "
        "The bit width determines the storage size and calling-convention "
        "treatment, so old code reads/writes the value with the wrong "
        "width.",
        description_template="_BitInt change on {name}: {detail} ({old} → {new}). The bit width determines storage size and ABI treatment.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        entity_from_field="entity_discriminator",
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "branch_protection_improved",
        _C,
        impact="An AArch64 branch-protection feature (BTI/PAC) was added to "
        ".note.gnu.property — a hardening improvement. Informational.",
        description_template="Branch protection improved: {old} → {new}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "branch_protection_weakened",
        _R,
        impact="An AArch64 branch-protection feature (BTI and/or PAC) was dropped "
        "from .note.gnu.property. Like CET, BTI enforcement is process-wide, "
        "so a single non-BTI DSO weakens the guarantee for the whole link "
        "map. RISK by default; gated to break by the security policy.",
        description_template="Branch protection weakened: {old} → {new}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "cet_protection_improved",
        _C,
        impact="An x86 CET feature (IBT/SHSTK) was added to .note.gnu.property — "
        "a hardening improvement. Informational.",
        description_template="CET protection improved: {old} → {new}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "cet_protection_weakened",
        _R,
        impact="An x86 CET control-flow-protection feature (IBT and/or SHSTK) was "
        "dropped from .note.gnu.property. CET is enforced per link map: a "
        "single non-IBT DSO disables indirect-branch tracking for the whole "
        "process, so weakening it silently lowers the runtime hardening of "
        "every consumer. RISK by default; the shipped security policy gates "
        "it to break.",
        description_template="CET protection weakened: {old} → {new}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "char8t_migration",
        _B,
        impact="A public parameter, return, or field type changed between a "
        "char-family spelling (char / unsigned char) and C++20 `char8_t`. "
        "`char8_t` is a distinct type that participates in overload "
        "resolution and name mangling, so the mangled symbol changes and "
        "old binaries fail to resolve it.",
        description_template="char8_t migration ({detail}) on {name}: {old} → {new}. char8_t is a distinct C++20 type that changes overload identity and name mangling.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        entity_from_field="entity_discriminator",
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "char_signedness_changed",
        _R,
        impact="The signedness of a plain `char` changed between builds "
        "(-fsigned-char ↔ -funsigned-char; the default is target-dependent). "
        "`char`, `signed char` and `unsigned char` are three distinct types, "
        "so a plain-`char` parameter or member reinterprets the same bytes "
        "with the opposite sign, silently changing comparisons and value "
        "range in consumer code recompiled against the other setting. Symbol "
        "names are unchanged, so only the captured build flag exposes it. "
        "Build consumers with the matching char signedness.",
        entity=_ENT.BINARY,
        contract_subject=_CS.BUILD_FACT,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DBD,),
    ),
    _E(
        "dt_relr_introduced",
        _R,
        impact="The linker enabled packed relative relocations (DT_RELR, "
        "`-z pack-relative-relocs`; default on some distros since "
        "binutils 2.38). A DT_RELR binary requires glibc ≥ 2.36 (or an "
        "equivalent loader) — older dynamic loaders refuse to load it. "
        "glibc marks this with a synthetic GLIBC_ABI_DT_RELR version "
        "requirement. Rebuild with `-z nopack-relative-relocs` to keep "
        "supporting older runtimes.",
        description_template="Packed relative relocations introduced (DT_RELR): requires glibc >= 2.36 or equivalent loader",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "dt_relr_removed",
        _C,
        impact="Packed relative relocations (DT_RELR) were dropped; the binary "
        "loads on older dynamic loaders again. Slightly larger relocation "
        "tables, no compatibility cost.",
        description_template="Packed relative relocations removed (DT_RELR): loader floor lowered",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.REMOVED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "dynamic_loading_flags_changed",
        _R,
        impact="DF_1_NODELETE / DF_1_NOOPEN / DF_1_ORIGIN toggled in "
        "DT_FLAGS_1. These flags change the dlopen/dlclose contract: "
        "NODELETE pins the library in memory (dlclose becomes a no-op), "
        "NOOPEN forbids loading via dlopen entirely, ORIGIN changes "
        "$ORIGIN-relative path resolution. Plugin hosts and consumers "
        "relying on the previous behaviour break at runtime.",
        description_template="Dynamic loading flags changed: {detail}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "elf_abi_flags_changed",
        _B,
        impact="The ELF e_flags ABI-selecting bits changed — the float ABI "
        "(hard/soft-float), EABI version, or base ISA differs between "
        "versions. Object code compiled against the old convention passes "
        "floating-point arguments in the wrong registers/stack slots, "
        "silently corrupting calls. Artifact-proven from e_flags; the "
        "flag-level FLOAT_ABI_CHANGED (L3) stays the explanatory signal.",
        description_template="ELF ABI flags changed: {old} → {new}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "elf_class_changed",
        _B,
        impact="The ELF class changed between 32-bit and 64-bit. Pointer width, "
        "type sizes, and the calling convention all differ; no consumer "
        "built against one class can use the other.",
        description_template="ELF class changed: {old}-bit → {new}-bit",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "elf_endianness_changed",
        _B,
        impact="The ELF data encoding (EI_DATA) flipped between little- and "
        "big-endian. The two binaries target different byte orders and "
        "cannot be loaded by the same consumers — every multi-byte value "
        "is reinterpreted.",
        description_template="ELF endianness changed: {old} → {new}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "elf_init_fini_changed",
        _R,
        impact="The presence of load/unload-time code (DT_INIT/DT_FINI/"
        "DT_INIT_ARRAY/DT_FINI_ARRAY) changed. Gaining constructors "
        "means code now runs on dlopen before any API call — new "
        "failure modes and ordering constraints; losing destructors "
        "means cleanup consumers relied on no longer happens at "
        "dlclose/exit.",
        description_template="ELF init/fini sections changed: {detail}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "elf_machine_changed",
        _B,
        impact="The ELF e_machine (target architecture) changed. The two inputs "
        "are different-architecture binaries — nothing about their ABI is "
        "comparable, and a consumer built for one cannot load the other. "
        "The ELF-side analogue of PE_MACHINE_CHANGED / MACHO_CPU_TYPE_CHANGED.",
        description_template="ELF machine changed: {old} → {new} — different target architecture",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "elf_osabi_changed",
        _R,
        impact="The ELF EI_OSABI (target OS ABI) changed (e.g. SYSV ↔ GNU/Linux ↔ "
        "FreeBSD). This can alter the meaning of OS-specific symbol types "
        "and relocations; consumers may resolve or load differently. RISK.",
        description_template="ELF OS ABI changed: {old} → {new}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "enum_size_flag_changed",
        _R,
        impact="The enum storage-size policy was toggled between builds "
        "(-fshort-enums ↔ default). With -fshort-enums the compiler picks the "
        "smallest integer type that holds an enum's range instead of a full "
        "int, so an enum member of a public struct, an enum-typed parameter, "
        "or an enum return value changes size and (as a struct member) shifts "
        "every field after it. Symbol names are unchanged, so a symbol-only "
        "check is blind; the artifact/type diff confirms any concrete layout "
        "break. Build all consumers with the matching -fshort-enums setting.",
        entity=_ENT.ENUM,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DBD,),
    ),
    _E(
        "enum_underlying_size_changed",
        _B,
        impact="Enum underlying type changed (e.g. int→long); affects ABI of functions passing enums by value.",
        description_template="Enum underlying type size changed: {name} ({old} → {new} bytes)",
        entity=_ENT.ENUM,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_D,),
    ),
    _E(
        "exceptions_mode_changed",
        _R,
        impact="C++ exception support was toggled between builds (-fexceptions ↔ "
        "-fno-exceptions). The two modes are not link-compatible: an "
        "exception thrown in -fexceptions code that unwinds through a frame "
        "compiled with -fno-exceptions is undefined behaviour (it calls "
        "std::terminate at best), and -fno-exceptions changes the codegen "
        "and emitted cleanup/EH tables of every public inline that uses "
        "throw/try/catch. If the public API exposes exception types or "
        "throwing inlines, rebuild all consumers in the matching mode.",
        entity=_ENT.BINARY,
        contract_subject=_CS.BUILD_FACT,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DBD,),
    ),
    _E(
        "executable_stack",
        _C,
        impact="Library has executable stack (PT_GNU_STACK RWE); NX protection disabled — security risk.",
        description_template="Executable stack detected: library linked with -Wl,-z,execstack — NX protection disabled (security risk)",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "executable_stack_removed",
        _C,
        impact="Executable stack removed (PT_GNU_STACK RWE→RW); NX protection restored — a hardening improvement, not a regression.",
        description_template="Executable stack removed: library now uses a non-executable stack — NX protection restored (good practice)",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.REMOVED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "float_abi_changed",
        _R,
        impact="The floating-point calling convention changed between builds "
        "(-mfloat-abi=soft/softfp/hard; the default is target-dependent). On "
        "ARM the float ABI decides whether floating-point arguments and "
        "returns travel in FP registers (hard) or core registers/memory "
        "(soft), so a function taking or returning a float/double is called "
        "with an incompatible convention across the boundary — a silent "
        "corruption or crash. Build the whole stack with one float ABI.",
        entity=_ENT.BINARY,
        contract_subject=_CS.BUILD_FACT,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DBD,),
    ),
    _E(
        "fortify_source_weakened",
        _R,
        impact="_FORTIFY_SOURCE fortified libc wrappers no longer referenced; compile-time/runtime buffer-overflow checks were dropped.",
        description_template="FORTIFY_SOURCE weakened: fortified libc wrappers (*_chk) no longer referenced",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "func_visibility_protected_changed",
        _R,
        impact="Symbol visibility changed to STV_PROTECTED. The symbol remains exported and "
        "calls through the normal dynamic-linking path keep working, but calls "
        "originating inside the library now bind to the library's own definition: "
        "an interposed replacement (LD_PRELOAD, a hooking or profiling shim, an "
        "allocator override) no longer intercepts them, so a consumer that relies on "
        "interposition silently gets different behavior. Compatible for consumers that "
        "never interpose; a break for those that do -- unknown from the binary alone.",
        description_template="ELF symbol visibility changed: {name} ({old} → {new}); symbol still exported, interposition semantics changed",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "glibcxx_dual_abi_flip_detected",
        _C,
        impact="Mass symbol churn detected that matches a libstdc++ dual ABI toggle "
        "(_GLIBCXX_USE_CXX11_ABI). Individual removed/added symbols are likely "
        "caused by this single root cause rather than intentional API changes.",
        description_template="libstdc++ dual ABI flip detected ({detail}): {name} churned symbols contain CXX11 ABI markers; likely caused by _GLIBCXX_USE_CXX11_ABI toggle",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B,),
    ),
    _E(
        "hash_style_removed",
        _R,
        impact="A symbol-hash table style present in the old binary was dropped "
        "(ld --hash-style default drift): SysV `.hash` and/or GNU "
        "`.gnu.hash`. Dynamic loaders and tools that only support the "
        "dropped style (very old glibc, some non-GNU loaders, MIPS "
        "toolchains for `.hash`) can no longer resolve symbols from this "
        "library.",
        description_template="Symbol hash table style removed: {old} → {new}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.REMOVED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "ifunc_introduced",
        _C,
        impact="IFUNC resolver indirection added; transparent to well-behaved callers.",
        description_template="Symbol became GNU_IFUNC: {name}",
        entity=_ENT.BINARY,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "ifunc_removed",
        _C,
        impact="IFUNC removed; transparent to callers.",
        description_template="Symbol no longer GNU_IFUNC: {name}",
        entity=_ENT.BINARY,
        contract_subject=_CS.DECLARATION,
        operation=_OP.REMOVED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "imported_symbol_added",
        _R,
        impact="The binary gained an undefined (imported) symbol — a new "
        "obligation the consumer's link environment must satisfy at load "
        "time. If none of the loaded dependencies provide it, the dynamic "
        "linker fails with an unresolved-symbol error. Weak imports are "
        "exempt (they resolve to null instead of failing).",
        description_template="New imported symbol: {name}{detail}",
        entity=_ENT.BINARY,
        contract_subject=_CS.DEPENDENCY_EDGE,
        operation=_OP.ADDED,
        observable_at=(_OBS_B,),
    ),
    _E(
        "imported_symbol_removed",
        _C,
        impact="The binary dropped an undefined (imported) symbol — one fewer "
        "external obligation. Existing consumers are unaffected.",
        description_template="Imported symbol no longer required: {name}{detail}",
        entity=_ENT.BINARY,
        contract_subject=_CS.DEPENDENCY_EDGE,
        operation=_OP.REMOVED,
        observable_at=(_OBS_B,),
    ),
    _E(
        "integer_model_changed",
        _B,
        impact="A large fraction of public integer parameters/returns flipped "
        "width together (e.g. int→long, int32_t→int64_t), or a public "
        "integer typedef changed its underlying size. This is the "
        "signature of an LP64↔ILP64 model switch (e.g. a BLAS-style "
        "`INT` typedef built for the 32-bit vs 64-bit integer interface). "
        "Every caller "
        "passes/reads integers with the wrong width; arguments and array "
        "indices are silently truncated or sign-extended.",
        description_template="Integer model changed ({new}): {detail}. This is the signature of an LP64↔ILP64 switch (e.g. oneMKL's 32-bit vs 64-bit MKL_INT interface); every caller passes/reads integers with the wrong width.",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DBD,),
    ),
    _E(
        "interpreter_changed",
        _R,
        impact="The ELF program interpreter (PT_INTERP) path changed. For an "
        "executable this repoints which dynamic linker runs it; a wrong "
        "or missing path fails at exec time with a cryptic ENOENT.",
        description_template="ELF interpreter changed: {old} → {new}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "long_double_abi_changed",
        _B,
        impact="A function's `long double` parameter or return representation "
        "changed — e.g. ppc64 migrating IBM double-double ↔ IEEE binary128, "
        "or `-mlong-double-64` shrinking 80-bit x87 to 64-bit. The source "
        "signature is unchanged, but the floating-point format differs, so "
        "old binaries pass/return the value in the wrong size and bit layout, "
        "silently corrupting it. Detected from the Itanium long-double "
        "mangling token (`e`/`g`/`u9__ieee128`) on a removed↔added pair, or "
        "from the `long double` DWARF byte size on a persisting symbol.",
        description_template="long double ABI changed: {detail} — floating-point representation differs (symbol {old} → {new})",
        entity=_ENT.BINARY,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DBD,),
    ),
    _E(
        "lto_mode_changed",
        _R,
        impact="Link-time optimization was toggled between builds (-flto ↔ no LTO, "
        "or with -fwhole-program-vtables). LTO changes cross-TU inlining and "
        "can devirtualize or drop vtable/typeinfo emission the linker would "
        "otherwise keep, so the emitted symbol set and inlined public-inline "
        "bodies can differ from a non-LTO build of the same source. A risk "
        "signal to review; the artifact diff proves any concrete symbol/layout "
        "break. Prefer a single LTO policy across the library and consumers.",
        entity=_ENT.BINARY,
        contract_subject=_CS.BUILD_FACT,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DBD,),
    ),
    _E(
        "macho_cpu_type_changed",
        _B,
        impact="A Mach-O architecture slice that used to ship is gone (e.g. a universal "
        "x86_64+arm64 dylib dropped its x86_64 slice, or x86_64 → arm64). Existing "
        "clients built for the removed architecture can no longer link against or load "
        "the dylib. Adding slices (single-arch → universal) is not flagged.",
        description_template="Mach-O architecture slice removed: {detail} no longer present ({old} → {new}); existing clients of the dropped arch can no longer load the dylib",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_MACHO,),
    ),
    _E(
        "macho_filetype_changed",
        _B,
        impact="The Mach-O filetype changed (e.g. MH_DYLIB → MH_BUNDLE). A "
        "dylib can be linked against at build time; a bundle can only "
        "be dlopen()ed. Consumers that link the old file kind cannot "
        "use the new one at all.",
        description_template="Mach-O filetype changed: {old} → {new}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_MACHO,),
    ),
    _E(
        "macho_linkage_flags_changed",
        _R,
        impact="Mach-O header linkage flags flipped (MH_TWOLEVEL two-level "
        "namespace, MH_WEAK_DEFINES, MH_BINDS_TO_WEAK, "
        "MH_NO_REEXPORTED_DYLIBS). Symbol resolution semantics change: "
        "flat vs two-level lookup can rebind symbols to different "
        "providers, and weak-definition coalescing behaviour differs.",
        description_template="Mach-O linkage flags changed: {detail}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_MACHO,),
    ),
    _E(
        "macho_reexport_changed",
        _R,
        impact="A re-exported dylib (LC_REEXPORT_DYLIB) was repointed to a "
        "different target. The umbrella's exported surface is now "
        "sourced from a different library — symbols may resolve to "
        "different implementations or disappear on systems where the "
        "new target differs.",
        description_template="Re-exported dylib repointed: {old} → {new}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_MACHO,),
    ),
    _E(
        "needed_added",
        _C,
        impact="New shared library dependency; may not be available on target systems.",
        entity=_ENT.BINARY,
        contract_subject=_CS.DEPENDENCY_EDGE,
        operation=_OP.ADDED,
        observable_at=(_OBS_B,),
    ),
    _E(
        "needed_order_changed",
        _R,
        impact="The DT_NEEDED dependency list was reordered while the set of "
        "dependencies stayed the same. The System V ABI's dynamic linker "
        "searches dependencies breadth-first in DT_NEEDED order, so a "
        "pure reorder can silently change which DSO wins the lookup for "
        "a non-versioned symbol defined in more than one dependency. Not "
        "proven breaking on its own — pair with a runtime binding check "
        "to confirm an actual provider changed.",
        description_template="DT_NEEDED order changed: {old} → {new}",
        entity=_ENT.BINARY,
        contract_subject=_CS.DEPENDENCY_EDGE,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B,),
    ),
    _E(
        "needed_removed",
        _C,
        impact="Dependency removed; should be transparent to consumers.",
        entity=_ENT.BINARY,
        contract_subject=_CS.DEPENDENCY_EDGE,
        operation=_OP.REMOVED,
        observable_at=(_OBS_B,),
    ),
    _E(
        "os_deployment_floor_raised",
        _R,
        impact="The minimum OS/kernel version the binary declares was raised "
        "(Mach-O LC_BUILD_VERSION minos, PE MajorSubsystemVersion, or "
        "ELF NT_GNU_ABI_TAG kernel floor). Consumers on OS versions in "
        "the dropped range can no longer load or run the library even "
        "though its symbol surface is unchanged.",
        description_template="OS deployment floor raised: {old} → {new}",
        entity=_ENT.BUILD,
        contract_subject=_CS.BUILD_FACT,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_MACHO,),
    ),
]
