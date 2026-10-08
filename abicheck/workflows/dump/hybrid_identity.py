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

"""Hybrid merge: ctor/dtor identity reconciliation and Mach-O normalization.

The key-matching half of :mod:`abicheck.workflows.dump.hybrid_merge`
(castxml synthetic ctor/dtor keys against clang's real Itanium names,
Mach-O ``_``-prefix normalization, the matching semantic-IR entity-id
rewrites, and the per-function fact backfill those matches feed). Split
out of the former ``dumper_hybrid.py`` (lane B, stage B5) to keep each
module under the 800-line cap.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from ...fact_provenance import (
    backfill_fact,
    func_fact_key,
)
from ...model import (
    Function,
    replace_with_fact_sync,
)
from ...model.identity import EntityId, EntityKind, with_mangled_name
from ...model.mangled_name import itanium_scope_components
from ...model.mangled_name_template_args import (
    skip_template_args as _skip_template_args,
)
from ...model.name_decoration import macho as macho_decoration
from ...model.occurrence import OccurrenceId
from ...model.semantic_ir import CanonicalEntity, SemanticIR, semantic_ir_conflict_key
from ...model.synthetic_key import (
    SYNTHETIC_CTOR_KEY_PREFIX,
    is_synthetic_ctor_key,
    is_synthetic_dtor_key,
)
from ...name_classification import canonicalize_type_name

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
    (``extract.headers.castxml.functions.function_mangled_name``'s ``",".join(ctor_identity_types)``)
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
    """Strip a still-present Darwin linker-symbol leading underscore,
    matching castxml's prefix-free convention on the same platform.

    Darwin prepends one underscore to every global symbol (C ``foo`` ->
    ``_foo``; Itanium ``_Z...`` -> ``__Z...``). The header-AST backends now
    normalize this at the point of origin too, so *mangled* reaching here
    is ordinarily **already** undecorated -- the Itanium half delegates to
    the Mach-O codec (``model.name_decoration.macho``): the ``"__Z..."``
    Itanium shape first, then the C-linkage ``"_foo"`` -> ``"foo"`` shape.
    """
    stripped = macho_decoration.decode_itanium(mangled)
    if stripped != mangled or mangled.startswith("_Z"):
        return stripped
    return macho_decoration.decode_c(mangled) or mangled


def _rewrite_semantic_ir_entity_ids(
    ir: SemanticIR | None, rewrites: Mapping[EntityId, EntityId]
) -> SemanticIR | None:
    """*ir* with every occurrence whose ``EntityId`` appears as a key in
    *rewrites* moved to that key's value, leaving every other occurrence
    untouched (ADR-063 Phase 6 third slice, Codex review, fresh evidence).

    The shared primitive both this module's identity-rewrite sites reduce
    to: an occurrence built from one backend's own parse can have its
    identity superseded LATER, by a decision this module itself makes
    (:func:`_macho_normalize_mangled`'s Darwin-underscore strip;
    :func:`_merge_functions`'s castxml-synthetic-ctor/dtor-to-real-clang-
    mangled-name match) -- a rewrite the per-backend `semantic_ir`
    construction (`extract.semantic_normalizer.normalize_header_ast`, which
    runs before either of those decisions) cannot see or participate in.
    Left unrewritten, `merge_semantic_ir`'s bare-`EntityId` matching cannot
    recognize the pre-rewrite and post-rewrite spellings as "the same
    declaration," and either retains the affected occurrence twice (once
    under each identity) or leaves a stale identity in the merged IR that
    the flat, already-rewritten `functions`/`variables` no longer carry --
    two different failure shapes, the same root cause.

    ``None`` in, ``None`` out, matching :func:`~abicheck.extract.
    semantic_ir_merge.merge_semantic_ir`'s own convention for "this backend
    produced no IR at all". A no-op (returns *ir* unchanged, not a rebuilt-
    but-equal copy) when *rewrites* is empty or matches nothing, mirroring
    that same module's "only touch what actually changed" convention.
    """
    if ir is None or not rewrites:
        return ir
    rewritten: dict[OccurrenceId, CanonicalEntity] = {}
    changed = False
    for occ_id, entity in ir.occurrences.items():
        new_entity_id = rewrites.get(occ_id.entity_id, occ_id.entity_id)
        new_occ_id = (
            occ_id
            if new_entity_id == occ_id.entity_id
            else OccurrenceId(new_entity_id, occ_id.disambiguator)
        )
        # First-observation-wins on a post-rewrite collision, mirroring
        # `extract.semantic_normalizer._add_occurrence`'s identical
        # convention -- an exceedingly rare shape rather than a case this
        # rewrite needs to merge facts for.
        if new_occ_id != occ_id:
            changed = True
        rewritten.setdefault(new_occ_id, entity)
    if not changed:
        return ir
    return SemanticIR(occurrences=rewritten)


def _macho_normalize_semantic_ir(ir: SemanticIR | None) -> SemanticIR | None:
    """*ir* with every ``FUNCTION``/``VARIABLE`` occurrence's ``EntityId``
    re-spelled through :func:`_macho_normalize_mangled`, mirroring the
    identical rewrite this module already applies to *clang_functions*/
    *clang_variables* above (Codex review, ADR-063 Phase 6 third slice,
    fresh evidence).

    Without this, a Mach-O hybrid dump's ``clang_snap.semantic_ir`` keeps
    every function/variable occurrence under clang's own Darwin-decorated
    mangled key (``"__Z..."``) while the flat ``merged_functions``/
    ``merged_variables`` this same function builds from the ALREADY-
    normalized *clang_functions*/*clang_variables* carry the prefix-free
    one (``"_Z..."``) -- so ``merge_semantic_ir``'s bare-``EntityId``
    matching never recognizes the two as the same declaration, and every
    affected function/variable is retained TWICE in the merged
    ``semantic_ir`` (once under each spelling) while the flat snapshot has
    it once, normalized. Types/enums need no equivalent call: they key on
    the source-level name, never a mangled linker symbol, so they carry no
    such platform-specific decoration in the first place (see this
    module's own comment at the call site below).
    """
    if ir is None:
        return ir
    rewrites: dict[EntityId, EntityId] = {}
    for occ_id in ir.occurrences:
        entity_id = occ_id.entity_id
        if (
            entity_id.kind in (EntityKind.FUNCTION, EntityKind.VARIABLE)
            and len(entity_id.extra) == 2
            and entity_id.extra[0] == "mangled"
        ):
            new_entity_id = with_mangled_name(
                entity_id, _macho_normalize_mangled(entity_id.extra[1])
            )
            if new_entity_id is not None and new_entity_id != entity_id:
                rewrites[entity_id] = new_entity_id
    return _rewrite_semantic_ir_entity_ids(ir, rewrites)


def _drop_unmatched_constant_occurrences(
    ir: SemanticIR | None,
    conflicts: dict[str, str],
    kept_entity_ids: set[EntityId],
) -> tuple[SemanticIR | None, dict[str, str]]:
    """*ir*/*conflicts* with every ``CONSTANT`` occurrence whose ``EntityId``
    is not in *kept_entity_ids* dropped, every other occurrence untouched
    (ADR-063 Phase 6 fourth slice, Codex review, fresh evidence).

    ``merged.constants`` (unlike every other flat field this function
    builds) deliberately stays ``castxml_snap.constants`` verbatim -- see
    that field's own comment at its ``replace()`` call site for why a
    clang-only key cannot be unioned in the way ``typedefs_qualified`` is.
    ``merge_semantic_ir`` has no such special case: it is generic over
    every ``EntityKind`` and appends an overlay-only occurrence (a clang-
    only constant) into the merged IR unconditionally, the same as it would
    for a clang-only function/variable/type/enum -- all of which genuinely
    ARE unioned into their own flat fields, unlike constants. Left
    unfiltered, a clang-only constant would surface through ``semantic_ir``
    with no corresponding entry in ``merged.constants``/``constant_
    entity_ids`` at all, an occurrence naming a "declaration" the rest of
    the snapshot does not know exists. *kept_entity_ids* is the exact same
    set the caller already derives for the final, unioned-then-filtered
    ``constant_entity_ids`` (filtered to ``castxml_snap.constants``'s own
    keys) -- passed in rather than recomputed, so the two cannot drift.

    ``None`` in, ``None`` out, mirroring :func:`_rewrite_semantic_ir_
    entity_ids`'s convention. A no-op (returns *ir*/*conflicts* unchanged)
    when nothing needed dropping.
    """
    if ir is None:
        return ir, conflicts
    kept: dict[OccurrenceId, CanonicalEntity] = {}
    dropped: list[tuple[OccurrenceId, CanonicalEntity]] = []
    for occ_id, entity in ir.occurrences.items():
        if (
            occ_id.entity_id.kind is not EntityKind.CONSTANT
            or occ_id.entity_id in kept_entity_ids
        ):
            kept[occ_id] = entity
        else:
            dropped.append((occ_id, entity))
    if not dropped:
        return ir, conflicts
    filtered_ir = SemanticIR(occurrences=kept)
    if not conflicts:
        return filtered_ir, conflicts
    dropped_keys = {
        semantic_ir_conflict_key(occ_id, fact_name)
        for occ_id, entity in dropped
        for fact_name, _fact in entity.fact_items()
    }
    filtered_conflicts = {
        key: value for key, value in conflicts.items() if key not in dropped_keys
    }
    return filtered_ir, filtered_conflicts


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
    ``extract.headers.castxml.functions.function_mangled_name``'s synthesis).
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
    # key's own identity (extract.headers.castxml.functions.ctor_param_identity_type already
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
