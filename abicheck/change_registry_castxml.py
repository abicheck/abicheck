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

"""CastXML schema-completeness ChangeKind registry entries.

Split out of ``change_registry.py`` to keep that module under the
AI-readiness 2000-line hard cap (same reason ``change_registry_coverage.py``
exists). These entries are spliced into the single ``REGISTRY`` at import
time — declaring a kind here is exactly equivalent to declaring it in
``change_registry.py``.

Covers facts CastXML's XML schema already exposes but the parser previously
discarded: default member initializers, `abstract` records, `enum class`
scoping, the explicit `override` specifier, and `[[deprecated]]` on each
surface kind. See ``abicheck/dumper_castxml.py`` for the parser population
and ``abicheck/diff_types.py``/``abicheck/diff_symbols.py`` for the detectors
that emit these kinds.
"""

from __future__ import annotations

from .change_registry_types import ChangeKindMeta, Verdict

_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta

CASTXML_EXTENSION_ENTRIES: list[ChangeKindMeta] = [
    # ── Default member initializers (header/castxml only) ───────────────────
    _E("field_default_initializer_removed", _R,
       impact="A field's default member initializer was removed. Code relying "
              "on implicit initialization (aggregate init, a defaulted "
              "constructor) now leaves the member with indeterminate value "
              "instead of the old default — a silent correctness risk, not a "
              "compile break.",
       description_template="Field lost its default initializer: {name}::{detail}"),
    _E("field_default_initializer_changed", _C,
       impact="A field's default member initializer value changed. Existing "
              "source still compiles; objects default-constructed against the "
              "new header silently pick up the new value.",
       description_template="Field default initializer changed: {name}::{detail} ({old} → {new})"),

    # ── `abstract` (>=1 pure virtual) transitions (header/castxml only) ──────
    _E("type_became_abstract", _A,
       impact="A class/struct gained a pure virtual function (directly or via "
              "an inherited one newly left unimplemented), making it abstract. "
              "Source that directly instantiates the type (`Foo obj;`, "
              "`new Foo()`) no longer compiles. Not recorded in DWARF/the "
              "binary, so detected only in header (castxml) mode.",
       description_template="Class became abstract: {name} — direct instantiation no longer compiles"),
    _E("type_lost_abstract", _C,
       impact="A class/struct is no longer abstract (every pure virtual now "
              "has an implementation). Previously-valid source (which could "
              "never have instantiated it directly) is unaffected; the type "
              "is simply newly instantiable — purely additive.",
       description_template="Class lost abstract status: {name}"),

    # ── `enum class`/`enum struct` scoping transitions (header/castxml only) ─
    _E("enum_became_scoped", _A,
       impact="A plain enum became a scoped `enum class`/`enum struct`. "
              "Unqualified enumerator lookup (`Red` instead of `Color::Red`) "
              "and implicit conversion to/from the underlying integer type "
              "both stop compiling — a source break, not a binary one (the "
              "underlying representation is unchanged).",
       description_template="Enum became scoped: {name} — unqualified enumerator lookup and implicit int conversion no longer compile"),
    _E("enum_lost_scoped", _R,
       impact="A scoped `enum class`/`enum struct` became a plain enum. "
              "Existing qualified-name source (`Color::Red`) still compiles, "
              "but implicit conversion to/from the underlying integer type "
              "silently reappears — code that relied on the scoped enum's "
              "type safety to reject stray integer comparisons/arithmetic no "
              "longer gets that protection, a silent behavior change to "
              "review rather than a hard break.",
       description_template="Enum lost scoped status: {name} — implicit int conversion silently reappears"),

    # ── Explicit C++11 `override` specifier (header/castxml only) ────────────
    # Distinct from FUNC_VIRTUAL_REMOVED/vtable-slot kinds, which already
    # catch an actual dispatch break; this is the source-level
    # self-documentation marker alone.
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

    # ── `[[deprecated]]`/`[[deprecated("msg")]]` transitions (header/castxml
    #    only). One pair per surface kind, matching the existing per-entity-
    #    kind convention (is_final on types, is_explicit on functions, ...).
    #    All COMPATIBLE (quality): deprecation is advance notice, not a break.
    _E("func_deprecated_added", _C,
       description_template="Function marked deprecated: {name} ({detail})"),
    _E("func_deprecated_removed", _C,
       description_template="Function no longer marked deprecated: {name}"),
    _E("var_deprecated_added", _C,
       description_template="Variable marked deprecated: {name} ({detail})"),
    _E("var_deprecated_removed", _C,
       description_template="Variable no longer marked deprecated: {name}"),
    _E("type_deprecated_added", _C,
       description_template="Type marked deprecated: {name} ({detail})"),
    _E("type_deprecated_removed", _C,
       description_template="Type no longer marked deprecated: {name}"),
    _E("enum_deprecated_added", _C,
       description_template="Enum marked deprecated: {name} ({detail})"),
    _E("enum_deprecated_removed", _C,
       description_template="Enum no longer marked deprecated: {name}"),
    _E("field_deprecated_added", _C,
       description_template="Field marked deprecated: {name}::{detail} ({new})"),
    _E("field_deprecated_removed", _C,
       description_template="Field no longer marked deprecated: {name}::{detail}"),
]
