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

from .registry import ChangeEntity, ChangeKindMeta, ChangeOperation, Verdict

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta
_ENT = ChangeEntity
_OP = ChangeOperation

PLATFORM_ENTRIES_2: list[ChangeKindMeta] = [
    _E(
        "pe_ordinal_retargeted",
        _B,
        impact="A consumer imports this DLL function purely by ordinal number "
        "(no name in its import table). The DLL still exports that "
        "ordinal, but it now names a *different* function than it did in "
        "the old library — PE ordinals are commonly auto-assigned and "
        "reused when the export table shifts, so an ordinal-only "
        "consumer silently calls the wrong function with no link or load "
        "error.",
        description_template="PE export ordinal retargeted: {name} named '{old}' in the old library, now names '{new}' — a consumer that imports by ordinal silently calls a different function",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "pie_disabled",
        _R,
        impact="Position-independent executable disabled; the image loads at a fixed address, defeating ASLR.",
        description_template="PIE disabled: executable is no longer position-independent (ASLR defeated)",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "platform_baseline_floor_raised",
        _R,
        impact="The binary's own maximum required symbol-version tag exceeds a "
        "declared platform-baseline promise (e.g. a manylinux wheel tag "
        "such as `manylinux_2_27`, or an explicit `.abicheck.yml` "
        "`deployment.runtime_floors` declaration). Unlike a runtime-floor *raise* "
        "between releases, this fires on a single artifact's own "
        "requirement — the classic 'works on my box, `GLIBC_2.x not "
        "found` on the user's older system' failure a manylinux tag "
        "exists to prevent. Rebuild against the older sysroot/glibc the "
        "tag promises, or lower the declared floor if the promise "
        "itself changed.",
        description_template="Platform-baseline floor exceeded for {detail}: binary requires {new}, declared baseline promises at most {old} (required by: {name})",
        entity=_ENT.BUILD,
        operation=_OP.MODIFIED,
    ),
    _E(
        "protected_visibility_changed",
        _R,
        impact="ELF symbol visibility changed between DEFAULT and PROTECTED. For data "
        "symbols this can break copy relocations; for functions it changes "
        "interposition semantics. The symbol remains exported.",
        description_template="Data symbol visibility changed: {name} ({old} → {new}); may break copy relocations",
        entity=_ENT.VARIABLE,  # functions use func_visibility_protected_changed
        operation=_OP.MODIFIED,
    ),
    _E(
        "relro_weakened",
        _R,
        impact="RELRO protection weakened (e.g. full→partial); the GOT is no longer fully read-only, widening the GOT-overwrite attack surface.",
        description_template="RELRO weakened: {old} → {new}",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "rpath_changed",
        _C,
        impact="The binary's RPATH (its own runtime library search path) "
        "changed; this can change which copy of a dependency gets "
        "loaded at runtime, but doesn't affect the library's own "
        "exported ABI.",
        description_template="RPATH changed: {old} → {new}",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "rpath_type_changed",
        _R,
        impact="The library-search tag type flipped between DT_RPATH and "
        "DT_RUNPATH (ld --enable-new-dtags default drift). The two have "
        "different lookup semantics: DT_RPATH applies to the whole "
        "dependency subtree and takes precedence over LD_LIBRARY_PATH, "
        "while DT_RUNPATH applies only to the object's direct dependencies "
        "and is overridden by LD_LIBRARY_PATH. Transitive dependencies or "
        "environment overrides that resolved before may now resolve "
        "differently (or not at all).",
        description_template="Library search tag type changed: {old} → {new} (lookup semantics differ)",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "rtti_mode_changed",
        _R,
        impact="C++ RTTI support was toggled between builds (-frtti ↔ -fno-rtti). "
        "-fno-rtti omits typeinfo for polymorphic types, so dynamic_cast / "
        "typeid against those types, and cross-DSO exception matching that "
        "relies on RTTI identity, can fail to link or silently misbehave "
        "when one side was built with RTTI and the other without. If the "
        "public API exposes polymorphic types or dynamic_cast/typeid in "
        "inlines, rebuild consumers in the matching mode.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "runpath_changed",
        _C,
        impact="The binary's RUNPATH (a lower-priority runtime library search "
        "path, consulted after LD_LIBRARY_PATH) changed; this can "
        "change which copy of a dependency gets loaded at runtime, but "
        "doesn't affect the library's own exported ABI.",
        description_template="RUNPATH changed: {old} → {new}",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "sanitizer_mode_changed",
        _R,
        impact="The sanitizer set changed between builds (-fsanitize=). Sanitizers "
        "instrument code and change object layout — AddressSanitizer adds "
        "redzones around globals and stack objects and swaps in an "
        "interceptor allocator, and the runtime must match — so a library "
        "and a consumer built with different -fsanitize= settings are not "
        "compatible. Ship sanitized builds only for testing, and match the "
        "sanitizer set across the library and its consumers.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "soname_bump_recommended",
        _C,
        impact="Binary-incompatible changes detected but SONAME was not bumped. "
        "Consumers linked against the current SONAME will encounter runtime "
        "failures. Recommended: bump the SONAME to signal the ABI break.",
        description_template="{name} binary-incompatible change(s) detected but {detail}. Consumers linked against {old} will encounter runtime failures. Recommended: bump SONAME to signal the ABI break.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "soname_bump_unnecessary",
        _C,
        impact="SONAME was bumped but no binary-incompatible changes were detected. "
        "This forces all consumers to relink unnecessarily. Consider whether "
        "the bump was intentional.",
        description_template="SONAME changed from {old} to {new} but no binary-incompatible changes were detected. This forces all consumers to relink unnecessarily. Consider whether the bump was intentional.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "soname_changed",
        _R,
        impact="SONAME changed. Already-compiled consumers record the old SONAME "
        "in DT_NEEDED and can fail to load unless the old SONAME remains "
        "available. The exported ABI surface may still be compatible, but "
        "deployment action is required.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "soname_missing",
        _C,
        impact="Library has no SONAME; package managers and ldconfig cannot track versions.",
        description_template="Old library has no SONAME (bad practice — packaging/ldconfig will fail); new library correctly defines SONAME {new}",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "stack_canary_removed",
        _R,
        impact="Stack-smashing protector (-fstack-protector) no longer referenced; stack-buffer overflows are no longer detected at runtime.",
        description_template="Stack canary removed: -fstack-protector no longer referenced",
        entity=_ENT.BINARY,
        operation=_OP.REMOVED,
    ),
    _E(
        "static_tls_introduced",
        _R,
        impact="The library set DF_STATIC_TLS: it now uses the static "
        "(initial-exec / local-exec) TLS model. Such a library can no "
        "longer be reliably dlopen()ed — the dynamic loader may fail with "
        "'cannot allocate memory in static TLS block' when the process's "
        "static TLS surplus is exhausted. Link-time consumers are "
        "unaffected, so this defaults to RISK; gate it to break via the "
        "plugin/security policy if the library is meant to be dlopen-loadable. "
        "The flag-level TLS_MODEL_CHANGED (L3) explains which build flag "
        "caused it; this kind proves the artifact effect.",
        description_template="Static-TLS model introduced (DF_STATIC_TLS set): the library may no longer be reliably dlopen()ed",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "static_tls_removed",
        _C,
        impact="DF_STATIC_TLS was cleared: the library returned to the dynamic "
        "TLS model and is dlopen-friendly again. Informational improvement.",
        description_template="Static-TLS model removed (DF_STATIC_TLS cleared) — dlopen-friendly again",
        entity=_ENT.BINARY,
        operation=_OP.REMOVED,
    ),
    _E(
        "struct_packing_mode_changed",
        _R,
        impact="The default struct-packing/alignment policy changed between builds "
        "(-fpack-struct / MSVC /Zp, or a differing pack width). Reducing the "
        "packing alignment removes padding, so every member offset and the "
        "type's size can change without any source or symbol change. Consumers "
        "compiled against the old packing read fields at stale offsets. The "
        "artifact/type diff proves the concrete offset break; this localizes "
        "the flag that caused it. Build consumers with the matching packing.",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "struct_return_convention_changed",
        _B,
        impact="The aggregate (struct/class/union) return convention changed for a "
        "public function — e.g. a small struct that was returned in registers "
        "is now returned via a hidden caller-provided pointer (sret), or vice "
        "versa (-freg-struct-return ↔ -fpcc-struct-return, or a "
        "triviality/size change that crosses the register-return threshold). "
        "Callers and callee disagree on where the result lives, so the return "
        "value is read from the wrong location — silent corruption or a crash. "
        "Proven from DWARF/ABI facts, so BREAKING; the flag-only signal stays "
        "as the generic abi_relevant_build_flag_changed (RISK).",
        policy_overrides={"plugin_abi": _C},
        entity=_ENT.FUNCTION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "sycl_backend_driver_req_changed",
        _R,
        impact="Minimum backend driver version requirement increased; may fail on systems with "
        "older drivers (e.g., Level Zero, OpenCL ICD).",
        description_template="Minimum driver requirement for {name} backend changed from {old} to {new}.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "sycl_implementation_changed",
        _B,
        impact="SYCL implementation changed (e.g., DPC++ to AdaptiveCpp); "
        "entirely different runtime ABI, plugin interface, and binary layout. "
        "All SYCL consumers must be rebuilt.",
        description_template="SYCL implementation changed from {old} to {new}; entirely different runtime ABI.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "sycl_pi_entrypoint_added",
        _C,
        is_addition=True,
        impact="New PI entry point added to dispatch table; existing plugins are unaffected.",
        description_template="{detail} entry point '{name}' added to plugin '{new}'.",
        entity=_ENT.BINARY,
        operation=_OP.ADDED,
    ),
    _E(
        "sycl_pi_entrypoint_removed",
        _B,
        impact="Required PI entry point removed from plugin dispatch table; runtime calls to "
        "this function will crash or return PI_ERROR_UNKNOWN.",
        description_template="{detail} entry point '{name}' removed from plugin '{old}'; runtime calls to this function will fail.",
        entity=_ENT.BINARY,
        operation=_OP.REMOVED,
    ),
    _E(
        "sycl_pi_version_changed",
        _B,
        impact="PI interface version changed; runtime rejects plugins compiled against the old "
        "PI version. All backend plugins must be rebuilt or upgraded.",
        description_template="PI interface version changed from {old} to {new}; backend plugins compiled against the old version may be rejected at runtime.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "sycl_plugin_added",
        _C,
        is_addition=True,
        impact="New backend plugin available; broadens hardware support.",
        description_template="Backend plugin '{name}' ({detail}) added; new {new} backend support available.",
        entity=_ENT.BINARY,
        operation=_OP.ADDED,
    ),
    _E(
        "sycl_plugin_removed",
        _B,
        impact="Backend plugin removed from distribution; applications targeting this backend "
        "will fail at runtime with PI_ERROR_DEVICE_NOT_FOUND.",
        description_template="Backend plugin '{name}' ({detail}) removed; applications targeting the {old} backend will fail at runtime.",
        entity=_ENT.BINARY,
        operation=_OP.REMOVED,
    ),
    _E(
        "sycl_plugin_search_path_changed",
        _R,
        impact="Plugin discovery path changed; plugins may not be found at runtime unless "
        "deployment configuration is updated.",
        description_template="SYCL plugin search paths changed; plugins may not be found at runtime without deployment configuration update.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "sycl_runtime_version_changed",
        _C,
        impact="SYCL runtime version changed; informational. Actual binary breaks are detected "
        "by symbol/type diff of the runtime library.",
        description_template="SYCL runtime version changed from {old} to {new}.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "symbol_binding_became_unique",
        _R,
        impact="An exported symbol's binding became STB_GNU_UNIQUE. GNU-unique "
        "symbols are enforced as process-wide unique by the dynamic loader, "
        "and a library that defines one becomes non-unloadable — dlclose() "
        "is inhibited for it. Changes loader semantics for consumers that "
        "rely on unloading. RISK.",
        description_template="Symbol binding became GNU_UNIQUE: {name} — inhibits dlclose() on this library",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "symbol_binding_changed",
        _C,
        impact="GLOBAL→WEAK binding lets interposers override unexpectedly; old code may get wrong implementation.",
        description_template="Symbol binding changed: {name} ({old} → {new})",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "symbol_binding_lost_unique",
        _R,
        impact="An exported symbol's binding was STB_GNU_UNIQUE and is no longer. "
        "The process-wide ODR-uniqueness guarantee that consumers may have "
        "relied on (a single shared instance of an inline/template static "
        "across all DSOs) is gone; duplicate per-DSO instances may reappear. "
        "RISK.",
        description_template="Symbol binding lost GNU_UNIQUE: {name} — process-wide uniqueness guarantee removed",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "symbol_binding_strengthened",
        _C,
        impact="WEAK→GLOBAL binding; safe upgrade, interposition still possible via LD_PRELOAD.",
        description_template="Symbol binding changed: {name} ({old} → {new})",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "symbol_elf_visibility_changed",
        _C,
        impact="ELF symbol visibility (st_other) changed (e.g. DEFAULT→PROTECTED). "
        "Symbol is still exported but interposition via LD_PRELOAD may stop working.",
        description_template="ELF visibility changed: {name} ({old} → {new})",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "symbol_leaked_from_dependency_changed",
        _R,
        impact="Symbol originates from a dependency library (e.g. libstdc++, libgcc) that leaked "
        "into this library's public ABI surface. The symbol changed between versions — "
        "existing consumers are unlikely to be affected directly, but the leak itself is a "
        "library quality issue. Apply -fvisibility=hidden to prevent accidental ABI surface "
        "enlargement from dependencies.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "symbol_moved_version_node",
        _R,
        impact="Symbol moved from one version node to another (e.g. LIBFOO_1.0 → "
        "LIBFOO_2.0). Applications linked against the old version node will "
        "not find this symbol at the expected version. This is typically "
        "intentional during a major release.",
        description_template="Symbol {name} moved from version node {old} to {new}. Applications linked against {old} will not find this symbol at the expected version. This is typically intentional during a major release.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "symbol_type_changed",
        _B,
        impact="Symbol type changed (e.g. FUNC→OBJECT); callers using wrong calling convention.",
        description_template="Symbol type changed: {name} ({old} → {new})",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "symbol_version_alias_changed",
        _R,
        impact="Default symbol version alias changed (e.g. foo@@VER_1.0 → foo@@VER_2.0). "
        "Old binaries requesting the previous default version may get a link or "
        "load error if the old version alias is not retained.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "symbol_version_defined_added",
        _C,
        impact="New symbol version defined; transparent to existing consumers.",
        description_template="Symbol version definition added: {new}",
        entity=_ENT.BINARY,
        operation=_OP.ADDED,
    ),
    _E(
        "symbol_version_defined_removed",
        _B,
        impact="Defined symbol version removed; old binaries requesting that version get link error.",
        description_template="Symbol version removed: {old}",
        entity=_ENT.BINARY,
        operation=_OP.REMOVED,
    ),
    _E(
        "symbol_version_node_removed",
        _B,
        impact="A version node (e.g. LIBFOO_1.0) was entirely removed from the "
        "version script. Applications linked against symbols under that "
        "version node will get unresolved symbol errors at load time.",
        description_template="Version node {name} was entirely removed from the version script. Symbols previously under this node: {detail}. Applications linked against {name} will get unresolved symbol errors.",
        entity=_ENT.BINARY,
        operation=_OP.REMOVED,
    ),
    _E(
        "symbol_version_required_added",
        _R,
        impact="Requires a newer symbol version than old system provides; may fail to load on older systems.",
        description_template="New symbol version requirement: {name} (from {detail})",
        entity=_ENT.BINARY,
        operation=_OP.ADDED,
    ),
    _E(
        "symbol_version_required_added_compat",
        _C,
        impact="New version requirement added but older than existing max; safe on current systems.",
        description_template="New symbol version requirement: {name} (from {detail}) — not newer than previous max, backward-compatible",
        entity=_ENT.BINARY,
        operation=_OP.ADDED,
    ),
    _E(
        "symbol_version_required_removed",
        _C,
        impact="Version requirement dropped; broadens compatibility.",
        description_template="Symbol version requirement removed: {name} (from {detail})",
        entity=_ENT.BINARY,
        operation=_OP.REMOVED,
    ),
    _E(
        "symbolic_binding_mode_changed",
        _R,
        impact="DT_SYMBOLIC/DF_SYMBOLIC was toggled. When set, the object "
        "resolves its own references against its own definitions first, "
        "before the global symbol scope — a lookup-precedence change "
        "that can silently stop honoring an LD_PRELOAD or another "
        "library's intended interposition of a symbol this object also "
        "defines.",
        description_template="Symbolic binding mode changed: {old} → {new}",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "text_relocation_introduced",
        _R,
        impact="DF_TEXTREL/DT_TEXTREL was gained: the dynamic loader must write "
        "into the (nominally read-only, shared) text segment to apply "
        "relocations. This defeats W^X and page-level text-segment "
        "sharing across processes, and on hardened systems the loader "
        "may refuse to load the object at all.",
        description_template="Text relocations introduced (DF_TEXTREL/DT_TEXTREL set): the loader must write into the text segment, defeating W^X and text-segment sharing",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "text_relocation_removed",
        _C,
        impact="DF_TEXTREL/DT_TEXTREL was dropped; the text segment stays "
        "read-only and shared again. A hardening improvement.",
        description_template="Text relocations removed (DF_TEXTREL/DT_TEXTREL cleared): text segment is read-only/shared again",
        entity=_ENT.BINARY,
        operation=_OP.REMOVED,
    ),
    _E(
        "threadsafe_statics_mode_changed",
        _R,
        impact="Thread-safe initialization of function-local statics was toggled "
        "(-fno-threadsafe-statics ↔ default). With -fno-threadsafe-statics "
        "the compiler omits the __cxa_guard acquire/release calls around a "
        "local static's first-use initialization, so a public inline holding "
        "a function-local static, compiled in different modes across TUs, has "
        "mismatched guard expectations — a data race or double-init on "
        "concurrent first use.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "time64_abi_changed",
        _B,
        impact="The time64/large-file ABI flipped: time_t/off_t-family public "
        "typedefs changed width together (glibc `_TIME_BITS=64` / "
        "`_FILE_OFFSET_BITS=64`, available since glibc 2.34, sometimes "
        "flipped by a toolchain or distro default on 32-bit targets). "
        "Every public function or struct carrying one of these typedefs "
        "changed layout — old binaries pass 32-bit values where the new "
        "library reads 64-bit ones (or vice versa). The per-symbol breaking "
        "findings share this single root cause; align _TIME_BITS/"
        "_FILE_OFFSET_BITS across the library and its consumers.",
        description_template="time64/LFS ABI flip detected: {detail}",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "tls_model_changed",
        _R,
        impact="The thread-local storage model changed between builds "
        "(-ftls-model=, or -fextern-tls-init ↔ -fno-extern-tls-init). The "
        "TLS access sequence (and, with -fextern-tls-init, whether a wrapper "
        "function mediates access to a dynamically-initialized thread_local "
        "from another TU) differs, so consumers built against the old model "
        "can use the wrong access pattern for an exported thread_local.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "tls_var_size_changed",
        _B,
        impact="Exported thread-local (TLS) variable size changed; consumers using copy "
        "relocations or direct TLS access will read/write out of bounds.",
        description_template="TLS variable size changed: {name} ({old} → {new} bytes)",
        entity=_ENT.VARIABLE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "toolchain_flag_drift",
        _C,
        impact="Compiler flags differ between versions; may cause subtle ABI mismatches.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "value_abi_trait_changed",
        _B,
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
        policy_overrides={"plugin_abi": _C},
        entity=_ENT.FUNCTION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "vector_abi_changed",
        _B,
        impact="Vector-function (SIMD clone) ABI selection changed (-mveclibabi/-fveclib/-vecabi); vectorized call variants resolve to a different ABI, so callers of the vector entry points pass/return data in the wrong registers.",
        policy_overrides={"plugin_abi": _C},
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "version_script_missing",
        _C,
        impact="Library exports symbols without a version script. This is a common "
        "oversight that prevents fine-grained symbol versioning and makes "
        "future ABI evolution harder to manage.",
        description_template="Library exports {detail} symbol(s) without a version script. This is a common oversight that prevents fine-grained symbol versioning and makes future ABI evolution harder to manage. Consider adding a version script (--version-script=libfoo.map).",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "versioned_symbol_scheme_detected",
        _R,
        impact="Most removed symbols reappear as added symbols differing only by a "
        "version token in the name (e.g. ICU 'u_strlen_75' -> 'u_strlen_78', "
        "or a GNU symbol-version node bump). The large removed/added churn is "
        "likely a library-wide versioned-symbol scheme, not independent API "
        "removals — review against the library's versioning convention; a "
        "suppression preset can scope these renames to compatible.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "visibility_leak",
        _C,
        impact="Internal symbols exported without -fvisibility=hidden; namespace pollution risk.",
        description_template="Old library exports {detail} internal-looking symbol(s) without -fvisibility=hidden (bad practice — accidental ABI surface enlargement): {name}",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "vtable_symbol_identity_changed",
        _R,
        impact="Vtable or typeinfo symbol identity changed (e.g. via visibility or "
        "version-script changes) while class layout is stable. Cross-DSO RTTI "
        "comparison and exception handling may silently fail.",
        entity=_ENT.TYPE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "wchar_model_changed",
        _R,
        impact="The -fshort-wchar compiler flag drifted between builds. GCC and "
        "Clang document that objects built with and without "
        "-fshort-wchar are not binary compatible: the flag switches "
        "wchar_t between the platform default (commonly 4-byte signed on "
        "Linux/macOS) and a 2-byte unsigned type. Any public function "
        "parameter, return value, or struct field carrying wchar_t "
        "changes size and signedness with no symbol-level signal, so a "
        "symbol-only check is blind to it.",
        description_template="wchar_t model changed: {old} → {new}. Objects built with and without -fshort-wchar are not binary compatible for any public wchar_t parameter, field, or return value.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "whole_program_vtables_mode_changed",
        _R,
        impact="Whole-program vtable optimization was toggled between builds "
        "(-fwhole-program-vtables, typically with LTO). It lets the linker "
        "devirtualize calls and elide or rewrite vtable/typeinfo emission "
        "across translation units under a closed-world assumption, so mixing "
        "a build that assumed whole-program visibility with a consumer that "
        "extends a class or overrides a virtual can dispatch to the wrong "
        "slot. If the public API exposes polymorphic types, build the library "
        "and its consumers with the matching setting.",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "writable_executable_segment",
        _R,
        impact="A loadable segment is now both writable and executable (W^X violation); injected code in that page becomes executable.",
        description_template="Writable + executable segment introduced (W^X violation)",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "x86_isa_baseline_raised",
        _R,
        impact="GNU_PROPERTY_X86_ISA_1_NEEDED gained a micro-architecture "
        "level (e.g. x86-64-v2 → x86-64-v3): the library now requires "
        "newer CPU instructions unconditionally. Consumers on older CPUs "
        "that could run the previous build get SIGILL or a loader "
        "rejection.",
        description_template="x86-64 ISA baseline raised: {old} → {new}",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
]
