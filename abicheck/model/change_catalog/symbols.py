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

from .registry import ChangeKindMeta, Verdict

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta

SYMBOLS_ENTRIES: list[ChangeKindMeta] = [
    _E("anon_field_changed", _B,
       impact="An anonymous struct/union member changed — either its type "
              "changed at the same offset, or it was removed entirely (this "
              "detector reports both under one kind, and only compares the "
              "anonymous member's own type spelling — it never inspects "
              "the new type's own nested member names). When the type "
              "changed: since it has no name to distinguish it, every "
              "named member reached through it may have shifted offset — "
              "an already-compiled consumer (or a mixed build linking old "
              "objects against the new library) still reading/writing at "
              "the old offsets now hits the wrong bytes. Recompiled source "
              "picks up the new offsets and is unaffected only if the new "
              "anonymous type promotes the same named members as before; "
              "if the replacement type renamed or dropped one of them "
              "(e.g. `s.member` no longer exists), recompiling fails too, "
              "the same as the removed-entirely case below. When it was "
              "removed: every named member promoted from it (e.g. "
              "`s.member`, reached directly through the enclosing "
              "struct/union) is simply gone, so source referencing any of "
              "them fails to compile against the new headers too — "
              "recompilation does not make this case safe."),
    _E("calling_convention_changed", _B,
       impact="Function calling convention changed; registers/stack usage differs, call crashes.",
       policy_overrides={"plugin_abi": _C}),
    _E("constant_added", _C, is_addition=True,
       impact="A new public const/constexpr declaration appeared in the "
              "public headers (`parse_constants()` extracts public "
              "`const`/`constexpr` variables and static data members with "
              "a compile-time initializer, not preprocessor macros — a "
              "separate detector family, `PUBLIC_MACRO_*`, covers those; "
              "this can be a plain internal-linkage namespace-scope "
              "constant or a `static constexpr` member/`inline constexpr` "
              "variable carrying external linkage and a real exported "
              "symbol — this detector doesn't distinguish the two). "
              "Either way, an already-compiled binary is unaffected by an "
              "*addition* specifically — there's no pre-existing reference "
              "to break. Source is not unconditionally safe, though: this "
              "detector checks only that the constant is new, not whether "
              "its name collides with an identifier a consumer already "
              "declares at the same scope — an ordinary redeclaration/"
              "ambiguity error, not a macro textual-substitution hazard.",
       description_template="New preprocessor constant: {name}"),
    _E("constant_changed", _A,
       impact="A public const/constexpr declaration's value changed "
              "(`parse_constants()` extracts public `const`/`constexpr` "
              "variables and static data members with a compile-time "
              "initializer, not preprocessor macros — a separate detector "
              "family, `PUBLIC_MACRO_*`, covers those). For a plain "
              "internal-linkage namespace-scope constant, any consumer "
              "that was compiled against the old value has that value "
              "baked in via ordinary compile-time constant folding, not "
              "looked up at link/load time, so it keeps behaving as if the "
              "constant were still the old value until it is recompiled. "
              "This detector doesn't distinguish that case from a "
              "`static constexpr` member/`inline constexpr` variable "
              "carrying external linkage and a real exported symbol: if a "
              "consumer took the constant's address rather than only "
              "using its value, it reads the new value at load time "
              "(through the exported symbol) instead of keeping the old "
              "one until recompiled.",
       description_template="Preprocessor constant value changed: {name} ({old} → {new})"),
    _E("constant_removed", _A,
       impact="A public const/constexpr declaration was removed from the "
              "public headers (`parse_constants()` extracts public "
              "`const`/`constexpr` variables and static data members with "
              "a compile-time initializer, not preprocessor macros — a "
              "separate detector family, `PUBLIC_MACRO_*`, covers those); "
              "source code referencing it by name fails to compile against "
              "the new headers either way. For a plain internal-linkage "
              "namespace-scope constant, an already-compiled binary is "
              "unaffected (the old value was already folded in at its own "
              "compile time, and the declaration never had an exported "
              "symbol) — but this detector doesn't distinguish that case "
              "from a `static constexpr` member/`inline constexpr` "
              "variable carrying external linkage and a real exported "
              "symbol, whose removal breaks an already-linked consumer "
              "resolving that symbol at load time the same way a removed "
              "function or variable would.",
       description_template="Preprocessor constant removed: {name}"),
    _E("ctor_explicit_added", _A,
       impact="A constructor or conversion operator gained the `explicit` "
              "specifier. Source code that relied on implicit conversion "
              "(copy-initialization like `Foo f = 42;`, pass-by-value at a "
              "call site, or return-by-implicit-conversion) no longer "
              "compiles. The mangled name is unchanged so binaries keep "
              "running, but recompilation against the new header fails."),
    _E("ctor_explicit_removed", _R,
       impact="A constructor or conversion operator lost the `explicit` "
              "specifier. Existing code keeps compiling, but implicit "
              "conversion paths that previously did not consider this "
              "function now do, potentially selecting a different overload "
              "than before and causing silent behavioral drift."),
    _E("ctor_overload_ambiguity_risk", _R,
       impact="A class gained a second (or later) non-explicit, single-"
              "argument constructor. Any call site whose argument type is "
              "implicitly convertible to more than one of the class's "
              "converting constructors becomes ambiguous — it either stops "
              "compiling or silently resolves to a different constructor "
              "than before. This cannot be proven from a header/binary "
              "snapshot alone (it depends on actual call-site argument "
              "types), so it is reported as a risk to review, not a "
              "certain break.",
       description_template="Class '{name}' gained a 2nd+ non-explicit converting constructor: {new}"),
    _E("field_access_changed", _A,
       impact="Field access level narrowed; old code accessing it won't compile.",
       policy_overrides={"sdk_vendor": _C},
       description_template="Field access level narrowed: {name}::{detail} ({old} → {new})"),
    _E("func_added", _C, is_addition=True,
       impact="New function available; existing binaries are unaffected.",
       description_template="New public function: {new}"),
    _E("func_became_inline", _A,
       impact="A function gained the inline attribute. This detector "
              "(`_check_inline_transitions()`) only compares the `is_inline` "
              "specifier and export presence, with no language gate — it "
              "can't establish whether the function's actual definition is "
              "still visible to a recompiling consumer's translation unit, "
              "nor which language's inline rules apply, so source "
              "compatibility isn't guaranteed unconditionally. If the "
              "header now declares the function inline without exposing a "
              "definition at all (e.g. the definition still lives in a "
              "source file the consumer doesn't include), an odr-used "
              "inline function with no visible definition in that "
              "translation unit fails to link, in C++ and C alike. When a "
              "definition is visible, the two languages still differ: in "
              "C++, recompiled source keeps working (each translation unit "
              "gets its own copy, folded together at link time). In C99/"
              "GNU C, a visible `inline` definition (without `extern`) "
              "does not itself guarantee an external definition exists "
              "anywhere — a non-static function's recompiled callers can "
              "still be left with an unresolved external reference at "
              "link time unless some translation unit provides a real "
              "out-of-line instantiation (an `extern` declaration paired "
              "with a matching definition, or a definition without "
              "`inline` at all). An already-linked consumer's outcome "
              "separately depends on what the new library actually ships: "
              "the exported symbol commonly disappears once nothing forces "
              "an out-of-line definition, in which case a caller resolving "
              "it at link/load time gets an undefined-symbol error, while "
              "a symbol kept exported (e.g. still ODR-used elsewhere in "
              "the library) leaves already-linked callers unaffected. This "
              "C99/GNU C description assumes the default ISO C99/C11 "
              "`inline` semantics; under the older GNU89 inline dialect "
              "(`-std=gnu89` or `-fgnu89-inline`, GCC's default before "
              "adopting C99 semantics), the plain-`inline`/`extern inline` "
              "correspondence is inverted — a non-`extern` `inline` "
              "definition itself produces an external definition — so this "
              "specific unresolved-reference risk does not apply to code "
              "compiled under that dialect."),
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
    _E("func_deleted", _B,
       impact="Function marked = delete; old binaries still call it, getting link error or UB.",
       description_template="Function explicitly deleted (= delete): {name}"),
    _E("func_deleted_dwarf", _B,
       impact="Function marked as deleted (= delete) detected via DWARF debug info. "
              "The function was previously callable; callers will fail to link.",
       description_template="Function explicitly deleted (= delete): {name}"),
    _E("func_deprecated_added", _C,
       impact="Function gained [[deprecated]]; callers now get a compiler "
              "warning when calling it. This detector matches functions by "
              "mangled name and only checks the deprecated flag — it "
              "doesn't verify the signature is otherwise unchanged (a "
              "return-type change, for instance, doesn't affect Itanium "
              "mangling), so a companion finding for such a change is "
              "possible. A consumer whose own "
              "build treats warnings as errors (e.g. "
              "-Werror=deprecated-declarations) has this turn a previously "
              "clean build into a failing one, so it isn't unconditionally "
              "\"not a break\" for source compatibility.",
       description_template="Function marked deprecated: {name} ({detail})"),
    _E("func_deprecated_removed", _C,
       impact="Function's [[deprecated]] marker was removed; the compiler "
              "warning stops, with no effect on the function's ABI.",
       description_template="Function no longer marked deprecated: {name}"),
    _E("func_exception_spec_changed", _R,
       impact="The function's dynamic exception specification (throw(...)) "
              "changed in a way the noexcept kinds do not cover. Old callers "
              "compiled against the previous specification may have exception "
              "tables and unwind assumptions that no longer match; a "
              "violated specification calls std::unexpected/std::terminate.",
       description_template="Exception specification changed: {name} ({old} → {new})"),
    _E("func_language_linkage_changed", _B,
       impact="Language linkage changed (extern \"C\" ↔ C++); the mangled symbol name "
              "changes, so old binaries reference a symbol that no longer exists under "
              "that name.",
       description_template="Language linkage changed: {name} ({old} → {new})"),
    _E("func_likely_renamed", _B,
       impact="Function likely renamed (binary fingerprint match: identical code size and hash, "
              "different symbol name). Old binaries reference the old name and will fail to "
              "resolve at load time. This is a heuristic signal — verify the rename is intentional.",
       description_template="Function likely renamed: {old} → {new} (size={detail}B, confidence={name}%)"),
    _E("func_lost_inline", _C,
       impact="A function lost its explicit inline attribute. This "
              "detector (`_check_inline_transitions()`) fires the same way "
              "regardless of language, but the two languages' `inline` "
              "semantics differ enough that the consequence does too. In "
              "C++, an ordinary function already has external linkage "
              "regardless of inline — the attribute only permits the "
              "compiler to fold identical out-of-line definitions emitted "
              "by multiple translation units into one, so losing it "
              "doesn't itself create or guarantee a new export (this "
              "detector doesn't verify the new binary's export table). "
              "None of the C-specific risk below applies to a function "
              "with internal linkage either, in C or C++ — `static`, or, "
              "in C++, declared inside an unnamed namespace: either gives "
              "the function internal linkage regardless of `inline`, so "
              "each translation unit keeps its own private definition and "
              "there is no cross-TU multiple-definition to create. This "
              "detector's `Function.is_static` captures only the `static` "
              "keyword, not unnamed-namespace membership, and gates on "
              "neither — a C++ function in an unnamed namespace still "
              "reads as non-static here even though it has the same "
              "internal linkage `static` gives. In C (C99/GNU C `inline` "
              "rules), for a function with external linkage the opposite "
              "risk applies: an "
              "`inline` function definition kept in a public header "
              "normally produces no external definition on its own, but "
              "removing `inline` from a definition every including "
              "translation unit sees turns it into an ordinary external "
              "definition — so multiple translation units that each "
              "`#include` the header and get recompiled can each emit "
              "their own external definition of the same name, producing "
              "multiple-definition link errors that don't arise in the "
              "C++ case. This C description assumes the default ISO C99/"
              "C11 `inline` semantics; under the older GNU89 inline "
              "dialect (`-std=gnu89` or `-fgnu89-inline`, GCC's default "
              "before adopting C99 semantics), the plain-`inline`/`extern "
              "inline` correspondence is inverted — a non-`extern` "
              "`inline` definition already produces an external "
              "definition — so losing `inline` there does not create this "
              "specific multiple-definition risk the same way. This "
              "detector matches functions by mangled name "
              "and only checks `is_inline` — it doesn't verify the "
              "signature is otherwise unchanged, and a mangled name alone "
              "doesn't guarantee that: a return-type change, for "
              "instance, doesn't affect Itanium mangling, so a companion "
              "`func_return_changed` finding is possible on the same "
              "matched pair and takes precedence over this one's low-risk "
              "read. When no companion signature finding fires, the "
              "function's own signature and calling convention are "
              "unchanged, so this is low-risk for already-linked "
              "consumers.",
       description_template="Function lost inline attribute: {name}"),
    _E("func_noexcept_added", _C,
       impact="In C++17 noexcept is part of the function type; old callers compiled against non-noexcept signature get a different mangled name."),
    _E("func_noexcept_removed", _R,
       impact="`noexcept` removed from a function. Old binaries keep resolving "
              "the symbol, so this is not a binary break — but since C++17 "
              "`noexcept` is part of the function *type*, so it is encoded in "
              "function-pointer and template-argument mangling: consumers that "
              "form a `void(*)() noexcept` pointer or pass the function as a "
              "non-type template argument no longer compile, and code relying on "
              "the guarantee can hit `std::terminate`. KDE's C++ binary-"
              "compatibility policy treats removing `noexcept` as a change to "
              "avoid unless it was `noexcept(false)`. Verdict is policy-"
              "adjustable; raise to API_BREAK under a strict source profile."),
    _E("func_override_specifier_added", _C,
       impact="A virtual method gained the explicit `override` specifier. "
              "Purely a compiler self-check on the declaration; the method's "
              "signature and ABI are unchanged.",
       description_template="Method gained `override` specifier: {name}"),
    _E("func_override_specifier_removed", _R,
       impact="A virtual method lost the explicit `override` specifier while "
              "remaining virtual. The signature may be unchanged (informational "
              "only), but this can also be the visible symptom of the base "
              "declaration it used to override having changed or disappeared "
              "elsewhere — worth a quick check even though this fact alone "
              "does not prove a break.",
       description_template="Method lost `override` specifier: {name}"),
    _E("func_params_changed", _B,
       impact="Callers push arguments with the old layout; callee reads wrong data from stack/registers.",
       description_template="Parameters changed: {name}"),
    _E("func_removed", _B,
       impact="Old binaries call a symbol that no longer exists; dynamic linker will refuse to load or crash at call site."),
    _E("func_removed_elf_only", _B,
       impact="Exported function symbol removed from the binary; old binaries that link or dlsym() it can fail even without header evidence."),
    _E("func_return_changed", _B,
       impact="Callers expect the old return type layout in registers/stack; misinterpretation causes data corruption.",
       description_template="Return type changed: {name}"),
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
    _E("func_virtual_added", _B,
       impact="Vtable layout changes; old binaries call wrong virtual function slot, leading to crashes or wrong behavior."),
    _E("func_virtual_removed", _B,
       impact="Vtable entry removed; old binaries that dispatch through the vtable call the wrong slot."),
    _E("func_visibility_changed", _B,
       impact="Symbol hidden from dynamic linking; old binaries can't find it at load time.",
       description_template="Function visibility changed to hidden: {name}"),
    _E("hidden_friend_added", _C, is_addition=True,
       impact="A new in-class `friend` declaration was added. Pure "
              "addition: existing code keeps compiling, no symbol "
              "disappears, and the new operator/function only "
              "participates in overload resolution at call sites that "
              "trigger ADL on one of its argument types.",
       description_template="Hidden friend declaration added: {new}"),
    _E("hidden_friend_removed", _A,
       impact="An in-class `friend` declaration (a 'hidden friend' — "
              "findable only via ADL on one of its argument types) was "
              "removed. Inline hidden friends never receive an external "
              "symbol, so the break is invisible at the binary layer, but "
              "every consumer that wrote `a + b` (or any other ADL-driven "
              "call site) fails to compile against the new headers. When "
              "the friend was also defined out-of-line, removal "
              "additionally surfaces as FUNC_REMOVED at link time.",
       description_template="Hidden friend declaration removed: {old}"),
    _E("internal_symbol_required_by_public_api", _B,
       impact="An internal-namespaced decl (e.g. ::detail::, ::impl::, "
              "::internal::) that already changed in an artifact-proven "
              "breaking way (e.g. func_removed) is called or referenced from "
              "a public entry point over the optional L5 source/call graph "
              "(--sources/--build-info/--header-graph). Although the symbol "
              "is conceptually internal, it is part of the effective public "
              "ABI: an application built against the old public entry point "
              "can fail to resolve it at load time. Call-graph analogue of "
              "INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API (ADR-044 P1 items 1-2), for "
              "the pure-call shape that walk's layout-only reachability model "
              "cannot see (no field/base/signature evidence, only a call "
              "edge)."),
    _E("method_access_changed", _A,
       impact="Method access level narrowed (e.g. public→private); old code calling it won't compile.",
       policy_overrides={"sdk_vendor": _C},
       description_template="Method access level narrowed: {name} ({old} → {new})"),
    _E("param_became_va_list", _C,
       impact="A parameter's type became va_list; source recompiled "
              "against the new header sees a different, variadic-style "
              "parameter. This detector only checks the declared type "
              "flip, not whether the two representations agree: va_list's "
              "own layout is platform-defined (an opaque struct/array on "
              "many ABIs, not a plain pointer), so an already-linked "
              "caller still passing its old, non-variadic argument has "
              "that value reinterpreted by the new callee as a va_list "
              "handle — safe only if the caller already happened to pass "
              "a genuine, compatible va_list object (e.g. a forwarding "
              "wrapper).",
       description_template="Parameter became va_list: {name} param {detail}"),
    _E("param_default_value_changed", _C,
       impact="A parameter's default argument value changed; a default "
              "argument is substituted at the *caller's* compile time, so "
              "an already-compiled consumer that omitted the argument "
              "keeps using the old value baked in, while source recompiled "
              "from a call site that omits the argument picks up the new "
              "one.",
       description_template="Parameter default changed: {name} param {detail}"),
    _E("param_default_value_removed", _A,
       impact="A parameter's default argument was removed; source code "
              "that relied on omitting this argument no longer compiles "
              "and must now supply it explicitly. An already-compiled "
              "consumer is unaffected, since the default was already "
              "substituted in at its own compile time.",
       policy_overrides={"sdk_vendor": _C},
       description_template="Parameter default removed: {name} param {detail}"),
    _E("param_lost_va_list", _C,
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
       description_template="Parameter was va_list, now fixed: {name} param {detail}"),
    _E("param_pointer_level_changed", _B,
       impact="A parameter's pointer indirection depth changed (e.g. T* → "
              "T** or the reverse); the value passed for that argument is "
              "interpreted differently by callee and caller, so a caller "
              "compiled against the old signature passes the wrong kind "
              "of value — silent misinterpretation or a crash.",
       description_template="Parameter pointer level changed: {name} param {detail} (depth {old} → {new})"),
    _E("param_renamed", _A,
       impact="A parameter's name changed; this has no effect on the "
              "compiled ABI (parameter names aren't part of the mangled "
              "signature or calling convention), but source using named-"
              "argument-style calls, or documentation/IDE tooling relying "
              "on the old name, may be affected.",
       policy_overrides={"sdk_vendor": _C},
       description_template="Parameter renamed: {name} param {detail}: {old} → {new}"),
    _E("param_restrict_changed", _C,
       impact="A parameter's restrict qualifier was added or removed "
              "(direction recorded in this finding's own detail); restrict "
              "is a compiler hint affecting how the library's own "
              "implementation of the function is optimized, not the "
              "calling convention. The two directions carry different "
              "risk: when restrict is ADDED, the new library's own "
              "compiled code may now assume the parameter doesn't alias "
              "other arguments — an already-compiled caller that still "
              "passes aliased pointers for that parameter can hit "
              "undefined behavior in the new callee's optimized code, with "
              "no recompilation of the caller involved. When restrict is "
              "REMOVED, the callee simply becomes more conservative "
              "(drops an optimization assumption), which is safe for every "
              "caller.",
       description_template="Parameter restrict qualifier {detail}: {name} param {old}"),
    _E("public_api_exposes_stl_by_value", _R,
       impact="A public function takes or returns a `std::` type by value across "
              "the library boundary. Standard-library layouts (string, vector, "
              "etc.) differ across toolchains, standard-library versions, and "
              "the C++11 dual-ABI setting, so passing one by value at the ABI "
              "boundary is fragile: a consumer built with a different STL silently "
              "reads the wrong layout. Pass an opaque handle or a C-style view "
              "instead."),
    _E("python_abi3_dropped", _R,
       impact="A CPython extension module that was previously a stable-ABI "
              "(`abi3` / `Py_LIMITED_API`) build — loadable on every interpreter "
              "at or above its floor — is now a version-specific build (its SOABI "
              "tag pins it to a single `cpython-3XX`). Consumers running any other "
              "interpreter in the module's former supported range can no longer "
              "import it. Nothing in the export table reveals the narrowed "
              "support; the promise lived in the wheel/SOABI tag. A deployment "
              "RISK for anyone not on the exact new interpreter.",
       description_template="extension '{name}' dropped its abi3 promise: {old} → {new}"),
    _E("python_abi3_floor_raised", _R,
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
       description_template="abi3 extension '{name}' raised its Py_LIMITED_API floor: {old} → {new}"),
    _E("python_api_callable_kind_changed", _A,
       impact="A callable's *protocol* changed in the module's Python-visible "
              "API even though its parameter list did not: `def`↔`async def` "
              "(callers must now `await`, or must stop awaiting, the result), or "
              "a class member changed between instance method, `@staticmethod`, "
              "`@classmethod`, and `@property`. Each of these changes how an "
              "existing site calls or accesses the member — an awaited call, a "
              "class-level vs instance-level bind, or attribute access vs a call "
              "— so it breaks callers. The compiled binary is unchanged. "
              "Source-level (`API_BREAK`).",
       description_template="Python callable kind changed for {name}: {detail}"),
    _E("python_api_class_added", _C, is_addition=True,
       impact="A new public class was added to the module's Python-visible API. "
              "Additive — existing callers are unaffected.",
       description_template="New Python class in extension API: {name}"),
    _E("python_api_class_removed", _A,
       impact="A public class was removed from a CPython extension module's "
              "Python-visible API. The binary still loads, but consumers that "
              "reference the class break at import/attribute-access time. A "
              "source-level (`API_BREAK`) change invisible to the C-ABI view.",
       description_template="Python class removed from extension API: {name}"),
    _E("python_api_default_removed", _A,
       impact="A parameter lost its default value in the module's "
              "Python-visible API, making a previously optional argument "
              "mandatory. Callers relying on the default now raise a "
              "missing-argument `TypeError`. Source-level (`API_BREAK`).",
       description_template="Python parameter default removed in {name}: {detail}"),
    _E("python_api_function_added", _C, is_addition=True,
       impact="A new public top-level function was added to the module's "
              "Python-visible API. Additive — existing callers are unaffected.",
       description_template="New Python function in extension API: {name}"),
    _E("python_api_function_removed", _A,
       impact="A public top-level function was removed from a CPython extension "
              "module's Python-visible API (recovered from its `.pyi` type "
              "stub). The compiled `.so`/`.pyd` still loads — its C-ABI export "
              "table is unchanged — but any consumer that `import`s and calls "
              "the function now fails with an `AttributeError` / `ImportError`. "
              "A source-level (`API_BREAK`) change the native-ABI check cannot "
              "see.",
       description_template="Python function removed from extension API: {name}"),
    _E("python_api_method_added", _C, is_addition=True,
       impact="A new public method was added to an existing class in the "
              "module's Python-visible API. Additive — existing callers are "
              "unaffected.",
       description_template="New Python method in extension API: {name}"),
    _E("python_api_method_removed", _A,
       impact="A public method was removed from a class that still exists in the "
              "module's Python-visible API. Callers of the method break at "
              "attribute-access time even though the class and the compiled "
              "binary are otherwise unchanged. Source-level (`API_BREAK`).",
       description_template="Python method removed from extension API: {name}"),
    _E("python_api_overload_removed", _A,
       impact="An `@overload` signature variant was dropped from an overloaded "
              "function/method in the module's Python-visible API. Typed callers "
              "that relied on that particular call shape (e.g. passing an `int` "
              "where only a `str` overload now remains) lose a supported "
              "signature — a source-level break invisible to the export table. "
              "Adding an overload is compatible and not reported. "
              "Source-level (`API_BREAK`).",
       description_template="Python overload removed from {name}: {detail}"),
    _E("python_api_parameter_added", _A,
       impact="A new *required* parameter (one with no default) was added to a "
              "function/method in the module's Python-visible API. Every "
              "existing call that omitted it now raises a missing-argument "
              "`TypeError`. Source-level (`API_BREAK`); a new *optional* "
              "parameter would be compatible and is not reported.",
       description_template="Required Python parameter added to {name}: {detail}"),
    _E("python_api_parameter_kind_changed", _A,
       impact="A parameter's *binding* changed in the module's Python-visible "
              "API even though its name did not: it went positional↔keyword-only, "
              "keyword→positional-only, or the positional order/position shifted "
              "(a reordered or mid-inserted parameter). Existing call sites that "
              "pass the argument by position or by keyword now bind it "
              "differently — a positional caller lands on the wrong parameter, or "
              "a keyword caller hits an unexpected-keyword `TypeError`. The "
              "compiled binary is unchanged; the break lives in the call shape. "
              "Source-level (`API_BREAK`).",
       description_template="Python parameter binding changed in {name}: {detail}"),
    _E("python_api_parameter_removed", _A,
       impact="A parameter was removed from a function/method in the module's "
              "Python-visible API. Any caller that passed that argument (by "
              "position or keyword) now raises a `TypeError`. The C-ABI is "
              "unchanged; the break lives in the Python signature. "
              "Source-level (`API_BREAK`).",
       description_template="Python parameter removed from {name}: {detail}"),
    _E("python_api_parameter_renamed", _A,
       impact="A parameter was renamed in a function/method of the module's "
              "Python-visible API. Callers that passed it by keyword hit an "
              "unexpected-keyword `TypeError`. The compiled binary is "
              "byte-identical — this is the canonical break the native-ABI "
              "check misses. Source-level (`API_BREAK`).",
       description_template="Python parameter renamed in {name}: {old} → {new}"),
    _E("python_api_parameter_type_changed", _R,
       impact="A parameter's type annotation changed in the module's "
              "Python-visible API. This is a type-checker / behavioural "
              "signal, not a hard runtime break: existing calls still execute, "
              "but static analysis and callers relying on the old contract may "
              "be affected. A `RISK`.",
       description_template="Python parameter type changed in {name}: {detail} ({old} → {new})"),
    _E("python_api_return_type_changed", _R,
       impact="A function/method's return type annotation changed in the "
              "module's Python-visible API. Callers may mishandle the returned "
              "value, but existing calls still execute — a behavioural / "
              "type-checker `RISK`, not a hard break.",
       description_template="Python return type changed for {name}: {old} → {new}"),
    _E("python_api_stub_invalid", _A,
       impact="A shipped Python type stub for the new extension artifact could "
              "not be safely parsed (syntax error, unreadable file, or size "
              "limit). The Python API surface is therefore untrusted and must "
              "fail closed rather than disabling Python-level API checks.",
       description_template="Invalid Python API stub for extension module: {detail}"),
    _E("python_gil_abi_changed", _R,
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
       description_template="extension '{name}' changed GIL/free-threaded ABI: {old} → {new}"),
    _E("python_stable_abi_violation", _R,
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
       description_template="abi3 extension '{name}' imports non-stable CPython symbol: {detail}"),
    _E("return_pointer_level_changed", _B,
       impact="A function's return type's pointer indirection depth "
              "changed; a caller compiled against the old signature reads "
              "the returned value as the wrong kind of pointer — silent "
              "misinterpretation or a crash.",
       description_template="Return pointer level changed: {name} (depth {old} → {new})"),
    _E("serialization_tag_changed", _B,
       impact="A serialization tag ID (or equivalent constant identifying a class "
              "for persistence) changed value or was swapped with another class's "
              "tag. Symbol table, types, and layout are all unchanged — every "
              "conventional ABI check passes. But saved models / persisted state "
              "from the old library deserialize as the wrong class against the new "
              "library, silently corrupting data. Common in "
              "SerializationIface-style designs."),
    _E("symbol_renamed_batch", _B,
       impact="Multiple symbols renamed (e.g. namespace prefix added/removed); "
              "old binaries reference the old names and will get undefined symbol errors at load time.",
       description_template="Batch symbol rename detected (namespace refactoring): prefix '{name}' added to {detail}"),
    _E("symbol_size_changed", _B,
       impact="ELF symbol size changed; copy relocations or memcpy-based consumers get truncated/oversized data.",
       description_template="Symbol size changed: {name} ({old} → {new} bytes)"),
    _E("symbol_size_changed_const_object", _B,
       impact="ELF size changed on a public const string-like object declared without a fixed bound in headers. "
              "Old non-PIE consumers may have copy relocations sized from the old DSO symbol, so a later DSO can "
              "truncate or otherwise mis-copy data at load time.",
       description_template="Symbol size changed: {name} ({old} → {new} bytes)"),
    _E("symbol_size_changed_internal", _B,
       impact="ELF size changed on an internal-looking (reserved/underscore-prefixed) exported data symbol; "
              "exported data remains part of the dynamic ABI and size changes can break copy relocations "
              "or direct data consumers. Override severity via --policy only when the symbol is known private.",
       description_template="Symbol size changed: {name} ({old} → {new} bytes)"),
    _E("var_access_changed", _A,
       impact="A variable's access level narrowed (e.g. public→private); "
              "source code that previously accessed it directly no longer "
              "compiles. An already-compiled consumer accessing the "
              "exported symbol directly is unaffected, since binary "
              "access control isn't enforced at link/load time.",
       description_template="Variable access level narrowed: {name} ({old} → {new})"),
    _E("var_access_widened", _C,
       impact="A variable's access level widened (e.g. private→public); "
              "this only grants new source-level access and cannot break "
              "anything that already compiled successfully.",
       description_template="Variable access level widened: {name} ({old} → {new})"),
    _E("var_added", _C, is_addition=True,
       impact="New variable available; existing binaries are unaffected.",
       description_template="New public variable: {name}"),
    _E("var_alignment_changed", _B,
       impact="An exported variable's declared alignment changed. Consumers "
              "compiled against the old alignment use matching aligned "
              "load/store instructions and copy-relocation slot sizes; a "
              "reduced alignment faults strict-alignment/SIMD access, and any "
              "change breaks layout assumptions baked into old binaries.",
       description_template="Variable alignment changed: {name} ({old} → {new} bits)"),
    _E("var_became_const", _B,
       impact="Variable moved to read-only section; old code writing to it gets SIGSEGV."),
    _E("var_deprecated_added", _C,
       impact="Variable gained [[deprecated]]; consumers get a compiler "
              "warning when referencing it. This detector matches "
              "variables by mangled name and only checks the deprecated "
              "flag — it doesn't verify the type is otherwise unchanged "
              "(a variable's type isn't encoded in its mangled name the "
              "way a function's parameters are), so a companion finding "
              "for a type change is possible. A consumer building with "
              "warnings as errors (e.g. -Werror=deprecated-declarations) "
              "has this turn a previously clean build into a failing one.",
       description_template="Variable marked deprecated: {name} ({detail})"),
    _E("var_deprecated_removed", _C,
       impact="Variable's [[deprecated]] marker was removed; the compiler "
              "warning stops, with no effect on the variable's ABI.",
       description_template="Variable no longer marked deprecated: {name}"),
    _E("var_lost_const", _B,
       impact="Variable no longer const; ODR violations possible if old code inlined the value."),
    _E("var_removed", _B,
       impact="Old binaries reference a global variable that no longer exists; link or load failure.",
       description_template="Public variable removed: {name}"),
    _E("var_type_changed", _B,
       impact="Old binaries read/write the variable with wrong size or layout; data corruption or segfault.",
       description_template="Variable type changed: {name}"),
]
