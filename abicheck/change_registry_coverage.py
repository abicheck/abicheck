# Copyright 2026 Nikolay Petrov
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

"""Coverage-extension ChangeKind registry entries.

Split out of ``change_registry.py`` to keep that module under the
AI-readiness 2000-line hard cap. These entries are spliced into the single
``REGISTRY`` at import time — declaring a kind here is exactly equivalent to
declaring it in ``change_registry.py`` (same one-entry-per-kind rule; the
classification sets and impact/description lookups all derive from the merged
registry). Covers the dynamic-loader / PE / Mach-O / language-contract kinds
added by the platform-coverage extension.
"""
from __future__ import annotations

from .change_registry_types import ChangeKindMeta, Verdict

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta

COVERAGE_EXTENSION_ENTRIES: list[ChangeKindMeta] = [
    # ── Coverage extension: dynamic-loader / import-surface facts ───────────
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
    _E("interpreter_changed", _R,
       impact="The ELF program interpreter (PT_INTERP) path changed. For an "
              "executable this repoints which dynamic linker runs it; a wrong "
              "or missing path fails at exec time with a cryptic ENOENT.",
       description_template="ELF interpreter changed: {old} → {new}"),
    _E("bind_now_disabled", _R,
       impact="DT_BIND_NOW/DF_BIND_NOW/DF_1_NOW was dropped: symbol binding "
              "reverts from eager (all relocations resolved at load) to lazy. "
              "Unresolved symbols that used to fail fast at load time now "
              "crash at first call, and full RELRO's GOT protection no longer "
              "applies in practice.",
       description_template="Eager binding (BIND_NOW) disabled"),
    _E("elf_endianness_changed", _B,
       impact="The ELF data encoding (EI_DATA) flipped between little- and "
              "big-endian. The two binaries target different byte orders and "
              "cannot be loaded by the same consumers — every multi-byte value "
              "is reinterpreted.",
       description_template="ELF endianness changed: {old} → {new}"),
    _E("x86_isa_baseline_raised", _R,
       impact="GNU_PROPERTY_X86_ISA_1_NEEDED gained a micro-architecture "
              "level (e.g. x86-64-v2 → x86-64-v3): the library now requires "
              "newer CPU instructions unconditionally. Consumers on older CPUs "
              "that could run the previous build get SIGILL or a loader "
              "rejection.",
       description_template="x86-64 ISA baseline raised: {old} → {new}"),
    _E("os_deployment_floor_raised", _R,
       impact="The minimum OS/kernel version the binary declares was raised "
              "(Mach-O LC_BUILD_VERSION minos, PE MajorSubsystemVersion, or "
              "ELF NT_GNU_ABI_TAG kernel floor). Consumers on OS versions in "
              "the dropped range can no longer load or run the library even "
              "though its symbol surface is unchanged.",
       description_template="OS deployment floor raised: {old} → {new}"),
    _E("dynamic_loading_flags_changed", _R,
       impact="DF_1_NODELETE / DF_1_NOOPEN / DF_1_ORIGIN toggled in "
              "DT_FLAGS_1. These flags change the dlopen/dlclose contract: "
              "NODELETE pins the library in memory (dlclose becomes a no-op), "
              "NOOPEN forbids loading via dlopen entirely, ORIGIN changes "
              "$ORIGIN-relative path resolution. Plugin hosts and consumers "
              "relying on the previous behaviour break at runtime.",
       description_template="Dynamic loading flags changed: {detail}"),
    _E("exported_object_alignment_reduced", _R,
       impact="An exported data object's address alignment dropped. Consumers "
              "that copy-relocate the object (non-PIC executables) allocated "
              "space with the old alignment guarantee, and code compiled "
              "against the old headers may use aligned loads (SIMD) that now "
              "fault or fall back to slow paths.",
       description_template="Exported object alignment reduced: {name} ({old} → {new} bytes)"),
    _E("elf_init_fini_changed", _R,
       impact="The presence of load/unload-time code (DT_INIT/DT_FINI/"
              "DT_INIT_ARRAY/DT_FINI_ARRAY) changed. Gaining constructors "
              "means code now runs on dlopen before any API call — new "
              "failure modes and ordering constraints; losing destructors "
              "means cleanup consumers relied on no longer happens at "
              "dlclose/exit.",
       description_template="ELF init/fini sections changed: {detail}"),
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

    # ── Coverage extension: PE/COFF (Windows) ────────────────────────────────
    _E("pe_hardening_weakened", _R,
       impact="The DLL lost exploit-mitigation bits in "
              "OPTIONAL_HEADER.DllCharacteristics (NX_COMPAT/DEP, "
              "DYNAMIC_BASE/ASLR, HIGH_ENTROPY_VA, GUARD_CF). Loading this "
              "DLL weakens the mitigation posture of every process that maps "
              "it — the PE counterpart of the ELF RELRO/PIE/canary "
              "regressions.",
       description_template="PE hardening weakened: lost {detail}"),
    _E("pe_hardening_improved", _C,
       impact="The DLL gained exploit-mitigation bits in DllCharacteristics. "
              "A hardening improvement; existing consumers are unaffected.",
       description_template="PE hardening improved: gained {detail}"),
    _E("library_version_downgraded", _R,
       impact="The embedded library version regressed (PE VS_FIXEDFILEINFO "
              "FileVersion or Mach-O LC_ID_DYLIB current_version). Installers "
              "and side-by-side logic that compare file versions may refuse "
              "to replace the file or silently keep the older copy, and a "
              "downgrade usually signals a mispackaged artifact.",
       description_template="Library version downgraded: {old} → {new}"),

    # ── Coverage extension: Mach-O (macOS) ───────────────────────────────────
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

    # ── Coverage extension: language-level contracts ─────────────────────────
    _E("func_variadic_added", _B,
       impact="The function gained a trailing C ellipsis (...). Variadic and "
              "non-variadic calls use different conventions on common ABIs "
              "(SysV x86-64 callers must set %al to the vector-register "
              "count; Apple AArch64 passes variadic args on the stack), so "
              "old callers invoke it with the wrong convention.",
       description_template="Function became variadic: {name}"),
    _E("func_variadic_removed", _B,
       impact="The function lost its trailing C ellipsis (...). Callers that "
              "passed extra arguments now invoke a mismatched signature, and "
              "on ABIs with distinct variadic conventions the call sequence "
              "itself differs.",
       description_template="Function no longer variadic: {name}"),
    _E("func_contract_attribute_added", _R,
       impact="The function gained a semantic contract attribute (nonnull, "
              "noreturn, format, alloc_size, malloc, returns_nonnull, "
              "warn_unused_result, sentinel, ...). The compiler now optimizes "
              "callers and the callee under the new contract — e.g. a NULL "
              "argument that used to be handled becomes undefined behaviour, "
              "or code after a call is deleted as unreachable.",
       description_template="Contract attribute added to {name}: {detail}"),
    _E("func_contract_attribute_removed", _R,
       impact="The function lost a semantic contract attribute callers may "
              "rely on (e.g. returns_nonnull dropped means callers that "
              "skipped NULL checks are now wrong; noreturn dropped means the "
              "function can return into code compiled as unreachable).",
       description_template="Contract attribute removed from {name}: {detail}"),
    _E("var_alignment_changed", _B,
       impact="An exported variable's declared alignment changed. Consumers "
              "compiled against the old alignment use matching aligned "
              "load/store instructions and copy-relocation slot sizes; a "
              "reduced alignment faults strict-alignment/SIMD access, and any "
              "change breaks layout assumptions baked into old binaries.",
       description_template="Variable alignment changed: {name} ({old} → {new} bits)"),
    _E("func_exception_spec_changed", _R,
       impact="The function's dynamic exception specification (throw(...)) "
              "changed in a way the noexcept kinds do not cover. Old callers "
              "compiled against the previous specification may have exception "
              "tables and unwind assumptions that no longer match; a "
              "violated specification calls std::unexpected/std::terminate.",
       description_template="Exception specification changed: {name} ({old} → {new})"),
    # ── Toolchain / runtime environment drift (G10, manylinux glibc floor) ──
    _E("platform_baseline_floor_raised", _R,
       impact="The binary's own maximum required symbol-version tag exceeds a "
              "declared platform-baseline promise (e.g. a manylinux wheel tag "
              "such as `manylinux_2_27`, or an explicit `--glibc-floor`/"
              "`runtime_floors` declaration). Unlike a runtime-floor *raise* "
              "between releases, this fires on a single artifact's own "
              "requirement — the classic 'works on my box, `GLIBC_2.x not "
              "found` on the user's older system' failure a manylinux tag "
              "exists to prevent. Rebuild against the older sysroot/glibc the "
              "tag promises, or lower the declared floor if the promise "
              "itself changed.",
       description_template="Platform-baseline floor exceeded for {detail}: binary requires {new}, declared baseline promises at most {old} (required by: {name})"),
]
