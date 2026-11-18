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


"""Ordinal part 1 of ``types.py``'s entry list -- not a separate owner.

``types.py`` remains the taxonomy and the single public name
(``TYPES_ENTRIES``); see its docstring for this taxonomy's scope, its
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

from .observability import observable
from .registry import (
    ChangeEntity,
    ChangeKindMeta,
    ChangeOperation,
    ContractSubject,
    Verdict,
)

_OBS_B = observable(("B",))
_OBS_DH = observable(
    ("B", "D"),
    ("B", "H"),
    rationale="layout or type fact: DWARF and the public header both carry it",
)
_OBS_H_HEADER = observable(
    ("B", "H"),
    rationale="a header-level fact: the public header declares the opaque type or typedef; neither the export table nor DWARF carries it",
)

_OBS_H_PURE = observable(
    ("B", "H"),
    rationale="a pure virtual has no body and no symbol: only the public header declares it, so a DWARF-only surface never holds it",
)
_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta
_ENT = ChangeEntity
_OP = ChangeOperation
_CS = ContractSubject

TYPES_ENTRIES_1: list[ChangeKindMeta] = [
    _E(
        "abi_equivalent_type_changed",
        _R,
        observable_at=(_OBS_DH,),
        impact="A parameter, return, field or variable was retyped between two "
        "types the compiler passes, stores and returns identically (same size, "
        "alignment and register class: long -> long long on LP64, char -> signed "
        "char, an enum -> its unsigned underlying integer, __int128 -> "
        "unsigned __int128, a float vector -> a double of the same size). The "
        "compiled ABI is unchanged, so an existing binary keeps working. The "
        "declared type is different, though: a source that overloads on it, a "
        "template instantiated over it, or a function pointer of the old type "
        "can see the difference, so the change is recorded as a source-level risk.",
        description_template="Type spelling changed between ABI-identical types: {name} {old} -> {new}",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "bundle_soname_skew",
        _B,
        impact="A co-versioned bundle of shared libraries (e.g. libfoo_core, "
        "libfoo_thread, libfoo_dpc) did not move SONAME in lockstep. "
        "Some siblings bumped the major SONAME, others did not. Distro "
        "packages built on this bundle have inconsistent dependency "
        "metadata; binaries dynamically loading the mixed cohort can fetch "
        "incompatible internal contracts and corrupt at the first cross-"
        "library call.",
        entity=_ENT.BUILD,
        contract_subject=_CS.BUILD_FACT,
        operation=_OP.MODIFIED,
    ),
    _E(
        "cpo_kind_changed",
        _B,
        observable_at=(_OBS_B,),
        impact="A public customization point object (CPO) changed kind: "
        "what used to be a free function is now a function-object "
        "(variable of an unspecified class type), or vice versa. "
        "Call syntax (`lib::sort(args...)`) keeps working but "
        "`decltype(lib::sort)` is now a different type, breaking "
        "extern templates, trait specializations, and any code that "
        "took the CPO's address.",
        description_template="Public name '{name}' was a {old} in old and is a {new} in new. Call syntax preserved; decltype, extern templates, and trait specializations break.",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        entity_from_field="entity_discriminator",
        operation=_OP.MODIFIED,
    ),
    _E(
        "cpu_dispatch_isa_dropped",
        _R,
        impact="An entire CPU ISA tier (e.g. avx512) of dispatched specializations "
        "was removed. The runtime dispatcher continues to work for callers "
        "that did not pin a specific ISA, but consumers that linked directly "
        "against a now-removed ISA-specific symbol get unresolved symbols. "
        "Reported as one grouped finding listing the affected algorithm "
        "stems. This risk grade covers export-only members; when any removed "
        "member was declared in a public header the finding carries an "
        "effective BREAKING verdict, since a direct caller no longer loads.",
        description_template="CPU dispatch ISA '{name}' tier removed: {detail}. Runtime dispatcher continues to work; consumers that pinned directly to '{name}' symbols get unresolved references at load time.",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.REMOVED,
    ),
    _E(
        "func_virtual_became_pure",
        _B,
        observable_at=(_OBS_H_PURE,),
        impact="Concrete virtual became pure; old binaries calling it get unresolved dispatch.",
        description_template="Function became pure virtual: {name}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "handle_type_changed",
        _B,
        observable_at=(_OBS_H_HEADER,),
        impact="An opaque handle typedef (a `void*` token or a pointer to a "
        "forward-declared struct) changed its underlying token type in a way "
        "callers can observe. Code that stored or compared the old handle "
        "representation now operates on an incompatible token.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "instantiation_missing_from_binary",
        _B,
        observable_at=(_OBS_B,),
        impact="Header declares an explicit template instantiation that the shipped "
        "library no longer exports. Consumer source compiles cleanly but fails "
        "to link at load time with an undefined-symbol error. Common when a "
        "build trim drops a Float/Method/Task combination without updating "
        "the public header's `extern template` declarations.",
        description_template="Template instantiation '{name}' was exported by the old library but is missing from the new binary. Other instantiations of '{detail}' still exist, so the public header very likely still advertises this one. Consumers built against the old header link cleanly but fail at load time with an undefined-symbol error.",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.REMOVED,
    ),
    _E(
        "layout_unverifiable",
        _R,
        impact="A public type's layout could not be verified at the available evidence "
        "tier — its size/offsets are not present (e.g. a symbols-only or partial "
        "dump with no debug info), so a real layout change cannot be ruled out. "
        "Informational and non-escalating; rebuild with debug info (or supply "
        "headers) to confirm.",
        description_template="'{name}' layout could not be verified: one side carries a layout descriptor but the other has no layout evidence (no size/offsets). A real layout change cannot be ruled out — rebuild with debug info (or supply headers) to confirm. Informational and non-escalating.",
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
    ),
    _E(
        "libcpp_abi_version_changed",
        _R,
        impact="The libc++ ABI version changed (e.g. _LIBCPP_ABI_VERSION 1 → 2). "
        "libc++ selects incompatible internal layouts for std:: types via an "
        "inline namespace (std::__1 vs std::__2), so types embedding them by "
        "value are laid out differently. Rebuild consumers against the matching "
        "libc++ ABI version.",
        description_template="libc++ ABI version changed ({old} → {new}). libc++ selects incompatible internal layouts for std:: types via an inline namespace (std::__{old} vs std::__{new}); types embedding them by value are laid out differently. Rebuild consumers against the matching libc++ ABI version.",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "opaque_invariant_broken",
        _B,
        observable_at=(_OBS_H_HEADER,),
        impact="A type that was opaque (its definition hidden from callers, crossed "
        "only by pointer) or PIMPL now exposes its layout — its complete "
        "definition became visible in the public include closure, or a "
        "public function began passing it by value. Callers that relied on "
        "never seeing the layout can now `sizeof`/embed it, so the type's "
        "size and fields have joined the ABI and any later change to them is "
        "a hard break.",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "overload_added",
        _R,
        observable_at=(_OBS_B,),
        impact="A new overload was added under a public name that previously had "
        "exactly one declaration. Old binaries are unaffected (binary "
        "compatible), but the change is not source-compatible: taking the "
        "function's address (`&Foo::bar`) becomes ambiguous and fails to "
        "compile, and existing call sites that relied on an implicit "
        "conversion may now resolve to the new overload, silently changing "
        "which function runs. KDE's C++ binary-compatibility policy lists "
        "adding an overload to a non-overloaded function as a change to "
        "avoid. Verdict is policy-adjustable — raise to API_BREAK under a "
        "strict source-compatibility profile.",
        description_template="Overload added to previously non-overloaded function: {name} — `&{name}` becomes ambiguous and overload resolution may change",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.ADDED,
    ),
    _E(
        "overload_set_rerouted",
        _R,
        observable_at=(_OBS_B,),
        impact="The overload set under a public name changed in a way "
        "where some overloads were removed and others added. "
        "Existing call sites that previously resolved to a removed "
        "overload now resolve to a different overload (often via "
        "implicit conversion or a templated catch-all), silently "
        "changing the called function. Compiles, links, runs — but "
        "runs different code.",
        description_template="Overload set for '{name}' changed: {detail}. Call sites that previously resolved to a removed overload may silently re-route to a different overload.",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "secondary_vtable_group_changed",
        _B,
        observable_at=(_OBS_DH,),
        impact="A polymorphic class's set of *secondary* vtable groups changed even "
        "though its own base declaration list did not — a direct or virtual "
        "base gained or lost virtual functions, so it started or stopped "
        "owning a secondary vtable group in the derived class. In the Itanium "
        "C++ ABI each polymorphic non-primary base contributes its own vtable "
        "group with its own this-adjustment; adding, removing, or reordering "
        "a group shifts every following group and the this-offsets baked into "
        "already-compiled consumers, so virtual dispatch through the affected "
        "base lands on the wrong slot. Reconstructed from DWARF inheritance "
        "(L1), catching a cross-type effect the per-type base/field diff — "
        "which only sees the unchanged derived class — cannot.",
        description_template="Secondary vtable groups changed for '{name}': {old} → {new} — a base's polymorphism changed, restructuring the derived vtable",
        entity=_ENT.TYPE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
]
