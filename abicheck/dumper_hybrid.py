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

"""Hybrid castxml+clang snapshot merge (G28 Phase 3, ``--ast-frontend hybrid``).

``dumper.py`` runs BOTH L2 header-AST backends over the same headers and hands
their two independent :class:`~abicheck.model.AbiSnapshot`\\ s to
:func:`merge_snapshots`, which combines them into one snapshot:

- **Ctor/dtor identity reconciliation** (the concrete motivating bug from the
  G28 plan): castxml sometimes cannot recover a real mangled name for a
  constructor/destructor and synthesizes a placeholder snapshot key instead
  (``dumper_castxml.SYNTHETIC_CTOR_KEY_PREFIX`` / the ``"~ClassName"`` dtor
  form). That placeholder shares no identity with the SAME entity's real
  Itanium-mangled key on the clang side, so comparing a castxml-parsed
  snapshot against a clang-parsed snapshot of unchanged source reports a
  false ``FUNC_REMOVED``+``FUNC_ADDED`` pair for every such constructor/
  destructor (see
  ``tests/test_castxml_clang_parity_gate.py::TestCrossProducerUnmangledIdentityKnownLimitation``).
  This module fixes it by matching a synthetic key against a real clang
  mangled name via structural equivalence (same qualified enclosing class,
  compatible cv-normalized parameter signature for a constructor, same
  access) and rewriting the merged entry's key to the real mangled name.
- **Per-fact backfill**: castxml-only facts (``deprecated``/``is_override``
  on functions, ``deprecated`` on variables, ``is_abstract``/``deprecated``
  on types, ``default``/``deprecated`` on fields, ``is_scoped``/
  ``deprecated`` on enums) are taken from castxml when present, backfilled
  from clang only when castxml's own value is ``None`` — forward-looking
  scaffolding for once the clang backend gains any of these independently;
  a no-op today, since ``dumper_clang.py`` doesn't populate any of them yet.
  Every such fact records its source in the returned snapshot's
  ``fact_provenance`` map (see ``abicheck/fact_provenance.py``), so
  detectors can tell a castxml-backed fact apart from an unbacked one on a
  per-declaration basis instead of trusting a whole-snapshot producer tag.

**Layout facts**: castxml remains the PRIMARY layout source — its own real
size/alignment/offset/vtable data is never overridden. When the optional G28
Phase 4 companion tool (``ABICHECK_CLANG_LAYOUT_TOOL``) has already enriched
the clang sub-dump before this merge, its facts backfill any of the same
fields castxml itself never computes at all (``data_size_bits``,
``is_standard_layout``, ``is_trivially_copyable``) or left empty (an opaque/
incomplete castxml record) — see :func:`_merge_record_type`/
:func:`_merge_field`. Without the layout tool enabled, this is a no-op:
``dumper_clang.py``'s plain ``-ast-dump=json`` parse leaves every one of
these fields empty too, so there is nothing to backfill from.

Everything not explicitly merged below (typedefs, constants, ELF/PE/Mach-O
metadata, DWARF metadata, ...) is taken verbatim from the castxml snapshot,
which is used as the base via ``dataclasses.replace``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .diff_cxx_rules import _skip_template_args, itanium_scope_components
from .dumper_castxml import (
    SYNTHETIC_CTOR_KEY_PREFIX,
    is_synthetic_ctor_key,
    is_synthetic_dtor_key,
)
from .fact_provenance import (
    enum_fact_key,
    field_fact_key,
    func_fact_key,
    type_fact_key,
    var_fact_key,
)
from .model import AbiSnapshot, EnumType, Function, RecordType, TypeField, Variable
from .name_classification import canonicalize_type_name

_CTOR_MARKER = "{ctor}"
_DTOR_MARKER = "{dtor}"


def _backfill_fact(
    own_value: Any, clang_value: Any, key: str, provenance: dict[str, str]
) -> Any:
    """Return the merged value for one castxml-only fact and record its
    provenance under *key*.

    "Prefer castxml, backfill from clang only when castxml's own value is
    null" (G28 Phase 3 design). Provenance is recorded as ``"castxml"`` even
    when *own_value* is ``None`` and no clang value was available to
    backfill from — the entity itself IS castxml-sourced; a genuinely
    "not deprecated"/"not overridden"/etc. `None` is not the same as "this
    entity was never seen by castxml at all" (the latter simply never calls
    this helper — see :func:`merge_snapshots`'s clang-only-entity handling).
    """
    if own_value is None and clang_value is not None:
        provenance[key] = "clang"
        return clang_value
    provenance[key] = "castxml"
    return own_value


def _ctor_dtor_scope(mangled: str) -> tuple[str, str] | None:
    """``(marker, qualified_scope)`` for a REAL Itanium-mangled ctor/dtor, or
    None if *mangled* isn't one (parsed structurally — see
    ``diff_cxx_rules.itanium_scope_components``, which returns the ctor/dtor
    marker as the last scope component)."""
    comps = itanium_scope_components(mangled)
    if not comps or comps[-1] not in (_CTOR_MARKER, _DTOR_MARKER):
        return None
    return comps[-1], "::".join(comps[:-1])


def _split_top_level_commas(s: str) -> list[str]:
    """Split *s* on commas at bracket depth 0 only.

    A castxml synthetic ctor key joins its parameter types with ``,``
    (``dumper_castxml._function_mangled_name``'s ``",".join(ctor_identity_types)``)
    with no escaping, so a single parameter type that itself contains a
    comma (``std::pair<int, int>``, any other multi-argument template) must
    not be split into two — that would understate the constructor's real
    arity and permanently block reconciliation against the clang side,
    reintroducing the false ``FUNC_REMOVED``/``FUNC_ADDED`` pair for every
    such constructor (Codex review). Mirrors the same depth-tracking
    convention already used in ``name_classification._has_top_level_ptr_or_ref``.
    """
    if not s:
        return []
    parts = []
    depth = 0
    start = 0
    for i, ch in enumerate(s):
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(s[start:i])
            start = i + 1
    parts.append(s[start:])
    return parts


def _macho_normalize_mangled(mangled: str) -> str:
    """Strip the single Darwin linker-symbol leading underscore clang's
    Mach-O ``mangledName`` carries, matching castxml's prefix-free
    convention on the same platform.

    Darwin prepends exactly one underscore to every global symbol's
    compiler-computed name (a C function ``foo`` -> ``_foo``; a C++ Itanium
    name, which itself starts with ``_Z``, -> ``__ZN...``). clang's
    ``-ast-dump=json`` ``mangledName`` field reports the real, platform-
    accurate linker symbol (WITH that extra underscore), while castxml's own
    ``mangled`` XML attribute is the "pure" Itanium name (WITHOUT it) — see
    ``dumper_clang._ClangAstParser._visibility``'s docstring, which already
    handles this same mismatch for export-table matching via
    ``_symbol_candidates``. Without normalizing it here too, EVERY Mach-O
    C++ function/variable's clang-side mangled key differs from its
    castxml-side key, so the hybrid merge's ``cf.mangled not in
    merged_mangled`` dedup check is always true — treating every function
    castxml already emitted as "clang-only" and duplicating the entire
    function list (Codex review).
    """
    return mangled[1:] if mangled.startswith("_") else mangled


def _strip_itanium_template_suffix(component: str) -> str:
    """Strip a trailing Itanium ``<template-args>`` (``I...E``) block from a
    single mangled scope component, recovering the base template name
    (``"Widget"`` from ``"WidgetIiE"``).

    Tries EVERY ``"I"`` occurrence in turn, not just the first: a base name
    that itself contains an uppercase ``"I"`` (``"Image"``, ``"Iterator"``,
    ``"MultiIndex"``) has its own ``"I"`` appear before the real
    template-argument-opening one, e.g. ``"ImageIiE"``'s first ``"I"`` is
    from ``"Image"`` itself. Starting ``_skip_template_args`` there consumes
    the wrong span and never reaches the end of the string, so the naive
    first-match returned the component UNCHANGED instead of stripping
    anything (Codex review) — silently leaving it un-normalized and
    mismatched against castxml's ``"Image"``. The correct template-argument
    boundary is the first ``"I"`` whose matching skip exhausts the ENTIRE
    remaining string (nothing follows a component's template-args block).
    """
    start = 0
    while True:
        idx = component.find("I", start)
        if idx == -1:
            return component
        end = _skip_template_args(component, idx)
        if end == len(component):
            return component[:idx]
        start = idx + 1


def _split_top_level_scope(scope: str) -> list[str]:
    """Split *scope* on ``::`` at bracket depth 0 only.

    A source-form scope for a nested class inside a template
    (``"ns::Outer<int>::Inner"``) must split into ``["ns", "Outer<int>",
    "Inner"]``, not further — but a template argument can itself contain a
    namespace-qualified type (``"ns::Widget<std::vector<int>>::Inner"``),
    whose ``std::vector`` would wrongly split the scope in two if ``::``
    were matched unconditionally. Mirrors the bracket-depth-aware convention
    already used by ``_split_top_level_commas``.
    """
    parts = []
    depth = 0
    start = 0
    i = 0
    n = len(scope)
    while i < n:
        ch = scope[i]
        if ch in "<([":
            depth += 1
            i += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
            i += 1
        elif depth == 0 and scope[i : i + 2] == "::":
            parts.append(scope[start:i])
            start = i + 2
            i += 2
        else:
            i += 1
    parts.append(scope[start:])
    return parts


def _normalize_scope_for_matching(scope: str) -> str:
    """Reduce a qualified ctor/dtor scope to a template-argument-free form
    comparable across both producers.

    castxml's own qualified-name resolution spells a template's scope in
    SOURCE form (``"ns::Widget<int>"``); the SAME class's scope from a real
    Itanium-mangled ctor/dtor (``itanium_scope_components``) is spelled
    ``"ns::WidgetIiE"`` — the raw mangled template-argument encoding. These
    are two different alphabets for the identical class, so an exact string
    comparison never matched any templated class's ctor/dtor even when
    nothing changed (Codex review). Stripping each side's own
    template-argument spelling down to the bare base name here makes them
    comparable; the constructor's own (already cv-normalized) parameter
    signature — not the scope — is what disambiguates distinct instantiations
    that happen to share a base template name (e.g. ``Box<int>`` vs.
    ``Box<double>``, whose constructors almost always differ in exactly the
    template-dependent parameter that this scope normalization discards).

    Every scope component is normalized, not just the innermost one: a
    nested class inside a template (``"ns::Outer<int>::Inner"`` vs. the
    mangled ``"ns::OuterIiE::Inner"``) has its template argument on an
    ENCLOSING component, which a last-component-only normalization would
    leave untouched on the castxml side while the clang side always encodes
    every enclosing level — permanently blocking reconciliation for any
    nested class inside a template (Codex review).
    """
    components = _split_top_level_scope(scope)
    normalized = [
        c.split("<", 1)[0] if "<" in c else _strip_itanium_template_suffix(c)
        for c in components
    ]
    return "::".join(normalized)


def _synthetic_ctor_dtor_scope(key: str) -> tuple[str, str, str] | None:
    """``(marker, qualified_scope, param_sig)`` parsed back out of a castxml
    synthetic ctor/dtor key (the exact inverse of
    ``dumper_castxml._CastxmlParser._function_mangled_name``'s synthesis).
    ``param_sig`` is ``""`` for a destructor (never overloaded)."""
    if is_synthetic_ctor_key(key):
        body = key[len(SYNTHETIC_CTOR_KEY_PREFIX) :]
        if "(" not in body or not body.endswith(")"):
            return None
        paren = body.index("(")
        return _CTOR_MARKER, body[:paren], body[paren + 1 : -1]
    if is_synthetic_dtor_key(key):
        return _DTOR_MARKER, key[1:], ""
    return None


def _match_synthetic_ctor_dtor(
    castxml_f: Function,
    clang_ctor_dtor: dict[tuple[str, str], list[Function]],
) -> Function | None:
    """Find the real-mangled clang ``Function`` a castxml synthetic ctor/dtor
    key structurally identifies, or None if no unambiguous match exists.

    A destructor needs only (marker, scope): a class has at most one, so any
    single candidate under that key IS the match. A constructor also
    requires a cv-normalized parameter-type match (there may be several
    overloads sharing the same scope) — matching the plan's explicit caution
    against "a false match between two coincidentally-same-signature but
    genuinely different entities": ambiguity (zero or multiple candidates
    surviving all checks) yields None rather than guessing.

    **Known residual limitation** (Codex review): the scope key is
    normalized template-argument-free (see ``_normalize_scope_for_matching``),
    so TWO OR MORE distinct instantiations of the same template that both
    declare a default (no-parameter) constructor, or both have a destructor,
    collide under the identical normalized ``(marker, scope)`` key with
    nothing left to disambiguate them (a destructor never takes parameters;
    a default constructor's own signature is empty on both sides). This
    correctly yields ambiguous → no match, same as any other unmodeled shape
    here — it does not produce a wrong match — but it does mean such a
    ctor/dtor stays unreconciled (the castxml synthetic key and the clang
    real name both survive as a false remove+add pair) for that narrow case.
    Resolving it would require decoding the ACTUAL Itanium template-argument
    encoding (or shelling out to a demangler) to recover each candidate's own
    instantiation identity — deliberately out of scope here to avoid a new
    dependency or a heuristic that could produce a wrong match, which would
    be worse than today's safe non-match.
    """
    parsed = _synthetic_ctor_dtor_scope(castxml_f.mangled)
    if parsed is None:
        return None
    marker, scope, param_sig = parsed
    candidates = clang_ctor_dtor.get(
        (marker, _normalize_scope_for_matching(scope)), []
    )
    if marker == _DTOR_MARKER:
        if len(candidates) == 1 and candidates[0].access == castxml_f.access:
            return candidates[0]
        return None
    # Constructor: narrow by cv-normalized signature, same as the synthetic
    # key's own identity (dumper_castxml._ctor_param_identity_type already
    # strips a top-level cv qualifier the same way real mangling would).
    wanted_sig = tuple(
        canonicalize_type_name(t) for t in _split_top_level_commas(param_sig)
    )
    matches = [
        c
        for c in candidates
        if c.access == castxml_f.access
        and tuple(canonicalize_type_name(p.type) for p in c.params) == wanted_sig
    ]
    return matches[0] if len(matches) == 1 else None


def _backfill_function_facts(
    f: Function, clang_f: Function | None, provenance: dict[str, str]
) -> Function:
    updates: dict[str, Any] = {}
    for attr in ("deprecated", "is_override"):
        key = func_fact_key(f.mangled, attr)
        value = _backfill_fact(
            getattr(f, attr), getattr(clang_f, attr, None), key, provenance
        )
        if value != getattr(f, attr):
            updates[attr] = value
    return replace(f, **updates) if updates else f


def _merge_functions(
    castxml_funcs: list[Function],
    clang_funcs: list[Function],
    provenance: dict[str, str],
) -> list[Function]:
    clang_ctor_dtor: dict[tuple[str, str], list[Function]] = {}
    for cf in clang_funcs:
        scope = _ctor_dtor_scope(cf.mangled)
        if scope is not None:
            marker, scope_str = scope
            key = (marker, _normalize_scope_for_matching(scope_str))
            clang_ctor_dtor.setdefault(key, []).append(cf)

    merged: list[Function] = []
    for f in castxml_funcs:
        if is_synthetic_ctor_key(f.mangled) or is_synthetic_dtor_key(f.mangled):
            match = _match_synthetic_ctor_dtor(f, clang_ctor_dtor)
            if match is not None:
                f = replace(f, mangled=match.mangled)
        merged.append(f)

    clang_by_mangled = {cf.mangled: cf for cf in clang_funcs}
    merged = [
        _backfill_function_facts(f, clang_by_mangled.get(f.mangled), provenance)
        for f in merged
    ]
    # Every function actually present in castxml_funcs is castxml-backed for
    # this fact — even one whose synthetic ctor/dtor key got rewritten to a
    # clang mangled name above, since the *declaration* itself is still
    # castxml's. Both backends populate Param.default now, but their VALUE
    # representations aren't cross-comparable (castxml keeps the real source
    # expression; dumper_clang.py falls back to a structural fingerprint/
    # placeholder for anything beyond a bare literal), so this fact still
    # needs a producer tag per function — _diff_param_defaults uses it to
    # require the SAME producer on both sides of a pair, not specifically
    # "castxml" (Codex review: a clang-only function is still comparable
    # against ANOTHER clang-only declaration of itself, exactly like a plain
    # ``--ast-frontend clang`` run already does today).
    for f in merged:
        provenance[func_fact_key(f.mangled, "param_defaults")] = "castxml"

    merged_mangled = {f.mangled for f in merged}
    clang_only = [cf for cf in clang_funcs if cf.mangled not in merged_mangled]
    for cf in clang_only:
        provenance[func_fact_key(cf.mangled, "param_defaults")] = "clang"
    merged.extend(clang_only)
    return merged


def _merge_variable(
    v: Variable, clang_v: Variable | None, provenance: dict[str, str]
) -> Variable:
    key = var_fact_key(v.mangled, "deprecated")
    value = _backfill_fact(
        v.deprecated, clang_v.deprecated if clang_v else None, key, provenance
    )
    return replace(v, deprecated=value) if value != v.deprecated else v


#: G28 Phase 4 layout facts castxml either never populates at all
#: (data_size_bits/is_standard_layout/is_trivially_copyable) or leaves empty
#: for an opaque/incomplete record (size_bits/alignment_bits/
#: vptr_offset_bits) -- backfilled from an already-enriched clang_t below
#: only when castxml's own value is still None (Codex review).
_LAYOUT_SCALAR_ATTRS = (
    "size_bits",
    "alignment_bits",
    "data_size_bits",
    "is_standard_layout",
    "is_trivially_copyable",
    "vptr_offset_bits",
)


def _merge_field(
    type_name: str,
    f: TypeField,
    clang_f: TypeField | None,
    provenance: dict[str, str],
) -> TypeField:
    updates: dict[str, Any] = {}
    for attr in ("default", "deprecated"):
        key = field_fact_key(type_name, f.name, attr)
        value = _backfill_fact(
            getattr(f, attr), getattr(clang_f, attr, None), key, provenance
        )
        if value != getattr(f, attr):
            updates[attr] = value
    # G28 Phase 4: same layout backfill as _merge_record_type, for the
    # per-field offset the optional companion tool computes.
    if clang_f is not None and f.offset_bits is None and clang_f.offset_bits is not None:
        updates["offset_bits"] = clang_f.offset_bits
    return replace(f, **updates) if updates else f


def _merge_record_type(
    t: RecordType, clang_t: RecordType | None, provenance: dict[str, str]
) -> RecordType:
    updates: dict[str, Any] = {}
    for attr in ("is_abstract", "deprecated"):
        key = type_fact_key(t.name, attr)
        value = _backfill_fact(
            getattr(t, attr), getattr(clang_t, attr, None), key, provenance
        )
        if value != getattr(t, attr):
            updates[attr] = value

    # G28 Phase 4 (optional ABICHECK_CLANG_LAYOUT_TOOL): clang_t may carry
    # REAL ASTRecordLayout facts the companion tool already backfilled onto
    # clang_snap BEFORE this merge (attach_clang_layout runs on clang_snap's
    # own recursive dump). Without this, a type present on BOTH backends --
    # the common case -- lost every one of these facts in a hybrid merge
    # even with the layout tool enabled, while a clang-ONLY type (appended
    # verbatim below) kept them (Codex review). Never overrides an existing
    # castxml value -- castxml's own real layout, when present, always wins.
    if clang_t is not None:
        for attr in _LAYOUT_SCALAR_ATTRS:
            if getattr(t, attr) is None and getattr(clang_t, attr) is not None:
                updates[attr] = getattr(clang_t, attr)
        if not t.base_offsets and clang_t.base_offsets:
            updates["base_offsets"] = clang_t.base_offsets

    clang_fields_by_name = {cf.name: cf for cf in clang_t.fields} if clang_t else {}
    merged_fields = [
        _merge_field(t.name, f, clang_fields_by_name.get(f.name), provenance)
        for f in t.fields
    ]
    if merged_fields != t.fields:
        updates["fields"] = merged_fields

    return replace(t, **updates) if updates else t


def _merge_enum_type(
    e: EnumType, clang_e: EnumType | None, provenance: dict[str, str]
) -> EnumType:
    updates: dict[str, Any] = {}
    for attr in ("is_scoped", "deprecated"):
        key = enum_fact_key(e.name, attr)
        value = _backfill_fact(
            getattr(e, attr), getattr(clang_e, attr, None), key, provenance
        )
        if value != getattr(e, attr):
            updates[attr] = value
    return replace(e, **updates) if updates else e


def merge_snapshots(castxml_snap: AbiSnapshot, clang_snap: AbiSnapshot) -> AbiSnapshot:
    """Merge a castxml-produced and a clang-produced snapshot of the SAME
    headers into one hybrid :class:`AbiSnapshot`.

    castxml remains the base (layout facts, ELF/PE/Mach-O metadata, typedefs,
    constants, and everything not explicitly merged here all come from it
    verbatim) — only the facts documented in this module's docstring are
    actually reconciled/backfilled. The result's ``ast_producer`` is
    ``"hybrid"`` and its ``fact_provenance`` records, per declaration, which
    backend's value was used for each of those facts.

    If EITHER side never got confirmed header-AST evidence — no headers were
    supplied, the dump ran ``dwarf_only``/``symbols_only``, or one backend
    degraded to a non-header fallback (e.g. the PE/Mach-O header-scoped path
    falling back to export-table mode when clang is unavailable or nothing
    matched) — returns *castxml_snap* unchanged rather than unioning the
    other side's declarations into a falsely-upgraded, confirmed
    header-aware ``ast_producer="hybrid"`` result. A one-sided fallback is
    not just missing data to merge: unioning a non-header snapshot's much
    broader export-table-derived functions/types into a header-scoped result
    would also pull that noise back in, and header-tier detectors (param
    defaults, constants, param renames) would misread the merge's forced
    header-aware provenance when compared against a genuinely header-aware
    snapshot (Codex review, x2).
    """
    if not (castxml_snap.from_headers and clang_snap.from_headers):
        return castxml_snap

    provenance: dict[str, str] = {}

    # Mach-O: normalize clang's mangled names to castxml's prefix-free
    # convention BEFORE any mangled-keyed matching/dedup below (functions AND
    # variables) -- see _macho_normalize_mangled's docstring. Type/enum
    # merges key on the source-level NAME, not a mangled linker symbol, so
    # they carry no such platform-specific decoration and need no change.
    clang_functions = clang_snap.functions
    clang_variables = clang_snap.variables
    if castxml_snap.platform == "macho":
        clang_functions = [
            replace(cf, mangled=_macho_normalize_mangled(cf.mangled))
            for cf in clang_functions
        ]
        clang_variables = [
            replace(cv, mangled=_macho_normalize_mangled(cv.mangled))
            for cv in clang_variables
        ]

    clang_types_by_name = {t.name: t for t in clang_snap.types}
    clang_enums_by_name = {e.name: e for e in clang_snap.enums}
    clang_vars_by_mangled = {v.mangled: v for v in clang_variables}

    merged_functions = _merge_functions(
        castxml_snap.functions, clang_functions, provenance
    )

    merged_types = [
        _merge_record_type(t, clang_types_by_name.get(t.name), provenance)
        for t in castxml_snap.types
    ]
    castxml_type_names = {t.name for t in castxml_snap.types}
    merged_types.extend(t for t in clang_snap.types if t.name not in castxml_type_names)

    merged_enums = [
        _merge_enum_type(e, clang_enums_by_name.get(e.name), provenance)
        for e in castxml_snap.enums
    ]
    castxml_enum_names = {e.name for e in castxml_snap.enums}
    merged_enums.extend(e for e in clang_snap.enums if e.name not in castxml_enum_names)

    merged_variables = [
        _merge_variable(v, clang_vars_by_mangled.get(v.mangled), provenance)
        for v in castxml_snap.variables
    ]
    castxml_var_mangled = {v.mangled for v in castxml_snap.variables}
    merged_variables.extend(
        v for v in clang_variables if v.mangled not in castxml_var_mangled
    )

    return replace(
        castxml_snap,
        functions=merged_functions,
        variables=merged_variables,
        types=merged_types,
        enums=merged_enums,
        ast_producer="hybrid",
        ast_toolchain={
            **{f"castxml_{key}": value for key, value in castxml_snap.ast_toolchain.items()},
            **{f"clang_{key}": value for key, value in clang_snap.ast_toolchain.items()},
        },
        ast_fallback_reason=None,
        fact_provenance=provenance,
        # from_headers/from_headers_inferred are inherited from castxml_snap
        # as-is via replace() (both already True/False here — the early
        # return above handles the case where they aren't).
        # Invalidate the lazy lookup caches (dataclasses.replace() otherwise
        # carries the OLD castxml-only indexes forward unchanged, since these
        # are ordinary fields with defaults, not something replace() knows to
        # reset just because functions/variables/types changed).
        _func_by_mangled=None,
        _var_by_mangled=None,
        _type_by_name=None,
    )


def run_hybrid_dump(
    dump_fn: Callable[..., AbiSnapshot],
    so_path: Path,
    headers: list[Path],
    **kwargs: Any,
) -> AbiSnapshot:
    """Run *dump_fn* (``dumper.dump``) once per real backend and merge.

    Takes *dump_fn* as a parameter, rather than importing ``dumper.dump``
    directly, so this module never depends on ``dumper.py`` (which already
    depends on this one) — avoiding an import cycle without needing a
    deferred/local import on either side. Every keyword argument is forwarded
    to both sub-dumps unchanged except ``header_backend``, which this
    function sets explicitly on each call; reuses every format handler,
    ELF/PE/Mach-O metadata attachment, and provenance tagging in *dump_fn*
    completely unchanged for both sub-dumps — only the merge step
    (:func:`merge_snapshots`) is new.
    """
    castxml_snap = dump_fn(so_path, headers, header_backend="castxml", **kwargs)
    clang_snap = dump_fn(so_path, headers, header_backend="clang", **kwargs)
    return merge_snapshots(castxml_snap, clang_snap)
