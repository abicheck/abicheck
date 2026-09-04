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

"""``OpaqueTypeIndex`` — one snapshot's opaque-type set, in both identity
tiers (ADR-063 Phase 2's post-parse consumer migration).

Placed in ``compare/`` alongside the ``diff_filtering.py`` call site it
serves, not because ``find_opaque_types``' own is_opaque/implementation-
source/by-value-exposure determination is itself a ``compare``-layer
"match old/new entities" question -- ADR-061 would call that a genuine
suppression-eligibility *policy* decision (Codex review on PR #1041).
It stays here anyway, for two independent reasons neither of which this
module can fix on its own: ``diff_filtering.py`` -- the only caller, and
this decision's textbook home per its own stated intent -- sits on a
documented zero-slack no-growth debt pin (``architecture/debt.yaml``),
so moving this logic back there would grow a file that PR was explicit
about not growing; and ``policy/`` -- ADR-061's actual routing-table
target -- is unreachable too, since ``compare/AGENTS.md`` classifies
``diff_filtering.py`` itself as one of ``compare/``'s own legacy paths,
whose permitted-imports rule forbids depending on ``policy/`` at all.
``diff_filtering._downgrade_opaque_type_changes`` is the actual
suppression *application* -- deciding a matched change should be
downgraded -- while :class:`OpaqueTypeIndex` itself (:meth:`intersect`/
:meth:`contains`) is a genuine identity-matching primitive; the
functions below sit in the gap between the two, misplaced by that
measure but not movable without a separate migration of
``diff_filtering.py`` out of ``compare/``'s legacy classification first.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..diff_helpers import depth_aware_bare_name
from ..diff_symbols import _PUBLIC_VIS
from ..model.identity_tiers import (
    SnapshotLocalIdentity,
    StableEntityId,
    snapshot_local_identity,
    stable_entity_id,
)
from ..model.qualified_name_split import (
    enclosing_close_positions,
    skip_template_arguments,
)

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..model import AbiSnapshot, RecordType

__all__ = [
    "OpaqueTypeIndex",
    "find_by_value_types",
    "find_opaque_types",
    "is_impl_source",
]


@dataclass(frozen=True)
class OpaqueTypeIndex:
    """The opaque-type set of one snapshot, in both identity tiers.

    Replaces the bare ``set[str]`` of ``RecordType.name`` this consumer used
    to carry -- the exact site ADR-063 Phase 2 names as a known collision
    ("opaque-type suppression keyed by bare ``RecordType.name``",
    ``diff_filtering._find_opaque_types``). Both tiers are carried, never
    mixed:

    * *stable* -- one
      :class:`~abicheck.model.identity_tiers.StableEntityId` per opaque
      declaration whose producer resolved a cross-snapshot-stable
      ``EntityId``. Empty for a DWARF/PE/Mach-O-only snapshot, where no
      backend resolves one at all.
    * *local* -- one
      :class:`~abicheck.model.identity_tiers.SnapshotLocalIdentity` per
      opaque declaration, keyed on the same ``RecordType.name`` spelling the
      pre-migration ``set[str]`` held, so the string tier's matching
      behavior is bit-for-bit what it was.

    Every opaque declaration contributes to *local*; one additionally
    contributes to *stable* when it has a stable identity. Keeping both is
    what makes :meth:`intersect` and :meth:`contains` a strict *superset* of
    the pre-migration behavior rather than a narrowing one -- **except**
    when :attr:`complete` licenses :meth:`contains`'s ``strict=True`` path
    (ADR-063 Phase 2's bare-name-collision narrowing); see that attribute's
    own docstring for the one case this index does narrow the pre-migration
    behavior in, and why it is provably safe when it does.
    """

    stable: frozenset[StableEntityId]
    local: frozenset[SnapshotLocalIdentity]
    #: For each bare spelling this index holds an opaque declaration under,
    #: the stable ids resolved among just the declaration(s) sharing that
    #: spelling -- the per-spelling breakdown :attr:`stable` itself discards
    #: by flattening every declaration into one snapshot-wide set.
    #: :meth:`intersect` uses this to verify *pairing*, not merely
    #: *presence*, before it may set :attr:`complete`: see that method's own
    #: docstring for the counter-example presence-only completeness misses
    #: (Codex review on PR #1045 -- a first revision of this narrowing
    #: checked only "did every raw declaration resolve *some* stable id",
    #: which does not by itself prove the two sides' ids for the *same*
    #: declaration actually agree). Not meaningful on an already-intersected
    #: index (empty there); only :func:`find_opaque_types`'s own per-snapshot
    #: output populates it.
    stable_by_local: Mapping[SnapshotLocalIdentity, frozenset[StableEntityId]] = field(
        default_factory=dict
    )
    #: Whether narrowing (:meth:`contains`'s ``strict=True``) is safe for
    #: *this* (already-intersected) index. Set only by :meth:`intersect`;
    #: defaults ``True`` on a freshly-built per-snapshot index, since
    #: completeness is a comparison-level (paired) property that a lone
    #: snapshot's own index cannot yet answer -- see :meth:`intersect`.
    complete: bool = True

    def intersect(self, other: OpaqueTypeIndex) -> OpaqueTypeIndex:
        """Per-tier intersection -- a declaration must be opaque on *both*
        sides to suppress. The tiers intersect independently: a type opaque
        on both sides but carrying a stable identity on only one still meets
        in the *local* tier, exactly as the pre-migration string set did.

        **Completeness is computed here, from paired stable coverage --
        never from bare presence.** For every spelling the two sides agree
        is opaque (``self.local & other.local``), narrowing is safe for that
        spelling only when the two sides' *own* stable-id sets for it
        (``stable_by_local``) are *exactly equal* -- proving the two sides
        resolved the identical roster of declarations under that spelling,
        not merely that *some* declaration each did.

        Exact equality, not intersection-is-non-empty: a first revision of
        this check required only that the two sides' stable-id sets for a
        spelling *intersect*, which a real bare-name *collision* itself
        falsifies as a sufficient condition (Codex review on PR #1045,
        second round, fresh evidence). When two distinct declarations
        genuinely share one spelling (``ns1::Handle``, ``ns2::Handle``, both
        opaque, both bare-named ``"Handle"``), each side's set for that
        spelling holds *two* ids -- and if the two sides agree on
        ``ns1::Handle``'s id but disagree on ``ns2::Handle``'s (the same
        producer-scoping disagreement the first round's counter-example
        used, now on only one of the two colliding declarations), the sets
        still *intersect* on the ``ns1`` id alone. An intersection-based
        check would call the whole spelling "paired" and go strict, then
        wrongly treat a stable-tier miss on ``ns2::Handle``'s own genuine,
        still-opaque finding as proof of non-opacity. Only exact set
        equality proves *every* id either side resolved for a spelling has
        a match on the other side too -- which is what a stable-tier miss
        anywhere in this index actually needs to mean "not one of the known
        opaque declarations" to be trustworthy.

        The first round's own counter-example (one declaration, disagreeing
        ids) is the *single-element* case of this same check: ``{id_old} ==
        {id_new}`` is ``False`` whenever the ids disagree, identically to
        the earlier intersection-based answer for that narrower shape --
        this revision only changes the *multi-declaration* case the first
        one got wrong.

        **Equal-and-empty does not count as paired.** ``frozenset() ==
        frozenset()`` is ``True``, but "neither side resolved any stable id
        for this spelling" is not evidence the two sides agree on
        anything -- it means there is no stable-tier evidence for this
        spelling *at all*, so a change belonging to it can only ever be
        correctly matched through the spelling tier. Counting it as paired
        would license a global ``strict=True`` that then rejects such a
        change on the (contentless) miss instead of falling through, which
        is exactly the ``test_a_change_carrying_a_stable_id_still_falls_
        back_to_its_spelling`` regression a first version of this ``==``
        revision introduced. Each key therefore also requires its shared
        set to be non-empty. ``all()`` over an empty spelling set (``local``
        itself empty) is still vacuously ``True`` -- no shared spelling
        means nothing for narrowing to get wrong.
        """
        local = self.local & other.local
        paired = all(
            self.stable_by_local.get(key, frozenset())
            == other.stable_by_local.get(key, frozenset())
            != frozenset()
            for key in local
        )
        return OpaqueTypeIndex(
            stable=self.stable & other.stable,
            local=local,
            complete=paired,
        )

    def __bool__(self) -> bool:
        return bool(self.stable or self.local)

    def contains(self, change: Change, spelling: str, *, strict: bool = False) -> bool:
        """Whether *change* names an opaque declaration.

        Stable tier first: when the change carries a cross-snapshot-stable
        ``EntityId`` this index holds, the declaration is *proven* to be the
        opaque one, regardless of how either side rendered its display
        spelling (a qualified ``Change.symbol`` against a bare
        ``RecordType.name`` misses under a string compare).

        *strict* (default ``False``, the pre-existing, always-safe
        behavior): whether a stable-tier *miss* -- the change carries a
        resolvable stable identity, but it is not one of this index's known
        opaque declarations -- may stop here rather than falling back to the
        spelling tier. A caller may only pass ``strict=True`` when it has
        independently established :attr:`complete` for the very index being
        queried (``diff_filtering._downgrade_opaque_type_changes`` is the
        one caller that does); passing it unconditionally would treat a
        merely-incomplete index as if a miss were proof of non-opacity,
        silently dropping a real suppression whenever the two sides'
        producers disagree about whether an identity was resolved at all.

        When the change carries no resolvable stable identity at all
        (``stable_entity_id(change.entity_id) is None``), *strict* has no
        effect -- there is no miss to be strict about, so this always falls
        through to the spelling tier, which is the one shape of the bare-
        name collision this index still cannot close (see
        ``diff_filtering._downgrade_opaque_type_changes``'s own docstring).
        """
        stable = stable_entity_id(change.entity_id)
        if stable is not None:
            if stable in self.stable:
                return True
            if strict:
                return False
        return snapshot_local_identity(spelling) in self.local


_IMPL_EXTENSIONS = frozenset({".c", ".cc", ".cpp", ".cxx", ".c++", ".m", ".mm"})


def is_impl_source(source_location: str | None) -> bool:
    """Check if a source_location path refers to an implementation file."""
    if not source_location:
        return False
    # source_location may be "foo.c:42" — strip line number
    path = source_location.split(":")[0] if ":" in source_location else source_location
    # Get file extension
    dot = path.rfind(".")
    if dot < 0:
        return False
    ext = path[dot:].lower()
    return ext in _IMPL_EXTENSIONS


def find_opaque_types(snap: AbiSnapshot) -> OpaqueTypeIndex:
    """Find types that are opaque to consumers.

    A type is opaque when:

    1. castxml marks it as ``incomplete`` (``is_opaque=True``) — the public
       header has only a forward declaration, OR
    2. The type definition is in an implementation file (.c/.cpp) AND all
       public-API references use pointers (never by value).  This handles
       DWARF mode where castxml is not used but DWARF's ``DW_AT_decl_file``
       reveals the type is implementation-private.

    Returns a two-tier :class:`~abicheck.compare.opaque_types.
    OpaqueTypeIndex`, not a ``set[str]``. Rule 2's by-value check
    (:func:`find_by_value_types`) still runs over ``RecordType.name``
    spellings against rendered signature text -- a spelling question, not
    an identity one, and ``EntityId`` has nothing to contribute to it --
    but now also tries the name's unqualified leaf spelling alongside the
    full one (see :func:`_type_is_by_value_referenced`), since a
    qualification mismatch there used to be silently absorbed by an equally
    spelling-based join and no longer is once :class:`OpaqueTypeIndex`'s
    stable tier can join across exactly that mismatch. Only the *result* is
    re-expressed as identities.
    """
    opaque: set[str] = set()
    declarations: dict[str, list[RecordType]] = {}

    for t in snap.types:
        if t.is_opaque or is_impl_source(t.source_location):
            # In the `is_impl_source` case the type is defined in an
            # implementation file — only consider it opaque if all API
            # references are through pointers (rule 2, resolved below).
            opaque.add(t.name)
            declarations.setdefault(t.name, []).append(t)

    if not opaque:
        return OpaqueTypeIndex(stable=frozenset(), local=frozenset())

    by_value_types = find_by_value_types(snap, opaque)
    surviving = opaque - by_value_types

    stable: set[StableEntityId] = set()
    local: set[SnapshotLocalIdentity] = set()
    stable_by_local: dict[SnapshotLocalIdentity, set[StableEntityId]] = {}
    for name in surviving:
        for t in declarations[name]:
            key = snapshot_local_identity(name, t.entity_id)
            local.add(key)
            resolved = stable_entity_id(t.entity_id)
            if resolved is not None:
                stable.add(resolved)
                stable_by_local.setdefault(key, set()).add(resolved)
    return OpaqueTypeIndex(
        stable=frozenset(stable),
        local=frozenset(local),
        stable_by_local={k: frozenset(v) for k, v in stable_by_local.items()},
    )


#: Matches whitespace or a leading cv-qualifier keyword, repeated -- the
#: filler :func:`_occurrence_is_indirect` skips between a matched type-name
#: occurrence and whatever declarator sigil (or lack of one) follows it, so
#: ``"Handle *const"``/``"Handle const *"`` (either qualifier order, either
#: spacing) both still find the ``*``.
_CV_OR_SPACE_RE = re.compile(r"(?:\s+|\bconst\b|\bvolatile\b)*")


def _type_token_matches(spelling: str, text: str) -> Iterator[re.Match[str]]:
    """Every occurrence of *spelling* in *text* as a whole type-name token,
    never as part of a longer identifier.

    A plain ``spelling in text`` substring test (this scan's original
    behavior) matches ``Handle`` inside an unrelated ``OtherHandle`` just as
    readily as inside a real ``ns::Handle`` reference -- harmless for a
    full, already-qualified name (a C/C++ identifier can never be a
    substring of another one without an intervening non-identifier
    character on both sides, so this predicate changes nothing for that
    candidate), but a real false-positive risk for the *leaf* spelling
    :func:`_type_is_by_value_referenced` widens the scan with (Codex review
    on PR #1041): matching ``Handle`` inside ``OtherHandle`` would wrongly mark
    the genuinely opaque ``ns::Handle`` as by-value exposed, dropping it
    from both identity tiers and reporting a private layout change as
    breaking. Boundaries are ``\\w``/non-``\\w`` only, via ``re``'s own
    zero-width lookaround.

    This alone does **not** stop a leaf spelling from matching inside a
    *different* qualified reference (``other::Handle`` still contains a
    token-bounded ``Handle``, since ``::`` is non-word on both sides) -- see
    :func:`_unqualified_type_token_matches`, which the leaf candidate uses
    instead, for that closing half. Yields every ``re.Match`` (not just the
    first, and not a bool) so :func:`_type_is_by_value_referenced` can
    classify indirection relative to *each* occurrence independently --
    ``Pair<Handle*, Handle>`` has one indirect and one by-value occurrence
    of ``Handle``, and stopping at the first match alone missed the second
    (Codex review on PR #1041, follow-up round)."""
    pattern = r"(?<!\w)" + re.escape(spelling) + r"(?!\w)"
    return re.finditer(pattern, text)


def _unqualified_type_token_matches(
    spelling: str, text: str
) -> Iterator[re.Match[str]]:
    """As :func:`_type_token_matches`, plus refusing a match immediately
    preceded by ``::`` -- i.e. *spelling* must appear bare, never as the
    trailing segment of a different qualified name.

    This is what the *leaf* candidate in :func:`_type_is_by_value_referenced`
    scans with, instead of the plain token match: ``ns::Handle``'s leaf
    fallback ``Handle`` matching a real ``other::Handle`` reference would
    wrongly treat an unrelated scope's declaration as exposing ``ns::Handle``,
    dropping the genuinely opaque type out of both identity tiers and
    reporting its private layout change as breaking (Codex review on
    PR #1041, following up on the embedded-substring case
    :func:`_type_token_matches` alone closes). The full, already-qualified
    candidate is unaffected -- it is never scanned through this function --
    so a genuine ``ns::Handle`` reference still matches regardless of what
    precedes it."""
    pattern = r"(?<!\w)(?<!:)" + re.escape(spelling) + r"(?!\w)"
    return re.finditer(pattern, text)


#: A C/C++ declarator-grouping paren whose own content opens with a
#: pointer/reference sigil -- the parens exist purely to override normal
#: declarator precedence (an array/function suffix binds tighter than a
#: bare ``*`` would), so ``"Handle (*)[3]"`` (pointer to an array of
#: ``Handle``) and ``"Handle (*)(int)"`` (pointer to a function returning
#: ``Handle``) are both genuinely indirect even though the ``*``/``&``
#: itself sits inside a paren rather than immediately after the type name
#: (Codex review on PR #1041, follow-up round). An optional leading
#: ``Class::``-qualified scope covers the pointer-to-member spelling too
#: (``"Handle (Class::*)[3]"``).
_DECLARATOR_GROUP_RE = re.compile(r"\(\s*(?:\w+(?:::\w+)*::\s*)?[*&]")


def _sigil_follows(text: str, pos: int) -> bool:
    """Whether *text* has a ``*``/``&`` at *pos* -- either directly, after
    skipping whitespace and a leading cv-qualifier keyword
    (:data:`_CV_OR_SPACE_RE`), so ``"Handle *const"``/``"Handle const *"``
    (either cv-qualifier order or spacing) both still find the ``*`` -- or
    wrapped in a declarator-grouping paren (:data:`_DECLARATOR_GROUP_RE`)
    immediately following, so ``"Handle (*)[3]"``'s pointer-to-array
    declarator is found too."""
    m = _CV_OR_SPACE_RE.match(text, pos)
    pos = m.end() if m else pos
    if pos < len(text) and text[pos] in "*&":
        return True
    return _DECLARATOR_GROUP_RE.match(text, pos) is not None


def _occurrence_is_indirect(text: str, end: int) -> bool:
    """Whether a matched type-name occurrence is passed by pointer/
    reference rather than by value, checking the occurrence's own
    declarator sigil first and then, if none, every *enclosing*
    declarator's sigil outward to true top level.

    Occurrence-relative, not a whole-text scan (Codex review on PR #1041,
    fresh evidence beyond every prior template-argument-nesting fix): for
    an implementation record named ``ns::Handle`` referenced through a
    public function-pointer parameter/return like ``"void (*)(Handle*)"``,
    the matched leaf occurrence ``Handle`` sits immediately before its own
    ``*`` -- but that ``*`` lives inside the function-pointer's own nested
    parameter-list parens, which every prior whole-text scan (tracking
    ``(...)``/``[...]``/``<...>``/quoted-literal nesting to decide whether
    *any* sigil counts) correctly ignored as belonging to a *different*
    part of the declarator, wrongly treating ``ns::Handle`` as exposed by
    value. A sigil elsewhere in the rendered text -- inside an unrelated
    nested declarator, a different template argument, or a separate
    parameter -- has never said anything about how the *matched*
    occurrence itself is declared; checking only what applies to that
    occurrence itself is what actually answers that question.

    The occurrence's own template arguments (if the matched name is
    immediately followed by ``<``) must be skipped as one unit first,
    though, via :func:`~abicheck.model.qualified_name_split.
    skip_template_arguments` -- not just the bare identifier -- since a
    genuine top-level indirection can sit only after they close:
    ``"Box<void (*)()> *"``'s trailing ``*`` makes ``Box`` a pointer, but
    it comes after the whole ``<void (*)()>``, not right after ``"Box"``
    itself.

    A sigil applying to an *enclosing* declarator protects a nested
    occurrence too, not only the occurrence's own immediate sigil (Codex
    review on PR #1041, follow-up round): ``Pair<Handle>*``'s ``Handle``
    is itself followed only by ``>``, but the enclosing ``Pair<...>``'s
    own trailing ``*`` means a consumer of that pointer never needs to
    know ``Handle``'s layout either -- they never construct or copy a
    ``Pair<Handle>`` by value, only ever hold a pointer to one.
    :func:`~abicheck.model.qualified_name_split.enclosing_close_positions`
    walks every bracket enclosing the occurrence, innermost first, so each
    enclosing level's own trailing sigil is checked in turn."""
    if _sigil_follows(text, skip_template_arguments(text, end)):
        return True
    return any(
        _sigil_follows(text, enclosing_end)
        for enclosing_end in enclosing_close_positions(text, end)
    )


def _type_is_by_value_referenced(tname: str, text: str) -> bool:
    """Whether *text* references *tname* as a by-value type, trying both
    the full name and (when applicable) its unqualified leaf spelling, and
    classifying indirection relative to each occurrence that matched (see
    :func:`_occurrence_is_indirect`) -- *any* by-value occurrence of either
    candidate is enough to count as exposed.

    *tname* is ``RecordType.name`` -- which may be qualified
    (``"ns::Handle"``) even when the signature text this function scans
    renders the identical type unqualified (``"Handle"``, when the
    reference sits inside the same namespace, or when the producer's own
    signature renderer simply drops a redundant qualifier). A plain
    ``tname in rendered_text`` substring test misses that case entirely --
    a real gap this function has always had, made consequential rather than
    cosmetic once :class:`~abicheck.compare.opaque_types.OpaqueTypeIndex`'s
    stable tier can reliably join the two sides' declarations across
    exactly that same qualification mismatch (Codex review on PR #1041):
    a by-value exposure this scan fails to see leaves the type wrongly
    ``opaque``, and the stable tier then suppresses the resulting finding
    with no spelling mismatch left to (accidentally) save it.

    The full name is checked via :func:`_type_token_matches`; an unqualified
    leaf spelling (the segment after the last *depth-zero* ``"::"`` -- see
    :func:`~abicheck.diff_helpers.depth_aware_bare_name`, since a naive
    ``rsplit("::", 1)`` would cut inside a qualified template argument
    instead of at the real scope boundary, e.g. extracting ``"Tag>"`` out
    of ``"api::Wrapper<dep::Tag>"`` rather than ``"Wrapper<dep::Tag>"`` --
    Codex review on PR #1041, follow-up round; tried only when the leaf
    differs from the full name -- an already-bare name gets no second
    candidate) is checked via the *stricter*
    :func:`_unqualified_type_token_matches`, which additionally refuses a
    match immediately preceded by ``::`` -- otherwise the leaf widening
    would treat a real, separately-scoped ``other::Handle`` reference as
    exposing ``ns::Handle`` too.

    **Documented, still-open gap** (Codex review on PR #1041, follow-up
    round): rejecting a preceding ``::`` closes the *qualified*-collision
    case, but a *bare* same-leaf reference in a genuinely different scope
    (e.g. a function declared inside namespace ``other`` whose own
    unqualified ``Handle`` parameter really means ``other::Handle``) still
    matches ``ns::Handle``'s leaf candidate -- there is no textual marker
    left to reject on, since the reference carries no qualifier at all. This
    is the exposure-detection mirror of the identical, already-accepted
    bare-name-collision limitation on the *suppression* side (see
    ``TestKnownGapStaysDocumented`` in ``tests/test_opaque_identity_tiers.py``)
    -- both are the same underlying limitation of matching by rendered
    spelling instead of by an entity's actual owning scope, which is
    exactly what ``EntityId``-based identity resolution exists to close
    properly, not a heuristic patch here. See
    ``test_find_by_value_types_leaf_widening_bare_reference_in_another_scope_is_a_documented_gap``
    in ``tests/test_cov95_diff_filtering.py`` for the pinned reproduction.

    Each regex scan is gated on a plain substring check first (``tname in
    text`` / ``leaf in text``) -- a token match always contains its own
    candidate as a substring, so this is behavior-preserving, and it skips
    the regex entirely for the common miss case on a snapshot with many
    opaque candidates and few actual references (CodeRabbit review on
    PR #1041)."""
    matched = False
    if tname in text:
        for m in _type_token_matches(tname, text):
            matched = True
            if not _occurrence_is_indirect(text, m.end()):
                return True
    if matched or "::" not in tname:
        return False
    leaf = depth_aware_bare_name(tname)
    if not leaf or leaf == tname:
        return False
    if leaf in text:
        for m in _unqualified_type_token_matches(leaf, text):
            if not _occurrence_is_indirect(text, m.end()):
                return True
    return False


def find_by_value_types(snap: AbiSnapshot, opaque: set[str]) -> set[str]:
    """Return the subset of *opaque* types that any public function/variable uses by value."""
    by_value_types: set[str] = set()
    for func in snap.functions:
        if func.visibility not in _PUBLIC_VIS:
            continue
        rt = func.return_type.strip()
        for tname in opaque:
            if tname in by_value_types:
                continue
            if _type_is_by_value_referenced(tname, rt):
                by_value_types.add(tname)
        for param in func.params:
            pt = param.type.strip()
            for tname in opaque:
                if tname in by_value_types:
                    continue
                if _type_is_by_value_referenced(tname, pt) and param.pointer_depth == 0:
                    by_value_types.add(tname)
    # Also check variables — a public variable of this type means it's by-value
    for var in snap.variables:
        if var.visibility not in _PUBLIC_VIS:
            continue
        vt = var.type.strip()
        for tname in opaque:
            if tname in by_value_types:
                continue
            if _type_is_by_value_referenced(tname, vt):
                by_value_types.add(tname)
    return by_value_types
