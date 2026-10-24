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


"""Ordinal part 3 of ``platform.py``'s entry list -- not a separate owner.

``platform.py`` remains the taxonomy and the single public name
(``PLATFORM_ENTRIES``); see its docstring for this taxonomy's scope, its
boundary against the other four, and the methodology the entries were
categorized by. This file holds a contiguous slice of that one list and
claims no responsibility of its own, so nothing should import it directly.

The split is by declaration-order line position, not by concern, purely so
each file stays under ADR-061's 800-line ceiling -- the same reason and the
same shape as ``kind_names_{1,2,3}.py``, whose own docstring records that an
ordinal split is the right tool when the content is a data table rather than
behavior. Partitioning *this* list by a named sub-concern would be a
different change: it would move the D9 ownership boundary that
``symbols``/``types``/``platform``/``build``/``source`` already draws, and
the "Adding a new ChangeKind" procedure names those five modules by name.
"""

from __future__ import annotations

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

PLATFORM_ENTRIES_3: list[ChangeKindMeta] = [
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
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "vector_abi_changed",
        _B,
        impact="Vector-function (SIMD clone) ABI selection changed (-mveclibabi/-fveclib/-vecabi); vectorized call variants resolve to a different ABI, so callers of the vector entry points pass/return data in the wrong registers.",
        policy_overrides={"plugin_abi": _C},
        entity=_ENT.BINARY,
        contract_subject=_CS.BUILD_FACT,
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
        contract_subject=_CS.BINARY_WIDE,
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
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "visibility_leak",
        _C,
        impact="Internal symbols exported without -fvisibility=hidden; namespace pollution risk.",
        description_template="Old library exports {detail} internal-looking symbol(s) without -fvisibility=hidden (bad practice — accidental ABI surface enlargement): {name}",
        entity=_ENT.BINARY,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "vtable_symbol_identity_changed",
        _R,
        impact="Vtable or typeinfo symbol identity changed (e.g. via visibility or "
        "version-script changes) while class layout is stable. Cross-DSO RTTI "
        "comparison and exception handling may silently fail.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
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
        contract_subject=_CS.BUILD_FACT,
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
        contract_subject=_CS.BUILD_FACT,
        operation=_OP.MODIFIED,
    ),
    _E(
        "writable_executable_segment",
        _R,
        impact="A loadable segment is now both writable and executable (W^X violation); injected code in that page becomes executable.",
        description_template="Writable + executable segment introduced (W^X violation)",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
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
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
    ),
]
