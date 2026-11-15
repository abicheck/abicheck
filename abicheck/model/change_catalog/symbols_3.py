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
"""ADR-093 WS-1: entries of ``symbols_2.py`` split out at the 800-line cap.

Whole entries moved unchanged from ``symbols_2.py`` (each gains only its
``observable_at``). Registered in ``symbols.py``.
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
_OBS_H = observable(
    ("B", "H"), rationale="declaration-only fact: visible in the public header"
)
_OBS_DH = observable(
    ("B", "D"),
    ("B", "H"),
    rationale="layout or type fact: DWARF and the public header both carry it",
)

SYMBOLS_ENTRIES_3: list[ChangeKindMeta] = [
    _E(
        "param_lost_va_list",
        _C,
        impact="A parameter that was previously typed va_list now has a "
        "fixed type; source recompiled against the new header sees "
        "a stricter, non-variadic parameter type. This detector "
        "only checks the declared type flip, not whether the two "
        "representations agree: since va_list's own layout is "
        "platform-defined (an opaque struct/array on many ABIs, not "
        "a plain pointer), an already-linked caller still "
        "constructing and passing a va_list object has that value "
        "reinterpreted by the new callee as the fixed type — safe "
        "only if the caller already happened to pass a value "
        "compatible with the new fixed type.",
        description_template="Parameter was va_list, now fixed: {name} param {detail}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "param_pointer_level_changed",
        _B,
        impact="A parameter's pointer indirection depth changed (e.g. T* → "
        "T** or the reverse); the value passed for that argument is "
        "interpreted differently by callee and caller, so a caller "
        "compiled against the old signature passes the wrong kind "
        "of value — silent misinterpretation or a crash.",
        description_template="Parameter pointer level changed: {name} param {detail} (depth {old} → {new})",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "param_renamed",
        _R,
        impact="A parameter's name changed. Neither the compiled ABI "
        "(parameter names aren't part of the mangled signature or "
        "calling convention) nor C/C++ source compatibility is affected: "
        "neither language has named arguments. Generated bindings, "
        "documentation or IDE tooling keyed on the old name may be.",
        description_template="Parameter renamed: {name} param {detail}: {old} → {new}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "param_restrict_changed",
        _C,
        impact="A parameter's restrict qualifier was removed: the callee "
        "drops an optimization assumption and becomes more conservative, "
        "which is safe for every caller and leaves the calling convention "
        "untouched. (Adding restrict tightens the caller's obligation and "
        "is reported separately as param_restrict_added.)",
        description_template="Parameter restrict qualifier {detail}: {name} param {old}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "param_pointee_qualifier_added",
        _R,
        impact="The single pointee of a pointer/reference parameter gained "
        "const/volatile (`T *` -> `const T *`). The calling convention and "
        "every direct call are unaffected -- the callee now promises more. "
        "But the function's type changed: a consumer that stores it in a "
        "pointer of the old type (`void (*cb)(T *) = fn;`) no longer "
        "compiles (a C constraint violation, a C++ error), and a function "
        "table or callback registration built that way breaks on rebuild. "
        "Whether any consumer does that is not visible from the library.",
        description_template="Parameter pointee qualifier added: {name} param {detail}: {old} → {new}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "param_pointee_qualifier_changed",
        _A,
        impact="A const/volatile behind a pointer/reference parameter was "
        "removed, or added at a level an implicit conversion cannot reach "
        "(`char **` -> `const char **`). The calling convention is "
        "unchanged, so already-built binaries keep working, but direct "
        "callers no longer compile cleanly: passing a pointer to const "
        "data where the parameter no longer promises const is a C++ error "
        "(a constraint violation in C), and the callee may now modify "
        "data the caller assumed it would not.",
        description_template="Parameter pointee qualifier changed: {name} param {detail}: {old} → {new}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "func_return_pointee_qualifier_added",
        _A,
        impact="The pointee of a returned pointer/reference gained "
        "const/volatile (`char *f()` -> `const char *f()`). The return "
        "register and calling convention are unchanged, so built binaries "
        "keep working, but a caller that binds the result to a mutable "
        "pointer (`char *p = f();`) no longer compiles in C++ (a discarded-"
        "qualifier constraint violation in C), and so does a consumer that "
        "stores the function in a pointer of the old type.",
        description_template="Return pointee qualifier added: {name}: {old} → {new}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "func_return_pointee_qualifier_removed",
        _R,
        impact="The pointee of a returned pointer/reference lost "
        "const/volatile (`const char *f()` -> `char *f()`). Every direct "
        "call still compiles -- the result converts implicitly to the old "
        "type -- but the function's type changed: a consumer that stores it "
        "in a pointer of the old type (`const char *(*fp)(void) = f;`) no "
        "longer compiles. Whether any consumer does that is not visible "
        "from the library.",
        description_template="Return pointee qualifier removed: {name}: {old} → {new}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "func_return_pointee_qualifier_changed",
        _A,
        impact="The pointee of a returned multi-level pointer lost "
        "const/volatile at a level the old type did not protect with "
        "`const` at every level above it (`const char **f()` -> "
        "`char **f()`). The return register is unchanged, so built "
        "binaries keep working, but the new result no longer converts "
        "implicitly to the old type ([conv.qual]: `char **` is not a "
        "`const char **`), so a caller binding it to the old type "
        "(`const char **p = f();`) no longer compiles.",
        description_template="Return pointee qualifier changed: {name}: {old} → {new}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "overload_ambiguity_introduced",
        _R,
        impact="A new overload joined an existing one with the same arity, "
        "and at every position where their parameters differ both take a "
        "scalar (arithmetic, enum, pointer). Built binaries are unaffected, "
        "but a call that passes an argument with no type of its own there "
        "-- an empty braced list `{}` -- had one viable candidate and is now "
        "ambiguous, so that consumer no longer compiles. Constructors are "
        "included (`T x({});`). Whether any consumer writes such a call is "
        "not visible from the library.",
        description_template="Overload makes an existing call ambiguous: {detail} — was {old}, new {new}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.ADDED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "param_restrict_added",
        _R,
        impact="A pointer parameter gained `restrict`. The calling convention "
        "is untouched, but the caller's obligation is not: passing "
        "overlapping buffers, valid under the old declaration, is undefined "
        "behaviour under the new one, and the new library is compiled to "
        "exploit that promise (e.g. vectorised loops). An unchanged, "
        "already-built consumer that relies on overlap silently computes a "
        "different result -- only consumers that never alias the arguments "
        "are safe. Removing `restrict` is reported as param_restrict_changed.",
        description_template="Parameter restrict qualifier added: {name} param {old}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "public_api_exposes_stl_by_value",
        _R,
        impact="A public function takes or returns a `std::` type by value across "
        "the library boundary. Standard-library layouts (string, vector, "
        "etc.) differ across toolchains, standard-library versions, and "
        "the C++11 dual-ABI setting, so passing one by value at the ABI "
        "boundary is fragile: a consumer built with a different STL silently "
        "reads the wrong layout. Pass an opaque handle or a C-style view "
        "instead.",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "return_pointer_level_changed",
        _B,
        impact="A function's return type's pointer indirection depth "
        "changed; a caller compiled against the old signature reads "
        "the returned value as the wrong kind of pointer — silent "
        "misinterpretation or a crash.",
        description_template="Return pointer level changed: {name} (depth {old} → {new})",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "symbol_renamed_batch",
        _B,
        impact="Multiple symbols renamed (e.g. namespace prefix added/removed); "
        "old binaries reference the old names and will get undefined symbol errors at load time.",
        description_template="Batch symbol rename detected (namespace refactoring): prefix '{name}' added to {detail}",
        entity=_ENT.BINARY,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B,),
    ),
    _E(
        "symbol_size_changed",
        _B,
        impact="ELF symbol size changed; copy relocations or memcpy-based consumers get truncated/oversized data.",
        description_template="Symbol size changed: {name} ({old} → {new} bytes)",
        entity=_ENT.VARIABLE,  # OBJECT/COMMON/TLS only
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "symbol_size_changed_const_object",
        _B,
        impact="ELF size changed on a public const string-like object declared without a fixed bound in headers. "
        "Old non-PIE consumers may have copy relocations sized from the old DSO symbol, so a later DSO can "
        "truncate or otherwise mis-copy data at load time.",
        description_template="Symbol size changed: {name} ({old} → {new} bytes)",
        entity=_ENT.VARIABLE,  # OBJECT/COMMON/TLS only
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "symbol_size_changed_internal",
        _B,
        impact="ELF size changed on an internal-looking (reserved/underscore-prefixed) exported data symbol; "
        "exported data remains part of the dynamic ABI and size changes can break copy relocations "
        "or direct data consumers. Override severity via --policy only when the symbol is known private.",
        description_template="Symbol size changed: {name} ({old} → {new} bytes)",
        entity=_ENT.VARIABLE,  # OBJECT/COMMON/TLS only
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_B_ELF,),
    ),
    _E(
        "var_access_changed",
        _A,
        impact="A variable's access level narrowed (e.g. public→private); "
        "source code that previously accessed it directly no longer "
        "compiles. An already-compiled consumer accessing the "
        "exported symbol directly is unaffected, since binary "
        "access control isn't enforced at link/load time.",
        description_template="Variable access level narrowed: {name} ({old} → {new})",
        entity=_ENT.VARIABLE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "var_access_widened",
        _C,
        impact="A variable's access level widened (e.g. private→public); "
        "this only grants new source-level access and cannot break "
        "anything that already compiled successfully.",
        description_template="Variable access level widened: {name} ({old} → {new})",
        entity=_ENT.VARIABLE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "var_added",
        _C,
        is_addition=True,
        impact="New variable available; existing binaries are unaffected.",
        description_template="New public variable: {name}",
        entity=_ENT.VARIABLE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.ADDED,
        observable_at=(_OBS_B,),
    ),
    _E(
        "var_alignment_changed",
        _B,
        impact="An exported variable's declared alignment changed. Consumers "
        "compiled against the old alignment use matching aligned "
        "load/store instructions and copy-relocation slot sizes; a "
        "reduced alignment faults strict-alignment/SIMD access, and any "
        "change breaks layout assumptions baked into old binaries.",
        description_template="Variable alignment changed: {name} ({old} → {new} bits)",
        entity=_ENT.VARIABLE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "var_became_const",
        _B,
        impact="Variable moved to read-only section; old code writing to it gets SIGSEGV.",
        entity=_ENT.VARIABLE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "var_deprecated_added",
        _C,
        impact="Variable gained [[deprecated]]; consumers get a compiler "
        "warning when referencing it. This detector matches "
        "variables by mangled name and only checks the deprecated "
        "flag — it doesn't verify the type is otherwise unchanged "
        "(a variable's type isn't encoded in its mangled name the "
        "way a function's parameters are), so a companion finding "
        "for a type change is possible. A consumer building with "
        "warnings as errors (e.g. -Werror=deprecated-declarations) "
        "has this turn a previously clean build into a failing one.",
        description_template="Variable marked deprecated: {name} ({detail})",
        entity=_ENT.VARIABLE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "var_deprecated_removed",
        _C,
        impact="Variable's [[deprecated]] marker was removed; the compiler "
        "warning stops, with no effect on the variable's ABI.",
        description_template="Variable no longer marked deprecated: {name}",
        entity=_ENT.VARIABLE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "var_lost_const",
        _B,
        impact="Variable no longer const; ODR violations possible if old code inlined the value.",
        entity=_ENT.VARIABLE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "var_removed_elf_only",
        _B,
        impact="An exported data symbol (STT_OBJECT/STT_TLS/STT_COMMON) that "
        "the old binary carried is gone from the new binary's export table, "
        "and no public header declares it on either side. The removal "
        "counterpart of var_added_elf_only, and it exists for the same "
        "asymmetry that one does: a header-aware comparison builds its "
        "variable map from the header AST, so an undeclared export never "
        "entered that map and _diff_variables could not report VAR_REMOVED "
        "for it -- the loss disappeared entirely rather than being reported "
        "weakly. This is the shape a C++ ABI-support object takes: a class's "
        "vtable/typeinfo (`_ZTV`/`_ZTI`/`_ZTS`/`_ZTT`) is emitted by the "
        "compiler, declared by no header, and bound by old consumers that "
        "construct, destroy, dynamic_cast or throw the type -- so losing its "
        "export breaks them even though the class declaration and its whole "
        "method list survive unchanged. The evidence is the export table "
        "alone, so nothing is known about the object's type or size; public-"
        "surface scoping still decides relevance from the owning type, and an "
        "owner that cannot be resolved stays reported rather than silently "
        "clean.",
        description_template="Exported data symbol not declared in any public header removed: {name}",
        entity=_ENT.VARIABLE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.REMOVED,
        observable_at=(_OBS_B,),
    ),
    _E(
        "var_removed",
        _B,
        impact="Old binaries reference a global variable that no longer exists; link or load failure.",
        description_template="Public variable removed: {name}",
        entity=_ENT.VARIABLE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.REMOVED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "var_type_changed",
        _B,
        impact="Old binaries read/write the variable with wrong size or layout; data corruption or segfault.",
        description_template="Variable type changed: {name}",
        entity=_ENT.VARIABLE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "func_virtual_noexcept_added",
        _A,
        impact="`noexcept` was added to a virtual method. Its mangled name and "
        "vtable slot are unchanged, so built consumers keep working, but an "
        "overrider declared without `noexcept` in a consumer's derived class "
        "no longer compiles: an overrider may not have a weaker exception "
        "specification than the function it overrides ([except.spec]/5). "
        "Reported beside `func_noexcept_added`, which states the fact itself; "
        "a `final` method or class cannot be overridden, which a header "
        "comparison does not rule out here.",
        description_template="noexcept added to virtual method: {name}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "func_return_covariant_narrowed",
        _R,
        impact="A function returning a pointer or reference to a class now "
        "returns one to a class derived from it, every step of the derivation "
        "a non-virtual base at offset 0. The returned address is the same "
        "either way and a return type is not part of a (non-template) mangled "
        "name, so an existing binary keeps working, and for a virtual "
        "overrider no vtable slot or thunk is added (Itanium C++ ABI 2.5.3). "
        "Callers that rebuild see the narrower type, which can change overload "
        "resolution or template deduction on the result: a source-level risk.",
        description_template="Return type narrowed to a derived class at offset 0: {name}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_DH,),
    ),
    _E(
        "func_became_final",
        _A,
        impact="A virtual member function gained the `final` virt-specifier. "
        "Its vtable slot and its symbol are unchanged, so built consumers keep "
        "working, but a consumer class that overrides it no longer compiles "
        "([class.virtual]/4).",
        description_template="Virtual method became final: {name}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
    _E(
        "func_lost_final",
        _R,
        impact="A virtual member function lost the `final` virt-specifier. "
        "Overriding it is allowed again and existing source still compiles, but "
        "a call compiled while it was final may have been devirtualized, so an "
        "override added now is not reached from that code.",
        description_template="Virtual method lost final: {name}",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
        observable_at=(_OBS_H,),
    ),
]
