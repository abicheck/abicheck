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

"""ADR-050 D4 (G32 Phase C) — compatible merge across translation units.

Replaces Phase B's placeholder merge (``dumper_manifest.py``'s original
``merge_tu_fragments``, which raised the moment the same ``entity_key``
appeared in more than one :class:`~abicheck.tu_fragment.TuFragment`,
regardless of whether the two declarations were actually compatible) with
the real merge lattice the ADR describes: for each ``entity_key`` seen in
more than one fragment, the merge is only *trivial* — union provenance,
keep the richer declaration — when the two declarations are genuinely
compatible: a forward declaration paired with its full definition, a plain
declaration + redeclaration, or a difference confined to an added default
argument / initializer. Two full declarations that disagree on anything
else (return type, layout, calling convention, ...) raise
:class:`abicheck.errors.TuMergeError` with ``code="INCONSISTENT_DECLARATION"``
instead of silently picking one side.

**Both conflict codes are extraction-time failures, not
:class:`~abicheck.checker_policy.ChangeKind` members**, despite the
all-caps naming reading exactly like one. A :class:`~abicheck.checker_policy.ChangeKind`
is something ``checker.compare``'s diff produces when comparing two already-
complete snapshots; :class:`~abicheck.errors.TuMergeError` fires *before* a
manifest-driven dump ever produces a snapshot to diff — a fragment set with
an unresolved conflict is not a complete snapshot and can never reach D2's
comparability gate as a clean side. It is therefore correctly outside the
``ChangeKind`` registry and its four-step procedure, the
``changekind-partition``/``changekind-detector`` completeness gates, and
``RISK_KINDS``/``QUALITY_KINDS`` severity classification entirely — the
same reasoning already applied to ``IncompatibleSnapshotSchemaError`` (D1)
and ``DumpDepthNotSatisfiedError``.

``HETEROGENEOUS_ABI_CONTEXT`` fires when the *declared* compiler/target is
uniform (``dump_manifest.py``'s parse-time rule already guarantees that,
D3) but the TUs were nonetheless *extracted* by different AST producers --
``--ast-frontend auto`` falls back to a different backend independently per
TU, so one TU can land on castxml while another falls back to clang within
the same manifest (Codex review, PR #635). ``merge_fragments`` checks every
contributing fragment's ``ast_producer`` for exactly this before merging
any entities.

**Determinism**: merge is deterministic regardless of the order fragments
are passed in — a required property (shuffled TU-processing order must
produce byte-identical merged output). This module never folds fragments
in the caller-supplied order: every per-``entity_key`` candidate list is
built by iterating fragments sorted by their (unique) ``tu_name`` first, so
both which candidate "wins" a union (e.g. which side's default argument is
kept) and the final entity ordering depend only on fragment *content*
(tu_name, entity_key), never on the incidental order ``run_tu_loop``
happened to finish TUs in.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from functools import partial
from typing import TypeVar

from .errors import TuMergeError
from .model import EnumType, Function, Param, RecordType, ScopeOrigin, Variable
from .provenance import build_public_set, classify_origin, header_from_location
from .tu_fragment import MergedTuFragments, TuFragment, entity_key

#: TuMergeError.code values (ADR-050 D4). Kept as plain module constants
#: (not an enum) since TuMergeError.code is a bare string field, matching
#: ManifestValidationError's/comparability's own string-code precedent.
INCONSISTENT_DECLARATION = "INCONSISTENT_DECLARATION"
HETEROGENEOUS_ABI_CONTEXT = "HETEROGENEOUS_ABI_CONTEXT"

_T = TypeVar("_T")


def merge_fragments(
    fragments: Sequence[TuFragment],
    *,
    public_header_paths: Sequence[str] = (),
    public_header_dirs: Sequence[str] = (),
) -> MergedTuFragments:
    """Merge *fragments* into one :class:`MergedTuFragments`, resolving
    cross-TU redeclarations of the same :func:`~abicheck.tu_fragment.entity_key`
    when they are compatible and raising :class:`~abicheck.errors.TuMergeError`
    when they are not (ADR-050 D4).

    *public_header_paths*/*public_header_dirs* are the same manifest-wide
    public-surface inputs :func:`abicheck.provenance.apply_provenance` later
    classifies declarations against (opt-in — omitted, every declaration's
    origin stays ``UNKNOWN`` and this has no effect, matching that module's
    own default). When supplied, they let a trivial merge prefer whichever
    side's ``source_location`` classifies as ``PUBLIC_HEADER`` as the
    winning declaration's representative provenance -- see
    :func:`_more_public_of`'s own docstring for why this matters: a merged
    entity carries exactly one ``source_location``, and picking the wrong
    side can make a genuinely public declaration read as private/unreachable
    once ``apply_provenance`` runs on the merged snapshot.
    """
    if not fragments:
        return MergedTuFragments(
            functions=(),
            variables=(),
            types=(),
            enums=(),
            typedefs={},
            constants={},
            ast_producer="castxml",
            ast_toolchain={},
            ast_fallback_reason=None,
            ast_toolchain_supported=None,
            ast_toolchain_unsupported_reasons=(),
        )

    # Fixed, content-derived order (tu_name, never the caller's own sequence
    # order) -- see this module's own "Determinism" docstring section.
    ordered = sorted(fragments, key=lambda f: f.tu_name)

    # HETEROGENEOUS_ABI_CONTEXT's real trigger (Codex review, PR #635):
    # dump_manifest.py's parse-time rule only rejects a manifest that
    # *declares* different compilers/target triples across its TUs -- it
    # says nothing about `--ast-frontend auto`'s per-TU fallback, which
    # picks castxml/clang independently for each TU at extraction time. A
    # manifest with two TUs, one falling back to clang while the other
    # stays on castxml, would otherwise merge fine and silently stamp the
    # whole snapshot with just one representative fragment's ast_producer
    # -- which `resolve_header_ast_result`/`dumper.py` then trusts globally
    # (`is_clang` gates DWARF layout backfill/coherence for every
    # declaration, not just the representative fragment's own). Reject
    # that mix outright rather than let a wrong-producer assumption leak
    # into layout backfill for declarations that didn't actually come from
    # the representative producer.
    producers = {f.ast_producer for f in ordered}
    if len(producers) > 1:
        raise TuMergeError(
            "translation units were extracted by different AST producers "
            f"({sorted(producers)!r}) -- likely --ast-frontend auto falling "
            "back to a different backend for only some TUs (see "
            "--allow-ast-frontend-fallback). A manifest-driven dump requires "
            "every TU to share one AST producer, the same way it already "
            "requires one compiler/target triple, since downstream layout "
            "backfill/coherence logic trusts a single producer for the "
            "whole merged snapshot.",
            code=HETEROGENEOUS_ABI_CONTEXT,
            entity_key=("manifest", "ast_producer"),
            tu_names=tuple(f.tu_name for f in ordered),
        )

    header_segs, dir_segs, have_public_set = build_public_set(
        list(public_header_paths), list(public_header_dirs)
    )

    functions = _flatten(
        _merge_group(
            ((f.tu_name, fn) for f in ordered for fn in f.functions),
            key_fn=lambda fn: entity_key("function", fn.mangled),
            merge_fn=partial(
                _merge_functions,
                header_segs=header_segs,
                dir_segs=dir_segs,
                have_public_set=have_public_set,
            ),
        )
    )
    variables = _flatten(
        _merge_group(
            ((f.tu_name, var) for f in ordered for var in f.variables),
            key_fn=lambda var: entity_key("variable", var.mangled),
            merge_fn=partial(
                _merge_variables,
                header_segs=header_segs,
                dir_segs=dir_segs,
                have_public_set=have_public_set,
            ),
        )
    )
    types = _flatten(
        _merge_group(
            ((f.tu_name, rt) for f in ordered for rt in f.types),
            # RecordType.name is deliberately bare (namespace lives in
            # qualified_name -- see RecordType's own docstring); keying on
            # the bare name alone would collide `one::X` and `two::X` into
            # one spurious INCONSISTENT_DECLARATION conflict (Codex review,
            # PR #635). Falls back to the bare name when qualified_name is
            # unset (global-scope types, or a producer that never captured
            # it), matching every other merge key's "None means unknown,
            # don't invent structure" convention.
            key_fn=lambda rt: entity_key("type", rt.qualified_name or rt.name),
            merge_fn=partial(
                _merge_types,
                header_segs=header_segs,
                dir_segs=dir_segs,
                have_public_set=have_public_set,
            ),
        )
    )
    enums = _flatten(
        _merge_group(
            ((f.tu_name, en) for f in ordered for en in f.enums),
            # Same bare-name-collision fix as RecordType above (Codex
            # review, PR #635) -- EnumType.name is likewise bare, with the
            # namespace in qualified_name.
            key_fn=lambda en: entity_key("enum", en.qualified_name or en.name),
            merge_fn=partial(
                _merge_enums,
                header_segs=header_segs,
                dir_segs=dir_segs,
                have_public_set=have_public_set,
            ),
        )
    )
    typedefs = _merge_scalar_group(
        (
            (f.tu_name, name, value)
            for f in ordered
            for name, value in f.typedefs.items()
        ),
        kind="typedef",
    )
    constants = _merge_scalar_group(
        (
            (f.tu_name, name, value)
            for f in ordered
            for name, value in f.constants.items()
        ),
        kind="constant",
    )

    # Any contributing fragment's AST provenance is representative: ADR-050
    # D3 rejects a manifest declaring different compilers/target triples
    # across TUs at parse time (dump_manifest.py -- compiler/target are
    # base-profile-only fields), so every fragment here was produced by the
    # same toolchain by construction. `ordered[0]` (not `fragments[0]`) so
    # the choice is itself order-independent.
    representative = ordered[0]
    return MergedTuFragments(
        functions=functions,
        variables=variables,
        types=types,
        enums=enums,
        typedefs=typedefs,
        constants=constants,
        ast_producer=representative.ast_producer,
        ast_toolchain=representative.ast_toolchain,
        ast_fallback_reason=representative.ast_fallback_reason,
        ast_toolchain_supported=representative.ast_toolchain_supported,
        ast_toolchain_unsupported_reasons=representative.ast_toolchain_unsupported_reasons,
    )


def _merge_group(
    items: Iterable[tuple[str, _T]],
    *,
    key_fn: Callable[[_T], tuple[str, str]],
    merge_fn: Callable[[_T, _T], _T | None],
) -> dict[tuple[str, str], tuple[_T, ...]]:
    """Group *items* (``(tu_name, entity)`` pairs) by ``key_fn(entity)`` and
    fold each group's candidates through *merge_fn*, raising
    :class:`~abicheck.errors.TuMergeError` the moment two candidates from
    *different* TUs for the same key don't merge.

    A single TU's own parser output may legitimately repeat a key (e.g. two
    destructors both falling back to castxml's synthesized no-mangled-name
    marker within the same TU, already tolerated by the flat single-TU
    dump path) -- this is not a cross-TU merge concern, so a candidate
    sharing its accumulator's *own* ``tu_name`` is never passed to
    *merge_fn*; it rides through untouched as an extra entry in the
    returned tuple instead. Only a candidate from a genuinely different TU
    ever participates in an actual merge/conflict check.

    Iteration order over *items* determines both dict insertion order (the
    returned mapping's iteration order, and therefore the caller's final
    tuple order) and the left-to-right fold order within a group; callers
    pass *items* already derived from tu_name-sorted fragments, so both are
    deterministic regardless of the original fragment sequence's order.
    """
    by_key: dict[tuple[str, str], list[tuple[str, _T]]] = {}
    for tu_name, entity in items:
        by_key.setdefault(key_fn(entity), []).append((tu_name, entity))

    merged: dict[tuple[str, str], tuple[_T, ...]] = {}
    for key, candidates in by_key.items():
        acc_tu, acc_entity = candidates[0]
        extras: list[_T] = []
        for tu_name, entity in candidates[1:]:
            if tu_name == acc_tu:
                extras.append(entity)
                continue
            result = merge_fn(acc_entity, entity)
            if result is None:
                kind, name = key
                raise TuMergeError(
                    f"translation units {acc_tu!r} and {tu_name!r} declare "
                    f"incompatible versions of {kind} {name!r} -- ADR-050 "
                    "Phase C's merge only reconciles a forward declaration + "
                    "definition, a plain redeclaration, or a default-"
                    "argument-only difference; this pair disagrees on "
                    "something else (return type, layout, calling "
                    "convention, ...) and cannot be silently resolved.",
                    code=INCONSISTENT_DECLARATION,
                    entity_key=key,
                    tu_names=(acc_tu, tu_name),
                )
            acc_entity = result
            acc_tu = tu_name
        merged[key] = (acc_entity, *extras)
    return merged


def _flatten(grouped: dict[tuple[str, str], tuple[_T, ...]]) -> tuple[_T, ...]:
    return tuple(entity for entities in grouped.values() for entity in entities)


def _merge_scalar_group(
    items: Iterable[tuple[str, str, str]], *, kind: str
) -> dict[str, str]:
    """The typedef/constant analogue of :func:`_merge_group`: a bare
    ``name -> value`` mapping has no "richer declaration" to prefer, so a
    trivial merge only exists when every contributing TU agrees on the
    exact same value; any disagreement is an
    :class:`~abicheck.errors.TuMergeError`.
    """
    by_name: dict[str, list[tuple[str, str]]] = {}
    for tu_name, name, value in items:
        by_name.setdefault(name, []).append((tu_name, value))

    merged: dict[str, str] = {}
    for name, candidates in by_name.items():
        acc_tu, acc_value = candidates[0]
        for tu_name, value in candidates[1:]:
            if value != acc_value:
                raise TuMergeError(
                    f"translation units {acc_tu!r} and {tu_name!r} declare "
                    f"{kind} {name!r} with different values ({acc_value!r} "
                    f"vs {value!r}) -- ADR-050 Phase C cannot reconcile two "
                    "different values for the same name.",
                    code=INCONSISTENT_DECLARATION,
                    entity_key=entity_key(kind, name),
                    tu_names=(acc_tu, tu_name),
                )
        merged[name] = acc_value
    return merged


def _blank_provenance(entity: _T) -> _T:
    """Blank *entity*'s ``source_location``/``source_header``/``origin`` for
    an equality comparison.

    Every one of the four model types this module compares
    (:class:`Function`/:class:`Variable`/:class:`RecordType`/:class:`EnumType`)
    carries these same three provenance fields (ADR-015 schema v6). They
    legitimately differ across TUs for what is otherwise the very same
    declaration -- each TU force-includes its own header file, so a
    genuinely identical redeclaration still has a different
    ``source_location``/``source_header`` per side (e.g. ``"a.h:1"`` vs
    ``"b.h:1"``) purely because of *which* TU parsed it, not because the
    declarations disagree. Comparing them directly would make ADR-050 D4's
    own "declaration + redeclaration" trivial-merge case -- the routine
    shape of a real multi-TU manifest, not an edge case -- spuriously
    conflict on every ordinary cross-TU redeclaration.
    """
    return replace(  # type: ignore[type-var]
        entity, source_location=None, source_header=None, origin=ScopeOrigin.UNKNOWN
    )


def _more_public_of(
    a: _T,
    b: _T,
    *,
    header_segs: list[tuple[str, ...]],
    dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> _T:
    """Pick whichever of *a*/*b* -- two already-confirmed-compatible
    declarations -- should lend its ``source_location``/``source_header``/
    ``origin`` to the merged result.

    A merged declaration carries exactly one ``source_location`` (the model
    has no "seen from N headers" field), and
    :func:`abicheck.provenance.apply_provenance` classifies a declaration's
    public/private ``origin`` from that single field, *after* this merge
    already ran. Defaulting to an arbitrary side (e.g. always the
    tu_name-sorted-first one) is a real correctness gap, not a cosmetic
    choice: if TU ``a`` reaches this declaration only through a private
    header while TU ``b`` reaches the identical declaration through a
    declared *public* one, keeping ``a``'s location would make a genuinely
    public API read as private -- silently hiding a real ABI change from
    public-surface scoping (Codex review, PR #635). When exactly one side
    classifies as ``PUBLIC_HEADER``, that side wins; otherwise *a* (the
    deterministic tu_name-ordered default) is kept, unchanged from before.
    """
    if not have_public_set:
        return a
    origin_a = classify_origin(
        header_from_location(getattr(a, "source_location", None)),
        header_segs,
        dir_segs,
        have_public_set=have_public_set,
    )
    if origin_a == ScopeOrigin.PUBLIC_HEADER:
        return a
    origin_b = classify_origin(
        header_from_location(getattr(b, "source_location", None)),
        header_segs,
        dir_segs,
        have_public_set=have_public_set,
    )
    return b if origin_b == ScopeOrigin.PUBLIC_HEADER else a


def _with_more_public_provenance(
    winner: _T,
    other: _T,
    *,
    header_segs: list[tuple[str, ...]],
    dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> _T:
    """Return *winner* -- the structurally-complete side of a forward-
    declaration/definition merge (:func:`_merge_types`/:func:`_merge_enums`)
    -- with its provenance possibly overridden from *other* (the forward
    declaration) when *other* classifies as more public.

    :func:`_more_public_of` alone isn't enough here: unlike the
    already-identical-modulo-provenance case it's built for, *winner* and
    *other* are structurally different (fields/members differ by
    construction -- that's the whole point of a forward-decl/definition
    pair), so simply calling it and returning whichever side "wins" would
    silently drop the winner's richer structural facts whenever the forward
    declaration happens to be the public one. A public header commonly
    forward-declares a type whose full definition lives only in a private
    implementation header -- keeping the definition's fields/size/members
    is still correct, but the merged entity's *provenance* must reflect the
    public forward declaration, or ``apply_provenance`` reads a genuinely
    public type as private (Codex review, PR #635 follow-up).
    """
    provenance_source = _more_public_of(
        winner,
        other,
        header_segs=header_segs,
        dir_segs=dir_segs,
        have_public_set=have_public_set,
    )
    if provenance_source is winner:
        return winner
    return replace(  # type: ignore[type-var]
        winner,
        source_location=other.source_location,  # type: ignore[attr-defined]
        source_header=other.source_header,  # type: ignore[attr-defined]
        origin=other.origin,  # type: ignore[attr-defined]
    )


def _merge_functions(
    a: Function,
    b: Function,
    *,
    header_segs: list[tuple[str, ...]],
    dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> Function | None:
    """Trivial-merge two same-``entity_key`` :class:`Function` declarations
    -- a plain redeclaration, or a difference confined to one or more
    parameters' ``default`` (ADR-050 D4's "declaration + redeclaration,
    differing only in an added default argument"). Any other difference
    (return type, params other than ``default``, virtuality, ...) is a
    genuine conflict -- **including two different non-``None`` defaults for
    the same parameter** (``f(int=1)`` vs. ``f(int=2)``): that is not "an
    added default argument", it is two TUs disagreeing on the default
    itself, and silently keeping one side would produce an arbitrary
    snapshot rather than surfacing the conflict (Codex review, PR #635).
    """
    if len(a.params) != len(b.params):
        return None
    for pa, pb in zip(a.params, b.params, strict=True):
        if (
            pa.default is not None
            and pb.default is not None
            and pa.default != pb.default
        ):
            return None
    # Parameter *names* are not part of a C/C++ function's type -- `void
    # f(int value);` and `void f(int n);` are the identical declaration,
    # redeclared with cosmetically different names (both castxml and clang
    # preserve whatever the header spells) -- so they're blanked here
    # alongside `default`, the same "not ABI-relevant, don't let it block a
    # routine cross-TU redeclaration" treatment (Codex review, PR #635).
    # The merged declaration still keeps *a*'s own parameter names (via
    # `pa` below), not blanked ones -- only the *comparison* ignores them.
    a_bare = _blank_provenance(
        replace(a, params=[replace(p, name="", default=None) for p in a.params])
    )
    b_bare = _blank_provenance(
        replace(b, params=[replace(p, name="", default=None) for p in b.params])
    )
    if a_bare != b_bare:
        return None
    merged_params: list[Param] = [
        replace(pa, default=pa.default if pa.default is not None else pb.default)
        for pa, pb in zip(a.params, b.params, strict=True)
    ]
    base = _more_public_of(
        a,
        b,
        header_segs=header_segs,
        dir_segs=dir_segs,
        have_public_set=have_public_set,
    )
    return replace(base, params=merged_params)


def _merge_variables(
    a: Variable,
    b: Variable,
    *,
    header_segs: list[tuple[str, ...]],
    dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> Variable | None:
    """Trivial-merge two same-``entity_key`` :class:`Variable` declarations
    -- identical (modulo provenance), or differing only in ``value`` (an
    ``extern`` declaration in one TU paired with the defining, initialized
    redeclaration in another). Two different non-``None`` values is a
    genuine conflict, the variable analogue of :func:`_merge_functions`'s
    conflicting-default-argument check.
    """
    if a.value is not None and b.value is not None and a.value != b.value:
        return None
    a_bare = _blank_provenance(replace(a, value=None))
    b_bare = _blank_provenance(replace(b, value=None))
    if a_bare != b_bare:
        return None
    value = a.value if a.value is not None else b.value
    base = _more_public_of(
        a,
        b,
        header_segs=header_segs,
        dir_segs=dir_segs,
        have_public_set=have_public_set,
    )
    return replace(base, value=value)


def _merge_types(
    a: RecordType,
    b: RecordType,
    *,
    header_segs: list[tuple[str, ...]],
    dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> RecordType | None:
    """Trivial-merge two same-``entity_key`` :class:`RecordType` declarations
    -- ADR-050 D4's canonical "forward-declaration + definition" case:
    ``is_opaque`` (incomplete, no fields) paired with a complete definition
    merges to the definition, **provided the two agree on ``kind``**
    (``struct``/``class``/``union``) -- a `union X;` forward declaration is
    not compatible with a `struct X { ... };` definition even though both
    key on the bare name ``X`` (Codex review, PR #635). The definition's
    structural facts (fields, size, ...) always win, but its *provenance*
    prefers whichever side classifies as public (:func:`_with_more_public_provenance`)
    -- a public header commonly forward-declares a type whose full
    definition lives only in a private implementation header, and the
    merged entity must still read as public. Two complete (non-opaque)
    definitions must be identical (modulo provenance) to merge; if they
    disagree, that is a genuine ODR conflict.
    """
    if a.is_opaque and not b.is_opaque:
        if a.kind != b.kind:
            return None
        return _with_more_public_provenance(
            b,
            a,
            header_segs=header_segs,
            dir_segs=dir_segs,
            have_public_set=have_public_set,
        )
    if b.is_opaque and not a.is_opaque:
        if a.kind != b.kind:
            return None
        return _with_more_public_provenance(
            a,
            b,
            header_segs=header_segs,
            dir_segs=dir_segs,
            have_public_set=have_public_set,
        )
    if _blank_provenance(a) == _blank_provenance(b):
        return _more_public_of(
            a,
            b,
            header_segs=header_segs,
            dir_segs=dir_segs,
            have_public_set=have_public_set,
        )
    return None


def _merge_enums(
    a: EnumType,
    b: EnumType,
    *,
    header_segs: list[tuple[str, ...]],
    dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
) -> EnumType | None:
    """Trivial-merge two same-``entity_key`` :class:`EnumType` declarations
    -- the enum analogue of :func:`_merge_types`: an underlying-type-only
    forward declaration (no ``members``) paired with the full definition
    merges to the definition, **provided the two agree on
    ``underlying_type``/``is_scoped``** -- ``enum E : int;`` is not
    compatible with ``enum E : unsigned { X };`` even though the forward
    declaration has no members to compare (Codex review, PR #635). Like
    :func:`_merge_types`, the definition's ``members`` always win but its
    provenance prefers whichever side is public
    (:func:`_with_more_public_provenance`). Two non-empty member lists must
    agree (modulo provenance) to merge.
    """
    if not a.members and b.members:
        if a.underlying_type != b.underlying_type or a.is_scoped != b.is_scoped:
            return None
        return _with_more_public_provenance(
            b,
            a,
            header_segs=header_segs,
            dir_segs=dir_segs,
            have_public_set=have_public_set,
        )
    if not b.members and a.members:
        if a.underlying_type != b.underlying_type or a.is_scoped != b.is_scoped:
            return None
        return _with_more_public_provenance(
            a,
            b,
            header_segs=header_segs,
            dir_segs=dir_segs,
            have_public_set=have_public_set,
        )
    if _blank_provenance(a) == _blank_provenance(b):
        return _more_public_of(
            a,
            b,
            header_segs=header_segs,
            dir_segs=dir_segs,
            have_public_set=have_public_set,
        )
    return None
