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
- **Per-fact backfill**: facts that were originally castxml-only
  (``deprecated``/``is_override`` on functions, ``deprecated`` on
  variables, ``is_abstract``/``deprecated`` on types, ``default``/
  ``deprecated`` on fields, ``is_scoped``/``deprecated`` on enums) are taken
  from castxml when present, backfilled from clang only when castxml's own
  value is ``None``. G31 Phase C closed the gap this backfill originally
  anticipated for three of these facts specifically — ``deprecated`` (every
  surface kind), ``is_scoped``, and field ``default`` — by wiring real
  extraction into ``dumper_clang.py`` too, so this backfill is genuinely
  live for those three now, not forward-looking scaffolding;
  ``is_override``/``is_abstract`` remain castxml-only, so the backfill is
  still a no-op for those two specifically. Note that field ``default`` is
  cross-*producer* without being cross-*comparable*: castxml keeps the
  verbatim source expression where clang emits a literal or a structural
  fingerprint, so its detector gates on the two sides sharing one producer
  rather than on both merely having a known one. Every such fact records
  its source in the returned snapshot's ``fact_provenance`` map (see
  ``abicheck/fact_provenance.py``), so detectors can tell which backend
  backs a fact on a per-declaration basis.
- **Declaration-existence provenance** (G31 Phase C, hybrid-graph
  provenance-tagging): every merged function/variable also gets a
  ``"visibility"``-named ``fact_provenance`` entry recording which backend
  contributed the *declaration itself* (``"castxml"`` for a castxml-primary
  entry, ``"clang"`` for a clang-only-appended one) — not a per-field value
  merge like every other entry above, since this snapshot's own
  ``origin``/``ScopeOrigin`` classification (``provenance.apply_provenance()``)
  runs identically over both kinds of entry afterwards. The one consumer is
  :func:`abicheck.buildsource.header_graph.build_header_only_graph`, which
  reads it back to stamp each L2 graph node's own
  ``attrs["visibility_provenance"]`` — see that function's docstring for why
  the graph needed this and not the flat snapshot's other detectors.

**Layout facts**: castxml remains the PRIMARY layout source — its own real
size/alignment/offset/vtable data is never overridden. When the optional G28
Phase 4 companion tool (``ABICHECK_CLANG_LAYOUT_TOOL``) has already enriched
the clang sub-dump before this merge, its facts backfill ``data_size_bits``
and the offset/vptr facts castxml itself never computes at all, or left
empty for an opaque/incomplete castxml record — see
:func:`_merge_record_type`/:func:`_merge_field`. ``is_standard_layout``/
``is_trivially_copyable`` are the one exception to "without the layout tool
enabled, this is a no-op": G31 Phase C wired real extraction of both
directly into ``dumper_clang.py``'s plain ``-ast-dump=json`` parse (no
companion tool needed, since these are semantic type traits clang's AST
computes independent of any layout pass — see ``_clang_record_type_traits``'s
own docstring), so this backfill genuinely fires for those two even without
``ABICHECK_CLANG_LAYOUT_TOOL`` set. ``data_size_bits``/``size_bits``/
``alignment_bits``/``vptr_offset_bits`` still require the companion tool;
``dumper_clang.py``'s plain parse leaves those empty either way.

**``is_template_pattern``/``has_anonymous_aggregate_fields``** (G31 Phase C
fact-completeness, PR #719 follow-up): unlike every other backfilled fact
above, both are plain ``bool = False`` rather than an Optional tri-state, so
:func:`_merge_record_type` OR-merges them (never a null-check backfill) —
castxml's own ``False`` is always structurally correct by construction, not
a placeholder for "unknown". Verified against real castxml 0.6.3 + clang 18
output that ``is_template_pattern``'s backfill is empirically inert for the
current producer pair (a clang-recognized template pattern never shares a
type_map_key with any castxml-matched concrete type — it reaches the merged
snapshot via the clang-only-append path instead, already carrying the flag
correctly); kept anyway as a defense-in-depth/honesty measure, the same
precedent already set for ``RecordType.is_abstract``.
``has_anonymous_aggregate_fields`` is not provably inert the same way —
see :func:`_merge_record_type`'s own comment.

Everything not explicitly merged below (bare-keyed ``typedefs``, constants,
ELF/PE/Mach-O metadata, DWARF metadata, ...) is taken verbatim from the
castxml base (``dataclasses.replace``) -- except ``typedefs_qualified``
(schema v25), unioned from both sides (its own merge comment) to recover
an alias a single backend alone would miss.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import qualified_name_segments
from .comparability import PROFILE_FIELD_KEYS, _sha256_of
from .diff_helpers import type_map_key
from .dumper_castxml import (
    SYNTHETIC_CTOR_KEY_PREFIX,
    is_synthetic_ctor_key,
    is_synthetic_dtor_key,
)
from .fact_provenance import (
    backfill_fact,
    enum_fact_key,
    field_fact_key,
    func_fact_key,
    type_fact_key,
    var_fact_key,
)
from .model import (
    AbiSnapshot,
    EnumType,
    Function,
    RecordType,
    TypeField,
    Variable,
    replace_with_fact_sync,
)
from .model.identity import with_mangled_name
from .model.mangled_name import _skip_template_args, itanium_scope_components
from .name_classification import canonicalize_type_name

_CTOR_MARKER = "{ctor}"
_DTOR_MARKER = "{dtor}"
_CALLING_CONVENTION_ATTRIBUTES = frozenset({"ms_abi", "sysv_abi"})

log = logging.getLogger(__name__)


def _ctor_dtor_scope(mangled: str) -> tuple[str, str] | None:
    """``(marker, qualified_scope)`` for a REAL Itanium-mangled ctor/dtor, or
    None if *mangled* isn't one (parsed structurally — see
    ``model.mangled_name.itanium_scope_components``, which returns the ctor/dtor
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
    candidates = clang_ctor_dtor.get((marker, _normalize_scope_for_matching(scope)), [])
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
        value = backfill_fact(
            getattr(f, attr), getattr(clang_f, attr, None), key, provenance
        )
        if value != getattr(f, attr):
            updates[attr] = value
    # CastXML can omit GNU x86-64 ABI attributes from its ``attributes``
    # string even though clang's AST records them.  Backfill only when the
    # CastXML declaration captured attributes and has *no* calling-convention
    # claim.  Never union incompatible conventions: one function cannot be
    # both ms_abi and sysv_abi, and the TU merger rejects that contradiction.
    # Keep the CastXML-primary value on a disagreement and warn, rather than
    # silently manufacturing an impossible ABI or selecting clang as an
    # undocumented override.
    if clang_f is not None and clang_f.contract_attributes is not None:
        own_attrs = f.contract_attributes or []
        own_cc = {
            attr
            for attr in own_attrs
            if attr.split("(", 1)[0] in _CALLING_CONVENTION_ATTRIBUTES
        }
        clang_cc = {
            attr
            for attr in clang_f.contract_attributes
            if attr.split("(", 1)[0] in _CALLING_CONVENTION_ATTRIBUTES
        }
        cc_key = func_fact_key(f.mangled, "calling_convention")
        if not own_cc and clang_cc:
            updates["contract_attributes"] = sorted(set(own_attrs) | clang_cc)
            provenance[cc_key] = "clang"
        elif own_cc and clang_cc and own_cc != clang_cc:
            provenance[cc_key] = "castxml"
            log.warning(
                "hybrid calling-convention conflict for %s: castxml=%s, clang=%s; "
                "keeping castxml evidence",
                f.mangled,
                sorted(own_cc),
                sorted(clang_cc),
            )
        elif own_cc:
            provenance[cc_key] = "castxml"
    # ELF-sourced facts (elf_binding/elf_visibility) are independent of
    # which AST backend produced the declaration -- both backends'
    # dumper_elf_symbols._populate_elf_visibility reads the same .dynsym
    # symbol map keyed by mangled name, so clang_f's own value is the SAME
    # real fact, not a competing producer's opinion. This matters
    # specifically for a synthetic ctor/dtor key just rewritten to its real
    # clang mangled name above: castxml's own _populate_elf_visibility call
    # could never match the synthetic placeholder key against .dynsym, so
    # its elf_binding/elf_visibility are still None even though the entity
    # DOES have a real exported symbol -- clang_f, keyed correctly from the
    # start, already carries the right value (Codex review, fresh
    # evidence). For an ordinary (non-rewritten) function this is a no-op:
    # both sides independently looked up the identical real key, so a
    # genuinely-None castxml value means clang's is None too. Deliberately
    # NOT routed through backfill_fact/provenance: this isn't a producer
    # disagreement to record, just recovering a fact that was always there
    # under the right key.
    if (
        f.elf_binding is None
        and clang_f is not None
        and clang_f.elf_binding is not None
    ):
        updates["elf_binding"] = clang_f.elf_binding
    if (
        f.elf_visibility is None
        and clang_f is not None
        and clang_f.elf_visibility is not None
    ):
        updates["elf_visibility"] = clang_f.elf_visibility
    # ADR-063 Phase 5: keeps every fact-bridged field's Fact sibling in sync.
    return replace_with_fact_sync(f, **updates) if updates else f


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
                # Adopt match's entity_id too, or it keeps the synthetic key.
                f = replace(f, mangled=match.mangled, entity_id=match.entity_id)
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
    # "visibility" records which backend contributed the DECLARATION ITSELF
    # (castxml-primary vs. clang-only-appended), not a per-field value merge
    # like every other key this function writes — consumed by
    # buildsource.header_graph.build_header_only_graph() to stamp
    # GraphNode.attrs["visibility_provenance"] on the L2 header-only graph's
    # source_decl nodes (G31 Phase C hybrid-graph provenance-tagging;
    # docs/contribute/plans/g31-header-graph-default-on-followup.md). A
    # castxml-primary function's ScopeOrigin classification and a
    # clang-only-appended one's both go through the identical
    # provenance.apply_provenance() pass afterwards, so this key is not
    # itself the classification — it's which backend's declaration record
    # that classification was computed from, the same distinction
    # "param_defaults" above already tracks for a different consumer.
    for f in merged:
        provenance[func_fact_key(f.mangled, "param_defaults")] = "castxml"
        provenance[func_fact_key(f.mangled, "visibility")] = "castxml"

    merged_mangled = {f.mangled for f in merged}
    clang_only = [cf for cf in clang_funcs if cf.mangled not in merged_mangled]
    for cf in clang_only:
        provenance[func_fact_key(cf.mangled, "param_defaults")] = "clang"
        # A clang-only function's own deprecated value IS genuinely
        # clang-sourced -- without this, both_known_backed_fact(old, new,
        # func_fact_key(mangled, "deprecated")) sees no recorded provenance
        # for it at all and incorrectly declines to compare a real
        # deprecation transition on a declaration that exists on both sides
        # only via clang (Codex review, fresh evidence).
        provenance[func_fact_key(cf.mangled, "deprecated")] = "clang"
        # Same reasoning as "deprecated" immediately above, for
        # is_override (G31 Phase C's is_override/is_abstract backend
        # audit): a clang-only method's is_override IS genuinely
        # clang-sourced, and without this stamp
        # both_known_backed_fact(old, new, func_fact_key(mangled,
        # "is_override")) sees no recorded provenance for it and declines
        # to compare a real override-specifier transition on a method
        # that exists on both sides only via clang (Codex review, fresh
        # evidence).
        provenance[func_fact_key(cf.mangled, "is_override")] = "clang"
        provenance[func_fact_key(cf.mangled, "visibility")] = "clang"
    merged.extend(clang_only)
    return merged


def _merge_variable(
    v: Variable, clang_v: Variable | None, provenance: dict[str, str]
) -> Variable:
    key = var_fact_key(v.mangled, "deprecated")
    value = backfill_fact(
        v.deprecated, clang_v.deprecated if clang_v else None, key, provenance
    )
    return replace(v, deprecated=value) if value != v.deprecated else v


#: G28 Phase 4 layout facts castxml either never populates at all
#: (data_size_bits/is_standard_layout/is_trivially_copyable) or leaves empty
#: for an opaque/incomplete record (size_bits/alignment_bits/
#: vptr_offset_bits) -- backfilled from an already-enriched clang_t below
#: only when castxml's own value is still None (Codex review). Since G31
#: Phase C, is_standard_layout/is_trivially_copyable no longer need the
#: optional ABICHECK_CLANG_LAYOUT_TOOL companion tool to backfill from --
#: dumper_clang.py's plain parse populates both directly (see
#: _clang_record_type_traits) -- the other four entries still do.
_LAYOUT_SCALAR_ATTRS = (
    "size_bits",
    "alignment_bits",
    "data_size_bits",
    "is_standard_layout",
    "is_trivially_copyable",
    "vptr_offset_bits",
)


def _merge_field(
    t: RecordType,
    f: TypeField,
    clang_f: TypeField | None,
    provenance: dict[str, str],
) -> TypeField:
    updates: dict[str, Any] = {}
    for attr in ("default", "deprecated"):
        # Both facts are genuinely cross-producer since G31 Phase C
        # ("deprecated" from that phase's first pass, "default" from
        # dumper_clang_expr._field_initializer_value), so both need the qualified
        # key: a clang-only sibling type sharing t's bare name independently
        # writes to this same provenance dict (see merge_snapshots'
        # clang-only-type append loop below), and a bare key would let one
        # writer's entry silently overwrite the other's (Codex review, fresh
        # evidence). "default" was deliberately left bare when only
        # "deprecated" got a clang-only-append write -- that exemption no
        # longer holds now that "default" gets one too. Legacy hybrid
        # baselines keyed bare are still read via
        # diff_helpers.fact_same_producer_qualified's bare fallback.
        key = field_fact_key(type_map_key(t), f.name, attr)
        value = backfill_fact(
            getattr(f, attr), getattr(clang_f, attr, None), key, provenance
        )
        if value != getattr(f, attr):
            updates[attr] = value
    # G28 Phase 4: same layout backfill as _merge_record_type, for the
    # per-field offset the optional companion tool computes.
    if (
        clang_f is not None
        and f.offset_bits is None
        and clang_f.offset_bits is not None
    ):
        updates["offset_bits"] = clang_f.offset_bits
    return replace(f, **updates) if updates else f


def _merge_record_type(
    t: RecordType, clang_t: RecordType | None, provenance: dict[str, str]
) -> RecordType:
    updates: dict[str, Any] = {}
    for attr in ("is_abstract", "deprecated"):
        # Same bare-vs-qualified split as _merge_field above: is_abstract
        # stays bare (pre-existing castxml-only fact, no clang-only append
        # writes to it), deprecated is qualified (G31 Phase C).
        type_key = type_map_key(t) if attr == "deprecated" else t.name
        key = type_fact_key(type_key, attr)
        value = backfill_fact(
            getattr(t, attr), getattr(clang_t, attr, None), key, provenance
        )
        if value != getattr(t, attr):
            updates[attr] = value

    # G28 Phase 4 (optional ABICHECK_CLANG_LAYOUT_TOOL): clang_t may carry REAL ASTRecordLayout facts the companion tool already backfilled onto clang_snap BEFORE this merge (attach_clang_layout runs on clang_snap's own recursive dump). Without this, a type present on BOTH backends -- the common case -- lost every one of these facts in a hybrid merge even with the layout tool enabled, while a clang-ONLY type (appended verbatim below) kept them (Codex review). Never overrides an existing castxml value -- castxml's own real layout, when present, always wins.
    if clang_t is not None:
        for attr in _LAYOUT_SCALAR_ATTRS:
            if getattr(t, attr) is None and getattr(clang_t, attr) is not None:
                updates[attr] = getattr(clang_t, attr)
                # vptr_offset_bits_fact sibling: carry clang_t's own status so replace_with_fact_sync can't promote its real PARTIAL to present() (same bug class as dumper_layout_backfill.py's DWARF backfill, Codex review).
                if hasattr(clang_t, f"{attr}_fact"):
                    updates[f"{attr}_fact"] = getattr(clang_t, f"{attr}_fact")
        if not t.base_offsets and clang_t.base_offsets:
            updates["base_offsets"] = clang_t.base_offsets
        # G31 Phase C fact-completeness (verified against real castxml 0.6.3 +
        # clang 18 output, PR #719 follow-up): unlike every other backfilled
        # fact above, these two are plain `bool = False` -- not an Optional
        # tri-state -- so there is no null "castxml doesn't know" state to key
        # a backfill_fact()-style None-check off. castxml's own `False` is
        # ALWAYS structurally correct by construction rather than a placeholder
        # (castxml never emits an uninstantiated template pattern as a
        # declaration at all, and it always computes real per-field offsets
        # for an anonymous-aggregate flatten it can see), so this is a plain
        # OR-merge, not a None-guarded backfill.
        #
        # is_template_pattern is empirically INERT here, verified with a real
        # class-template dump: a clang-recognized template PATTERN never
        # shares a type_map_key with any castxml-visible concrete type (it's
        # a structurally distinct entity -- castxml only ever emits concrete
        # instantiations under their own instantiated name, e.g. "Box<int>",
        # never a bare "Box" pattern declaration), so `clang_t` for a
        # castxml-matched `t` is never itself the pattern; the True-carrying
        # clang entry instead reaches the merged snapshot verbatim via the
        # clang-only-append path below. Kept here anyway (not asserted
        # unreachable) both for defense in depth against a future clang
        # AST-shape change and because it is honest about the invariant this
        # merge is supposed to preserve, matching this module's own documented
        # precedent for RecordType.is_abstract (a backfill kept even though
        # the current producer pair makes it a no-op).
        #
        # has_anonymous_aggregate_fields is NOT provably inert the same way: a
        # castxml record with real, populated fields already carries
        # corroborating field-name-overlap evidence dumper_layout_backfill.py
        # prefers over this flag's own fallback path, but an opaque/incomplete
        # castxml record (or a future producer shape) could legitimately reach
        # this merge with an EMPTY `fields` list for a genuinely
        # anonymous-aggregate-only record, where clang's `True` is the only
        # signal available.
        if clang_t.is_template_pattern and not t.is_template_pattern:
            updates["is_template_pattern"] = True
        if clang_t.has_anonymous_aggregate_fields and not t.has_anonymous_aggregate_fields:
            updates["has_anonymous_aggregate_fields"] = True

    clang_fields_by_name = {cf.name: cf for cf in clang_t.fields} if clang_t else {}
    merged_fields = [
        _merge_field(t, f, clang_fields_by_name.get(f.name), provenance)
        for f in t.fields
    ]
    if merged_fields != t.fields:
        updates["fields"] = merged_fields

    from .model import replace_with_fact_sync

    return replace_with_fact_sync(t, **updates) if updates else t


def _merge_enum_type(
    e: EnumType, clang_e: EnumType | None, provenance: dict[str, str]
) -> EnumType:
    updates: dict[str, Any] = {}
    # Both facts get clang-only-append writes (merge_snapshots' clang-only
    # enum loop below), so both need the qualified key uniformly -- unlike
    # RecordType's is_abstract/deprecated split above, there's no bare-only
    # fact here to preserve compatibility with.
    type_key = type_map_key(e)
    for attr in ("is_scoped", "deprecated"):
        key = enum_fact_key(type_key, attr)
        value = backfill_fact(
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
    # entity_id's "mangled" tag is re-spelled too (Codex review).
    clang_functions = clang_snap.functions
    clang_variables = clang_snap.variables
    if castxml_snap.platform == "macho":
        clang_functions = [
            replace(cf, mangled=nm, entity_id=with_mangled_name(cf.entity_id, nm))
            for cf in clang_functions
            for nm in (_macho_normalize_mangled(cf.mangled),)
        ]
        clang_variables = [
            replace(cv, mangled=nm, entity_id=with_mangled_name(cv.entity_id, nm))
            for cv in clang_variables
            for nm in (_macho_normalize_mangled(cv.mangled),)
        ]

    # Keyed by type_map_key (namespace-qualified identity), not the bare
    # RecordType.name/EnumType.name: two distinct types sharing only a bare
    # leaf name in different namespaces (e.g. a::Foo/b::Foo) would otherwise
    # silently collide here too -- one castxml record merging against the
    # WRONG clang record, and/or a genuinely clang-only record (that merely
    # shares its bare name with an unrelated castxml record) being dropped
    # instead of appended (Codex review, fresh evidence).
    clang_types_by_key = {type_map_key(t): t for t in clang_snap.types}
    clang_enums_by_key = {type_map_key(e): e for e in clang_snap.enums}
    clang_vars_by_mangled = {v.mangled: v for v in clang_variables}

    merged_functions = _merge_functions(
        castxml_snap.functions, clang_functions, provenance
    )

    merged_types = [
        _merge_record_type(t, clang_types_by_key.get(type_map_key(t)), provenance)
        for t in castxml_snap.types
    ]
    castxml_type_keys = {type_map_key(t) for t in castxml_snap.types}
    clang_only_types = [
        t for t in clang_snap.types if type_map_key(t) not in castxml_type_keys
    ]
    for t in clang_only_types:
        # A clang-only type's own deprecated value IS genuinely clang-
        # sourced -- without this, both_known_backed_fact sees no recorded
        # provenance at all for a declaration that exists on both snapshot
        # sides only via clang, and incorrectly declines to compare a real
        # transition (Codex review, fresh evidence). Qualified key (not
        # bare t.name): two distinct types sharing only a bare leaf name in
        # different namespaces (e.g. a genuinely clang-only b::Foo and a
        # castxml+clang-matched a::Foo) would otherwise silently collide in
        # this shared provenance dict too -- one writer's entry
        # overwriting the other's (Codex review, fresh evidence, second
        # round) -- matching _merge_record_type/_merge_field's identical
        # qualification for this same fact above.
        type_key = type_map_key(t)
        provenance[type_fact_key(type_key, "deprecated")] = "clang"
        # Same reasoning as "deprecated" immediately above, for is_abstract
        # (G31 Phase C's is_override/is_abstract backend audit): a
        # clang-only type's own is_abstract value IS genuinely
        # clang-sourced, and without this stamp both_known_backed_fact
        # sees no recorded provenance for it and declines to compare a
        # real abstractness transition on a type that exists on both
        # sides only via clang (Codex review, fresh evidence). BARE key
        # (not qualified type_key, unlike "deprecated" above): is_abstract
        # is the one fact `_merge_record_type` deliberately keys bare
        # (see that function's own comment), because `diff_types._diff_types`
        # only ever looks it up via the bare `type_fact_key(t_old.name,
        # "is_abstract")` -- a qualified key here would silently mismatch
        # that lookup and make this stamp inert for a namespaced type
        # (Codex review, fresh evidence, third round).
        provenance[type_fact_key(t.name, "is_abstract")] = "clang"
        for f in t.fields:
            provenance[field_fact_key(type_key, f.name, "deprecated")] = "clang"
            # "default" joined "deprecated" as a genuinely clang-sourced field
            # fact in G31 Phase C (dumper_clang_expr._field_initializer_value).
            # Its detector gates on SAME producer rather than any-known
            # producer (the two backends' initializer representations aren't
            # cross-comparable), so this stamp is what lets a hybrid-vs-hybrid
            # pair compare a clang-only field's initializer at all -- and,
            # equally, what lets a mixed pair be correctly declined instead of
            # silently compared as if same-producer.
            provenance[field_fact_key(type_key, f.name, "default")] = "clang"
    merged_types.extend(clang_only_types)

    merged_enums = [
        _merge_enum_type(e, clang_enums_by_key.get(type_map_key(e)), provenance)
        for e in castxml_snap.enums
    ]
    castxml_enum_keys = {type_map_key(e) for e in castxml_snap.enums}
    clang_only_enums = [
        e for e in clang_snap.enums if type_map_key(e) not in castxml_enum_keys
    ]
    for e in clang_only_enums:
        # Qualified key -- same reasoning as clang_only_types above.
        type_key = type_map_key(e)
        provenance[enum_fact_key(type_key, "deprecated")] = "clang"
        provenance[enum_fact_key(type_key, "is_scoped")] = "clang"
    merged_enums.extend(clang_only_enums)

    merged_variables = [
        _merge_variable(v, clang_vars_by_mangled.get(v.mangled), provenance)
        for v in castxml_snap.variables
    ]
    # "visibility" mirrors _merge_functions' identical stamp above -- which
    # backend contributed the declaration itself, consumed by
    # header_graph.build_header_only_graph() for its graph-node provenance
    # tag, not a per-field value merge.
    for v in castxml_snap.variables:
        provenance[var_fact_key(v.mangled, "visibility")] = "castxml"
    castxml_var_mangled = {v.mangled for v in castxml_snap.variables}
    clang_only_variables = [
        v for v in clang_variables if v.mangled not in castxml_var_mangled
    ]
    for v in clang_only_variables:
        provenance[var_fact_key(v.mangled, "deprecated")] = "clang"
        provenance[var_fact_key(v.mangled, "visibility")] = "clang"
    merged_variables.extend(clang_only_variables)

    merged = replace(
        castxml_snap,
        functions=merged_functions,
        variables=merged_variables,
        types=merged_types,
        enums=merged_enums,
        # typedefs_qualified (schema v25, G31 Phase C continued, Codex
        # review): unlike bare `typedefs` (left verbatim from castxml_snap,
        # same as constants/ELF/PE/Mach-O metadata -- see this function's
        # own docstring), this field's whole purpose is to recover a
        # qualified typedef alias `type_reachability.py`'s scan would
        # otherwise miss. Leaving it castxml-only defeats that purpose for
        # a hybrid dump: a declaration only clang appended (or a typedef
        # only clang's own parse captured under this qualified key) would
        # never make it into `merged.typedefs_qualified`, so a public
        # signature referencing it through that alias could still miss a
        # reachable `std::` field. Union both sides -- qualified keys are
        # unique per declaration, so a real cross-backend disagreement on
        # the SAME key is not expected; castxml's own value wins on the
        # rare disagreement, matching "castxml remains the base" elsewhere
        # in this merge.
        typedefs_qualified={
            **clang_snap.typedefs_qualified,
            **castxml_snap.typedefs_qualified,
        },
        ast_producer="hybrid",
        ast_toolchain={
            **{
                f"castxml_{key}": value
                for key, value in castxml_snap.ast_toolchain.items()
            },
            **{
                f"clang_{key}": value for key, value in clang_snap.ast_toolchain.items()
            },
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

    # ADR-050 D1 (Codex review, PR #624 follow-up): without this, the merged
    # "hybrid" snapshot's contract stays castxml_snap's alone, silently
    # dropping the clang leg's own compiler identity -- two hybrid dumps
    # differing only in which clang binary/version parsed the clang leg
    # (the castxml leg identical) would then share a profile_fingerprint
    # despite a genuinely different extraction context on that leg. Both
    # sub-contracts are already correctly computed (each dump_fn call in
    # run_hybrid_dump gets its own via dumper._attach_extraction_contract);
    # fold the clang leg's compiler identity into the merged contract's
    # existing compiler_version field and recompute just that one
    # dependent hash, rather than re-deriving identity from the raw,
    # prefix-merged ast_toolchain dict above.
    if merged.contract is not None and clang_snap.contract is not None:
        # json.dumps, not a raw join (same class of bug already fixed for
        # macro_ops/slot tokens in comparability.py): neither identity
        # string is guaranteed delimiter-free.
        combined_compiler_version = json.dumps(
            [
                merged.contract.profile_fields.get("compiler_version", ""),
                clang_snap.contract.profile_fields.get("compiler_version", ""),
            ]
        )
        new_profile_fields = {
            **merged.contract.profile_fields,
            "compiler_version": combined_compiler_version,
        }
        new_fingerprint = _sha256_of(
            *[new_profile_fields[k] for k in PROFILE_FIELD_KEYS]
        )
        merged = replace(
            merged,
            contract=replace(
                merged.contract,
                profile_fields=new_profile_fields,
                profile_fingerprint=new_fingerprint,
            ),
        )

    return merged


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
    with qualified_name_segments.defer_closure_identity_renumbering():
        castxml_snap = dump_fn(so_path, headers, header_backend="castxml", **kwargs)
        clang_snap = dump_fn(so_path, headers, header_backend="clang", **kwargs)
    return qualified_name_segments.renumber_anonymous_closure_identities(merge_snapshots(castxml_snap, clang_snap))
