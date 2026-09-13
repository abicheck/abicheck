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


"""ADR-061 D9 taxonomy: symbol-level ChangeKind entries.

Function, variable, parameter, constant, and Python-API declaration facts --
the entities a linker/dynamic loader resolves by name, plus the C/C++ and
Python signature-level facts attached to them (linkage, inline-ness,
default arguments, access level, calling-convention-neutral qualifiers).
Distinguished from ``types.py`` (the type/layout side of the same
declarations) and from ``platform.py`` (the binary symbol-table
*representation* of the same names -- ELF/PE symbol binding, visibility,
and versioning, which are a platform-format concern rather than a
language-level one).

Categorized by which detector module actually produces each kind (verified
against the real ``ChangeKind.X`` construction sites in ``diff_symbols.py``
and its siblings -- ``diff_symbols_variables.py``, ``diff_symbols_renames.py``,
``diff_param_qualifiers.py``, ``diff_hidden_friends.py``,
``diff_python_api.py``, ``diff_python.py`` -- not by which flat
``change_registry_*.py`` sibling an entry happened to live in for pure
line-count reasons before this migration.
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

SYMBOLS_ENTRIES_2: list[ChangeKindMeta] = [
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
        operation=_OP.MODIFIED,
    ),
    _E(
        "python_abi3_dropped",
        _R,
        impact="A CPython extension module that was previously a stable-ABI "
        "(`abi3` / `Py_LIMITED_API`) build — loadable on every interpreter "
        "at or above its floor — is now a version-specific build (its SOABI "
        "tag pins it to a single `cpython-3XX`). Consumers running any other "
        "interpreter in the module's former supported range can no longer "
        "import it. Nothing in the export table reveals the narrowed "
        "support; the promise lived in the wheel/SOABI tag. A deployment "
        "RISK for anyone not on the exact new interpreter.",
        description_template="extension '{name}' dropped its abi3 promise: {old} → {new}",
        entity=_ENT.BUILD,
        operation=_OP.MODIFIED,
    ),
    _E(
        "python_abi3_floor_raised",
        _R,
        impact="Both builds of a CPython extension are stable-ABI (`abi3`) and both "
        "carry an explicit `cpXY-abi3` wheel/SOABI tag, but the new build's "
        "declared `Py_LIMITED_API` floor is higher than the old one's "
        "(e.g. `cp39-abi3` → `cp310-abi3`). Every interpreter in the dropped "
        "range — CPython at or above the old floor but below the new one — "
        "can no longer import the module, even though its exported and "
        "imported symbols may be unchanged. Because the floor is read from "
        "the explicit tag on *both* sides, this is exact (no heuristic "
        "min-of-imports inference). A deployment RISK: whether it breaks "
        "depends on which interpreters the consumer must support.",
        description_template="abi3 extension '{name}' raised its Py_LIMITED_API floor: {old} → {new}",
        entity=_ENT.BUILD,
        operation=_OP.MODIFIED,
    ),
    _E(
        "python_api_callable_kind_changed",
        _A,
        impact="A callable's *protocol* changed in the module's Python-visible "
        "API even though its parameter list did not: `def`↔`async def` "
        "(callers must now `await`, or must stop awaiting, the result), or "
        "a class member changed between instance method, `@staticmethod`, "
        "`@classmethod`, and `@property`. Each of these changes how an "
        "existing site calls or accesses the member — an awaited call, a "
        "class-level vs instance-level bind, or attribute access vs a call "
        "— so it breaks callers. The compiled binary is unchanged. "
        "Source-level (`API_BREAK`).",
        description_template="Python callable kind changed for {name}: {detail}",
        entity=_ENT.FUNCTION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "python_api_class_added",
        _C,
        is_addition=True,
        impact="A new public class was added to the module's Python-visible API. "
        "Additive — existing callers are unaffected.",
        description_template="New Python class in extension API: {name}",
        entity=_ENT.TYPE,
        operation=_OP.ADDED,
    ),
    _E(
        "python_api_class_removed",
        _A,
        impact="A public class was removed from a CPython extension module's "
        "Python-visible API. The binary still loads, but consumers that "
        "reference the class break at import/attribute-access time. A "
        "source-level (`API_BREAK`) change invisible to the C-ABI view.",
        description_template="Python class removed from extension API: {name}",
        entity=_ENT.TYPE,
        operation=_OP.REMOVED,
    ),
    _E(
        "python_api_default_removed",
        _A,
        impact="A parameter lost its default value in the module's "
        "Python-visible API, making a previously optional argument "
        "mandatory. Callers relying on the default now raise a "
        "missing-argument `TypeError`. Source-level (`API_BREAK`).",
        description_template="Python parameter default removed in {name}: {detail}",
        entity=_ENT.FUNCTION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "python_api_function_added",
        _C,
        is_addition=True,
        impact="A new public top-level function was added to the module's "
        "Python-visible API. Additive — existing callers are unaffected.",
        description_template="New Python function in extension API: {name}",
        entity=_ENT.FUNCTION,
        operation=_OP.ADDED,
    ),
    _E(
        "python_api_function_removed",
        _A,
        impact="A public top-level function was removed from a CPython extension "
        "module's Python-visible API (recovered from its `.pyi` type "
        "stub). The compiled `.so`/`.pyd` still loads — its C-ABI export "
        "table is unchanged — but any consumer that `import`s and calls "
        "the function now fails with an `AttributeError` / `ImportError`. "
        "A source-level (`API_BREAK`) change the native-ABI check cannot "
        "see.",
        description_template="Python function removed from extension API: {name}",
        entity=_ENT.FUNCTION,
        operation=_OP.REMOVED,
    ),
    _E(
        "python_api_method_added",
        _C,
        is_addition=True,
        impact="A new public method was added to an existing class in the "
        "module's Python-visible API. Additive — existing callers are "
        "unaffected.",
        description_template="New Python method in extension API: {name}",
        entity=_ENT.FUNCTION,
        operation=_OP.ADDED,
    ),
    _E(
        "python_api_method_removed",
        _A,
        impact="A public method was removed from a class that still exists in the "
        "module's Python-visible API. Callers of the method break at "
        "attribute-access time even though the class and the compiled "
        "binary are otherwise unchanged. Source-level (`API_BREAK`).",
        description_template="Python method removed from extension API: {name}",
        entity=_ENT.FUNCTION,
        operation=_OP.REMOVED,
    ),
    _E(
        "python_api_overload_removed",
        _A,
        impact="An `@overload` signature variant was dropped from an overloaded "
        "function/method in the module's Python-visible API. Typed callers "
        "that relied on that particular call shape (e.g. passing an `int` "
        "where only a `str` overload now remains) lose a supported "
        "signature — a source-level break invisible to the export table. "
        "Adding an overload is compatible and not reported. "
        "Source-level (`API_BREAK`).",
        description_template="Python overload removed from {name}: {detail}",
        entity=_ENT.FUNCTION,
        operation=_OP.REMOVED,
    ),
    _E(
        "python_api_parameter_added",
        _A,
        impact="A new *required* parameter (one with no default) was added to a "
        "function/method in the module's Python-visible API. Every "
        "existing call that omitted it now raises a missing-argument "
        "`TypeError`. Source-level (`API_BREAK`); a new *optional* "
        "parameter would be compatible and is not reported.",
        description_template="Required Python parameter added to {name}: {detail}",
        entity=_ENT.FUNCTION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "python_api_parameter_kind_changed",
        _A,
        impact="A parameter's *binding* changed in the module's Python-visible "
        "API even though its name did not: it went positional↔keyword-only, "
        "keyword→positional-only, or the positional order/position shifted "
        "(a reordered or mid-inserted parameter). Existing call sites that "
        "pass the argument by position or by keyword now bind it "
        "differently — a positional caller lands on the wrong parameter, or "
        "a keyword caller hits an unexpected-keyword `TypeError`. The "
        "compiled binary is unchanged; the break lives in the call shape. "
        "Source-level (`API_BREAK`).",
        description_template="Python parameter binding changed in {name}: {detail}",
        entity=_ENT.FUNCTION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "python_api_parameter_removed",
        _A,
        impact="A parameter was removed from a function/method in the module's "
        "Python-visible API. Any caller that passed that argument (by "
        "position or keyword) now raises a `TypeError`. The C-ABI is "
        "unchanged; the break lives in the Python signature. "
        "Source-level (`API_BREAK`).",
        description_template="Python parameter removed from {name}: {detail}",
        entity=_ENT.FUNCTION,
        operation=_OP.MODIFIED,  # the function persists (Codex, PR #1284)
    ),
    _E(
        "python_api_parameter_renamed",
        _A,
        impact="A parameter was renamed in a function/method of the module's "
        "Python-visible API. Callers that passed it by keyword hit an "
        "unexpected-keyword `TypeError`. The compiled binary is "
        "byte-identical — this is the canonical break the native-ABI "
        "check misses. Source-level (`API_BREAK`).",
        description_template="Python parameter renamed in {name}: {old} → {new}",
        entity=_ENT.FUNCTION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "python_api_parameter_type_changed",
        _R,
        impact="A parameter's type annotation changed in the module's "
        "Python-visible API. This is a type-checker / behavioural "
        "signal, not a hard runtime break: existing calls still execute, "
        "but static analysis and callers relying on the old contract may "
        "be affected. A `RISK`.",
        description_template="Python parameter type changed in {name}: {detail} ({old} → {new})",
        entity=_ENT.FUNCTION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "python_api_return_type_changed",
        _R,
        impact="A function/method's return type annotation changed in the "
        "module's Python-visible API. Callers may mishandle the returned "
        "value, but existing calls still execute — a behavioural / "
        "type-checker `RISK`, not a hard break.",
        description_template="Python return type changed for {name}: {old} → {new}",
        entity=_ENT.FUNCTION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "python_api_stub_invalid",
        _A,
        impact="A shipped Python type stub for the new extension artifact could "
        "not be safely parsed (syntax error, unreadable file, or size "
        "limit). The Python API surface is therefore untrusted and must "
        "fail closed rather than disabling Python-level API checks.",
        description_template="Invalid Python API stub for extension module: {detail}",
        entity=_ENT.BUILD,
        operation=_OP.MODIFIED,
    ),
    _E(
        "python_gil_abi_changed",
        _R,
        impact="A CPython extension module switched between the regular (GIL) and "
        "the free-threaded (PEP 703, `Py_GIL_DISABLED`) CPython ABI — its "
        "SOABI tag gained or lost the free-threaded `t` marker "
        "(`cpython-3XX` ↔ `cpython-3XXt`). The two builds target different, "
        "non-interchangeable interpreter ABIs: a consumer running the "
        "regular interpreter cannot load a free-threaded build and vice "
        "versa (different extension suffix, different struct layouts, and — "
        "since `Py_LIMITED_API` is incompatible with `Py_GIL_DISABLED` — a "
        "free-threaded build can never be `abi3`). A deployment RISK: "
        "whether it breaks depends on which interpreter the consumer runs.",
        description_template="extension '{name}' changed GIL/free-threaded ABI: {old} → {new}",
        entity=_ENT.BUILD,
        operation=_OP.MODIFIED,
    ),
    _E(
        "python_stable_abi_violation",
        _R,
        impact="A stable-ABI (`abi3` / `Py_LIMITED_API`) CPython extension module — "
        "produced by Cython, pybind11, nanobind, or a hand-written C "
        "extension — gained an import of a CPython C-API symbol that is not "
        "part of the Limited API (typically a private `_Py*` symbol). The "
        "module still exports only `PyInit_<mod>`, so the export-table view "
        "sees no change, but the module now links a symbol outside its abi3 "
        "promise. On an interpreter built without that symbol exported it "
        "fails to import with an `undefined symbol` error. Verdict is a "
        "deployment RISK: whether it breaks depends on the target "
        "interpreter, not on the module's own consumers.",
        description_template="abi3 extension '{name}' imports non-stable CPython symbol: {detail}",
        entity=_ENT.BUILD,
        operation=_OP.MODIFIED,
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
        operation=_OP.MODIFIED,
    ),
    _E(
        "serialization_tag_changed",
        _B,
        impact="A serialization tag ID (or equivalent constant identifying a class "
        "for persistence) changed value or was swapped with another class's "
        "tag. Symbol table, types, and layout are all unchanged — every "
        "conventional ABI check passes. But saved models / persisted state "
        "from the old library deserialize as the wrong class against the new "
        "library, silently corrupting data. Common in "
        "SerializationIface-style designs.",
        # Polymorphic: `_collect_tag_constants` pools constants, variables and
        # enum members, so the declared entity is only the fallback (the one
        # two of the three resolve to) -- reasoning and contract in
        # `TestSerializationTagFindingsKeepTheirContributingSource`.
        entity=_ENT.VARIABLE,
        entity_from_field="entity_discriminator",
        operation=_OP.MODIFIED,
    ),
    _E(
        "symbol_renamed_batch",
        _B,
        impact="Multiple symbols renamed (e.g. namespace prefix added/removed); "
        "old binaries reference the old names and will get undefined symbol errors at load time.",
        description_template="Batch symbol rename detected (namespace refactoring): prefix '{name}' added to {detail}",
        entity=_ENT.BINARY,
        operation=_OP.MODIFIED,
    ),
    _E(
        "symbol_size_changed",
        _B,
        impact="ELF symbol size changed; copy relocations or memcpy-based consumers get truncated/oversized data.",
        description_template="Symbol size changed: {name} ({old} → {new} bytes)",
        entity=_ENT.VARIABLE,  # OBJECT/COMMON/TLS only
        operation=_OP.MODIFIED,
    ),
    _E(
        "symbol_size_changed_const_object",
        _B,
        impact="ELF size changed on a public const string-like object declared without a fixed bound in headers. "
        "Old non-PIE consumers may have copy relocations sized from the old DSO symbol, so a later DSO can "
        "truncate or otherwise mis-copy data at load time.",
        description_template="Symbol size changed: {name} ({old} → {new} bytes)",
        entity=_ENT.VARIABLE,  # OBJECT/COMMON/TLS only
        operation=_OP.MODIFIED,
    ),
    _E(
        "symbol_size_changed_internal",
        _B,
        impact="ELF size changed on an internal-looking (reserved/underscore-prefixed) exported data symbol; "
        "exported data remains part of the dynamic ABI and size changes can break copy relocations "
        "or direct data consumers. Override severity via --policy only when the symbol is known private.",
        description_template="Symbol size changed: {name} ({old} → {new} bytes)",
        entity=_ENT.VARIABLE,  # OBJECT/COMMON/TLS only
        operation=_OP.MODIFIED,
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
        operation=_OP.MODIFIED,
    ),
    _E(
        "var_access_widened",
        _C,
        impact="A variable's access level widened (e.g. private→public); "
        "this only grants new source-level access and cannot break "
        "anything that already compiled successfully.",
        description_template="Variable access level widened: {name} ({old} → {new})",
        entity=_ENT.VARIABLE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "var_added",
        _C,
        is_addition=True,
        impact="New variable available; existing binaries are unaffected.",
        description_template="New public variable: {name}",
        entity=_ENT.VARIABLE,
        operation=_OP.ADDED,
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
        operation=_OP.MODIFIED,
    ),
    _E(
        "var_became_const",
        _B,
        impact="Variable moved to read-only section; old code writing to it gets SIGSEGV.",
        entity=_ENT.VARIABLE,
        operation=_OP.MODIFIED,
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
        operation=_OP.MODIFIED,
    ),
    _E(
        "var_deprecated_removed",
        _C,
        impact="Variable's [[deprecated]] marker was removed; the compiler "
        "warning stops, with no effect on the variable's ABI.",
        description_template="Variable no longer marked deprecated: {name}",
        entity=_ENT.VARIABLE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "var_lost_const",
        _B,
        impact="Variable no longer const; ODR violations possible if old code inlined the value.",
        entity=_ENT.VARIABLE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "var_removed",
        _B,
        impact="Old binaries reference a global variable that no longer exists; link or load failure.",
        description_template="Public variable removed: {name}",
        entity=_ENT.VARIABLE,
        operation=_OP.REMOVED,
    ),
    _E(
        "var_type_changed",
        _B,
        impact="Old binaries read/write the variable with wrong size or layout; data corruption or segfault.",
        description_template="Variable type changed: {name}",
        entity=_ENT.VARIABLE,
        operation=_OP.MODIFIED,
    ),
]
