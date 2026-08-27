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

"""ABICC-parity and field/parameter-qualifier ChangeKind registry entries.

Split out of ``change_registry.py`` to keep that module under the
AI-readiness 2000-line hard cap, following the same pattern as
``change_registry_composition.py``/``change_registry_coverage.py``. These
entries are spliced into the single ``REGISTRY`` at import time — declaring
a kind here is exactly equivalent to declaring it in ``change_registry.py``.
This is a size-relief split only, not a taxonomy grouping (ADR-061 D9's
``model/change_catalog/{symbols,types,platform,build,source}.py`` taxonomy
repartition is a separate, not-yet-started piece of work — see that ADR's
Phase 5 section): the kinds here span field/parameter qualifiers, pointer
levels, template inner-type analysis, and assorted ABICC full-parity gaps
that were moved out purely to free room for their previously-missing
``impact`` text (D9's "complete metadata" catalog-validation property).
"""
from __future__ import annotations

from .change_registry_types import ChangeKindMeta, Verdict

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_E = ChangeKindMeta

PARITY_EXTENSION_ENTRIES: list[ChangeKindMeta] = [
    # ── ELF dynamic-section paths ──────────────────────────────────────────
    _E("rpath_changed", _C,
       impact="The binary's RPATH (its own runtime library search path) "
              "changed; this can change which copy of a dependency gets "
              "loaded at runtime, but doesn't affect the library's own "
              "exported ABI.",
       description_template="RPATH changed: {old} → {new}"),
    _E("runpath_changed", _C,
       impact="The binary's RUNPATH (a lower-priority runtime library search "
              "path, consulted after LD_LIBRARY_PATH) changed; this can "
              "change which copy of a dependency gets loaded at runtime, but "
              "doesn't affect the library's own exported ABI.",
       description_template="RUNPATH changed: {old} → {new}"),

    # ── Symbol metadata (STT_COMMON) ───────────────────────────────────────
    _E("common_symbol_risk", _C,
       impact="An exported symbol is a tentative definition (STT_COMMON); "
              "its final address and merge behavior across translation "
              "units is decided by the linker, which can differ across "
              "toolchains/link orders. Not itself a break, but a source of "
              "non-determinism worth being aware of.",
       description_template="Exported STT_COMMON symbol: {name} (resolution depends on linker/loader)"),

    # ── DWARF layout coverage ──────────────────────────────────────────────
    _E("dwarf_info_missing", _C,
       impact="The new binary carries no DWARF debug info — this detector "
              "only checks whether DWARF is present, not why it's absent, "
              "so this fires identically whether the binary was never "
              "compiled with -g or was compiled with -g and then stripped "
              "during packaging. Either way, DWARF-derived struct/enum "
              "layout comparisons couldn't run for it, so layout changes "
              "in this release, if any, went unchecked. Not itself an ABI "
              "break; ensure debug info is present (compiled in, or "
              "supplied separately, e.g. via a matching unstripped/"
              "debuginfo build) and re-scan to restore full coverage.",
       description_template="New binary has no DWARF debug info — struct/enum layout comparison was skipped. Ensure debug info is present (compiled in or supplied separately) to enable."),

    # ── DWARF advanced (Sprint 4) — calling-convention/layout signals with
    #    no dedicated detector wired yet (declared for future use; see
    #    ADR's changekind-detector WARN, not an ERROR gate) ─────────────────
    _E("value_abi_trait_changed", _B,
       impact="A type's calling-convention-relevant triviality/copy-"
              "semantics trait changed (the DWARF-derived heuristic for "
              "whether a value type is 'trivial enough' to pass in "
              "registers per the platform ABI, e.g. the Itanium C++ ABI's "
              "non-trivial-for-calls rule). This detector reports this kind "
              "specifically when the register-vs-hidden-pointer return "
              "mechanism did NOT flip (see struct_return_convention_changed "
              "for that case) — the underlying fingerprint still changed, "
              "which can affect how the type participates in parameter "
              "passing or copy/move semantics under the platform ABI's own "
              "rules, so a caller compiled against the old trait should be "
              "treated as at risk.",
       policy_overrides={"plugin_abi": _C}),
    _E("type_visibility_changed", _B,
       impact="A type's visibility attribute changed, affecting whether "
              "its typeinfo/vtable symbols are exported from the shared "
              "library; a consumer relying on RTTI (dynamic_cast, typeid, "
              "exception matching) or virtual dispatch across the library "
              "boundary can fail to find the expected typeinfo/vtable once "
              "the visibility narrows."),
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

    # ── Sprint 2 — gap detectors ──────────────────────────────────────────
    _E("base_class_position_changed", _B,
       impact="Base classes were reordered; this shifts every subobject's "
              "offset within the derived class (the this-pointer "
              "adjustment needed to reach a given base changes), so a "
              "caller compiled against the old layout accesses inherited "
              "members and virtual dispatch through the wrong offset.",
       description_template="Base class order reordered: {name} — this-pointer adjustments changed"),
    _E("base_class_virtual_changed", _B,
       impact="A base class's virtual-inheritance mode changed (virtual ↔ "
              "non-virtual); this changes how the base subobject is "
              "located (a fixed offset for non-virtual inheritance vs. a "
              "vtable/vbase-offset lookup for virtual inheritance), so old "
              "code compiled against the previous scheme locates the base "
              "subobject incorrectly.",
       description_template="Base class virtual inheritance changed: {name} — {detail}"),

    # ── Sprint 7 — Source-level breaks ─────────────────────────────────────
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
    _E("param_renamed", _A,
       impact="A parameter's name changed; this has no effect on the "
              "compiled ABI (parameter names aren't part of the mangled "
              "signature or calling convention), but source using named-"
              "argument-style calls, or documentation/IDE tooling relying "
              "on the old name, may be affected.",
       policy_overrides={"sdk_vendor": _C},
       description_template="Parameter renamed: {name} param {detail}: {old} → {new}"),

    # ── Field qualifier changes ────────────────────────────────────────────
    _E("field_became_const", _C,
       impact="A struct/class field gained const; the field's offset and "
              "size are unchanged, but source code that wrote to it "
              "directly (not through a cast) no longer compiles.",
       description_template="Field became const: {name}::{detail}"),
    _E("field_lost_const", _C,
       impact="A field lost const; its offset and size are unchanged, but "
              "code that relied on the compiler enforcing (or optimizing "
              "based on) read-only access to it no longer gets that "
              "guarantee.",
       description_template="Field lost const: {name}::{detail}"),
    _E("field_became_volatile", _C,
       impact="A field gained volatile; its offset and size are unchanged, "
              "but the compiler now treats every access as observable and "
              "suppresses caching/reordering around it — code recompiled "
              "against the new declaration may see more memory traffic "
              "where it previously assumed the compiler could cache a "
              "read.",
       description_template="Field became volatile: {name}::{detail}"),
    _E("field_lost_volatile", _C,
       impact="A field lost volatile; its offset and size are unchanged, "
              "but a recompiled consumer may now have accesses to it "
              "cached or reordered by the compiler — code relying on every "
              "access reaching memory (e.g. memory-mapped hardware state) "
              "can break once recompiled against the new declaration.",
       description_template="Field lost volatile: {name}::{detail}"),
    _E("field_became_mutable", _C,
       impact="A field gained mutable, letting it be modified even "
              "through a const object/method; layout is unchanged, but "
              "this weakens the const-correctness guarantee callers "
              "holding a const reference could previously rely on.",
       description_template="Field became mutable: {name}::{detail}"),
    _E("field_lost_mutable", _C,
       impact="A field lost mutable; it can no longer be modified through "
              "a const object/method — source code doing so via a "
              "const-qualified path no longer compiles.",
       description_template="Field lost mutable: {name}::{detail}"),

    # ── Pointer level changes ──────────────────────────────────────────────
    _E("param_pointer_level_changed", _B,
       impact="A parameter's pointer indirection depth changed (e.g. T* → "
              "T** or the reverse); the value passed for that argument is "
              "interpreted differently by callee and caller, so a caller "
              "compiled against the old signature passes the wrong kind "
              "of value — silent misinterpretation or a crash.",
       description_template="Parameter pointer level changed: {name} param {detail} (depth {old} → {new})"),
    _E("return_pointer_level_changed", _B,
       impact="A function's return type's pointer indirection depth "
              "changed; a caller compiled against the old signature reads "
              "the returned value as the wrong kind of pointer — silent "
              "misinterpretation or a crash.",
       description_template="Return pointer level changed: {name} (depth {old} → {new})"),

    # ── Anonymous struct/union ─────────────────────────────────────────────
    _E("anon_field_changed", _B,
       impact="An anonymous struct/union member changed — either its type "
              "changed at the same offset, or it was removed entirely (this "
              "detector reports both under one kind). When the type "
              "changed: since it has no name to distinguish it, every "
              "named member reached through it may have shifted offset — "
              "an already-compiled consumer (or a mixed build linking old "
              "objects against the new library) still reading/writing at "
              "the old offsets now hits the wrong bytes, while source "
              "recompiled against the new headers picks up the new "
              "offsets and is unaffected. When it was removed: every "
              "named member promoted from it (e.g. `s.member`, reached "
              "directly through the enclosing struct/union) is simply "
              "gone, so source referencing any of them fails to compile "
              "against the new headers too — recompilation does not make "
              "this case safe."),

    # ── ABICC full parity — remaining gaps ─────────────────────────────────
    _E("var_value_changed", _C,
       impact="A global variable's initial/static value changed; this "
              "detector only compares the value itself, not the "
              "variable's type or size, so this fires purely on the "
              "observed value difference. If a companion finding also "
              "reports the type or size changed, treat that as the more "
              "significant signal — otherwise this is a behavior change, "
              "not an ABI break, and old binaries that inlined the old "
              "value via constant propagation keep using it until "
              "recompiled.",
       description_template="Global data value changed: {name} ({old} → {new})"),
    _E("type_kind_changed", _B,
       impact="An aggregate's declared kind changed (e.g. struct/class ↔ "
              "union); a struct/class↔union change reinterprets "
              "overlapping vs. non-overlapping member storage — code "
              "compiled against the old kind reads/writes members at the "
              "wrong effective location.",
       description_template="Aggregate kind changed: {name} ({old} → {new})"),
    _E("source_level_kind_changed", _A,
       impact="An aggregate's declared struct/class kind changed at the "
              "source level; `_diff_type_kind_changes()` always routes any "
              "union-involving transition to the separate "
              "`type_kind_changed` kind instead, so this one is reported "
              "only for struct↔class — which differ solely in default "
              "member access and default inheritance access, never in "
              "storage layout. The binary layout is unaffected; source "
              "relying on the old kind's default access may no longer "
              "compile or may behave differently (e.g. a member that was "
              "implicitly public becoming implicitly private).",
       policy_overrides={"sdk_vendor": _C},
       description_template="Aggregate kind changed: {name} ({old} → {new})"),
    _E("used_reserved_field", _C,
       impact="A previously-reserved/padding field was put into real use; "
              "since the space was already part of the struct's layout, "
              "the struct's overall size is usually unaffected. This "
              "detector only checks the rename's type/offset, not "
              "whether existing callers actually zero-initialize that "
              "space, so the risk runs both directions: a consumer "
              "reading a struct the new library populated may see "
              "meaningful, non-zero data where it expected unused "
              "padding, and — the direction this detector cannot rule "
              "out — an old caller that constructs the struct without "
              "explicitly initializing the (formerly reserved) field and "
              "passes it to the new library can hand the new callee "
              "indeterminate bytes that it now interprets as real data.",
       description_template="Reserved field put into use: {name}::{old} → {new}"),
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
    _E("constant_changed", _A,
       impact="A preprocessor constant's value changed; any consumer that "
              "was compiled against the old value has that value baked in "
              "at compile time (macros are substituted before compilation, "
              "not looked up at link/load time), so it keeps behaving as "
              "if the constant were still the old value until it is "
              "recompiled.",
       description_template="Preprocessor constant value changed: {name} ({old} → {new})"),
    _E("constant_added", _C, is_addition=True,
       impact="A new preprocessor constant appeared in the public "
              "headers; an already-compiled binary is unaffected (macros "
              "are purely a compile-time textual substitution, absent from "
              "the ABI). Source is not unconditionally safe, though: this "
              "detector checks only that the constant is new, not whether "
              "its name collides with a consumer's own identifier — a "
              "macro sharing the name of a consumer's variable, function, "
              "or parameter (e.g. a new `#define min ...` ahead of a "
              "consumer's own `min(...)`) gets textually substituted into "
              "that consumer's source and can break a previously-valid "
              "build.",
       description_template="New preprocessor constant: {name}"),
    _E("constant_removed", _A,
       impact="A preprocessor constant was removed from the public "
              "headers; source code referencing it by name fails to "
              "compile against the new headers, though an already-"
              "compiled binary is unaffected (the old value was already "
              "substituted in at its own compile time).",
       description_template="Preprocessor constant removed: {name}"),
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

    # ── Inline attribute changes ───────────────────────────────────────────
    _E("func_became_inline", _A,
       impact="A function gained the inline attribute; source recompiled "
              "against the new header keeps working (each translation "
              "unit gets its own copy), but an already-linked consumer's "
              "outcome depends on what the new library actually ships: "
              "the exported symbol commonly disappears once nothing forces "
              "an out-of-line definition, in which case a caller resolving "
              "it at link/load time gets an undefined-symbol error, while "
              "a symbol kept exported (e.g. still ODR-used elsewhere in "
              "the library) leaves already-linked callers unaffected."),
    _E("func_lost_inline", _C,
       impact="A function lost its explicit inline attribute. A non-static "
              "C++ function already has external linkage regardless of "
              "inline — the attribute only permits the compiler to fold "
              "identical out-of-line definitions emitted by multiple "
              "translation units into one, so losing it doesn't itself "
              "create or guarantee a new export (this detector doesn't "
              "verify the new binary's export table). The function's own "
              "signature and calling convention are unchanged either way, "
              "so this is low-risk for already-linked consumers.",
       description_template="Function lost inline attribute: {name}"),

    # ── PR #89: ELF fallback ──────────────────────────────────────────────
    _E("func_deleted_elf_fallback", _B,
       impact="The exported symbol vanished from the dynamic symbol table "
              "with no explicit `= delete`/removal marker in the header "
              "the diff could otherwise attribute it to; an already-"
              "linked consumer calling it fails to resolve the symbol at "
              "load time.",
       description_template="Symbol disappeared from ELF .dynsym without explicit deletion marker: {name} — was exported in old library, absent in new library's dynamic symbol table while header still declares it"),

    # ── Template inner-type analysis ────────────────────────────────────────
    _E("template_param_type_changed", _B,
       impact="A template's own parameter's inner type changed (e.g. a "
              "member of the type used to instantiate the template); this "
              "changes the instantiation's layout and interface, so a "
              "caller/consumer using the old instantiation's ABI is no "
              "longer compatible with the new one.",
       description_template="Template parameter inner type changed: {name} param {detail} ({old} → {new})"),
    _E("template_return_type_changed", _B,
       impact="A template's own return type's inner type argument "
              "changed; this changes what the template instantiation "
              "returns and how, so a caller compiled against the old "
              "return type reads the result incorrectly.",
       description_template="Template return type inner argument changed: {name} ({old} → {new})"),
]
