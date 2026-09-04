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

"""``directly_referenced_stdlib_type_spellings`` -- split out of
``type_reachability.py`` (over the file-size soft limit) as its own module,
matching that file's own sibling-split precedent
(``type_reachability_spelling.py``).

:func:`directly_referenced_stdlib_types`'s own *identity*-shaped answer,
re-expressed in the spelling a finding's own ``symbol``/``caused_by_type``
actually carries -- see the public function's own docstring below for the
full rationale. Composes :mod:`abicheck.type_reachability_spelling`'s
low-level spelling primitives with :mod:`abicheck.type_reachability`'s own
stdlib-reference scan (:func:`~abicheck.type_reachability.
_run_stdlib_reference_scan`) and merged-typedef view
(:func:`~abicheck.type_reachability._merged_typedefs`); not a documented
public Python API path (absent from ``service.__all__`` and the generated
``python-api-reference.md``), so every caller imports it from this module
directly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from .type_reachability import _merged_typedefs, _run_stdlib_reference_scan
from .type_reachability_spelling import (
    _namespace_suffix_spellings,
    _non_stdlib_signature_spellings,
    _partition_snapshot_types,
    _record_identity,
    _stripped_signature_spelling,
    _typedef_candidate_spellings,
    _typedef_spelling_targets,
)

if TYPE_CHECKING:
    from .model import AbiSnapshot

__all__ = ["directly_referenced_stdlib_type_spellings"]


def _alias_confirmed_identities(
    base: Iterable[str],
    alias_map: Mapping[str, frozenset[str]],
    non_stdlib_spellings: frozenset[str],
) -> frozenset[str]:
    """*base*, plus every identity reached only through a typedef alias where
    at least one producing alias is itself free of a collision against
    *non_stdlib_spellings*.

    A match found via a real declaration's own literal text is always
    trustworthy. A match found only while recursively scanning a typedef's
    *target* string is trustworthy too, but only under that condition -- the
    scan itself cannot judge it, since only the caller computes that
    vocabulary. One genuinely unambiguous route is real proof regardless of
    how many other, separately ambiguous routes reached the same identity.

    Shared by both the exact and the trusted set: the two differ only in
    which scan collections they start from, never in this rule.
    """
    confirmed = set(base)
    for identity, aliases in alias_map.items():
        if aliases - non_stdlib_spellings:
            confirmed.add(identity)
    return frozenset(confirmed)


def _stripped_spelling_index(
    all_stdlib_identities: Iterable[str],
    non_stdlib_spellings: frozenset[str],
    non_stdlib_identities: frozenset[str],
    typedef_candidate_spellings: frozenset[str],
    typedef_spelling_targets: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, int]]:
    """Each stdlib identity's own stripped spelling, and how many identities
    share each such spelling.

    Counts span EVERY stdlib identity the snapshot carries, not just the
    referenced subset -- an unreferenced sibling sharing a referenced
    identity's bare spelling still makes that spelling ambiguous for any
    other finding's bare ``Change.symbol`` to match against.
    """
    stripped_by_identity: dict[str, str] = {}
    stripped_counts: dict[str, int] = {}
    for identity in set(all_stdlib_identities):
        stripped = _stripped_signature_spelling(identity)
        if not stripped or stripped in non_stdlib_spellings:
            continue
        if stripped in typedef_candidate_spellings:
            typedef_target = typedef_spelling_targets.get(stripped)
            # A typedef target naming this exact identity -- spelled fully
            # qualified, or as one of its own *structural* namespace-suffix
            # spellings (`_namespace_suffix_spellings`, which only ever
            # drops a namespace/class-scope prefix `identity` itself
            # already carries) -- genuinely refers to the same entity and
            # is not a collision. An earlier revision instead compared via
            # `_stripped_signature_spelling(typedef_target)` -- rejected
            # (Codex review, fresh evidence): that normalization also
            # strips inline ABI-tag namespaces (`__cxx11::`, `__1::`,
            # `__ndk1::`), so a target naming a *different*, differently
            # ABI-tagged stdlib sibling this snapshot never captured (e.g.
            # a typedef targeting `std::__cxx11::basic_string<char>`
            # against a captured pre-C++11-ABI `std::basic_string<char>`)
            # stripped-collapses to the identical bare form as `identity`
            # purely by coincidence, and was waved through here as "the
            # same identity" when it actually names something else
            # entirely -- confirmed empirically. `_namespace_suffix_
            # spellings` cannot manufacture that coincidence, since it
            # only derives suffixes from `identity`'s own scope chain.
            # A suffix match is itself not safe unqualified: the target
            # string can equal one of `identity`'s own structural suffixes
            # while *also* being the real, distinct identity of an
            # unrelated non-stdlib record/enum captured in this same
            # snapshot (Codex review, fresh evidence -- e.g. a DWARF
            # `std::chrono::duration` alongside an unrelated global
            # `duration` record, with a typedef `chrono::duration ->
            # duration`: the target textually matches `identity`'s own bare
            # suffix, but a real, captured `duration` record is exactly
            # what it actually names). Rejecting whenever the target is
            # itself a captured non-stdlib identity closes this without
            # reopening the ABI-tag coincidence above, since an *exact*
            # match against `identity` is still accepted unconditionally.
            suffix_match = (
                typedef_target in _namespace_suffix_spellings(identity)
                and typedef_target not in non_stdlib_identities
            )
            if typedef_target != identity and not suffix_match:
                # A real, unrelated typedef alias whose key -- or one of
                # its own derived namespace-suffix spellings -- happens to
                # equal this identity's stripped spelling (bare key,
                # namespace-qualified key, or an ambiguous one where
                # `typedef_spelling_targets` itself drops the spelling
                # rather than resolving it, leaving `typedef_target` as
                # `None`, still correctly rejected here) -- the stripped
                # spelling isn't this stdlib identity's own bare backend
                # form at all, it's someone else's (possibly ambiguous)
                # alias, so it can never safely stand in for the identity
                # in a bare Change.symbol match.
                continue
        stripped_by_identity[identity] = stripped
        stripped_counts[stripped] = stripped_counts.get(stripped, 0) + 1
    return stripped_by_identity, stripped_counts


def _assemble_spellings(
    identities: Iterable[str],
    stripped_by_identity: Mapping[str, str],
    stripped_counts: Mapping[str, int],
    exact: frozenset[str],
    trusted: frozenset[str],
) -> frozenset[str]:
    """Which spellings each referenced identity may safely be matched by."""
    spellings: set[str] = set()
    for identity in identities:
        stripped = stripped_by_identity.get(identity)
        if stripped is None or stripped_counts[stripped] > 1:
            # The derived spelling either collides with an unrelated
            # non-stdlib record/enum (`stripped is None`, filtered out
            # above) or with another stdlib identity in the snapshot --
            # referenced or not (`stripped_counts[stripped] > 1`) -- either
            # way, only an identity independently proven via its own
            # literal spelling survives; the derived form itself is never
            # added here.
            if identity in exact:
                spellings.add(identity)
            continue
        if identity not in trusted:
            # The stripped form is unambiguous against everything else in
            # the snapshot -- but that alone only says "nothing else could
            # this spelling mean," not "the signature legitimately reaches
            # this spelling at all." An identity whose own reachability was
            # never proven trustworthy (e.g. found only inside a record
            # that was itself reached solely through an ambiguous typedef
            # alias) cannot be rescued by bare-spelling uniqueness alone.
            # No fallback to `exact` here (unlike the ambiguous-stripped-
            # form branch above): `exact` is always a subset of `trusted`
            # by construction (every site that adds to `_referenced_exact`/
            # `_exact_typedef_aliases` adds the identical identity/origin to
            # `_referenced_trusted`/`_trusted_via_alias` first), so
            # `identity not in trusted` already implies `identity not in
            # exact` -- nothing would ever survive here.
            continue
        # Unambiguous against every stdlib identity in the snapshot *and*
        # against every non-stdlib spelling in it, *and* independently
        # proven trustworthy: keep both the identity and its derived bare
        # form.
        spellings.add(identity)
        spellings.add(stripped)
    return frozenset(spellings)


def directly_referenced_stdlib_type_spellings(
    snapshot: AbiSnapshot,
    *,
    exclude_export_only_roots: bool = False,
    committed_roots: frozenset[str] | None = None,
) -> frozenset[str]:
    """:func:`directly_referenced_stdlib_types`, re-expressed in the spelling
    a finding's own ``symbol``/``caused_by_type`` actually carries, for a
    caller that needs to match against those fields rather than against
    ``RecordType`` identities.

    ``directly_referenced_stdlib_types`` returns each type's *identity* --
    ``qualified_name or name`` (see :func:`_record_identity`), the
    fully-qualified spelling. A ``Change``'s own ``symbol``/``caused_by_type``
    is populated from ``diff_types.py``'s comparison of two ``RecordType``
    entries' own ``name`` fields, which per-backend may be that same
    identity (DWARF bakes the qualified form directly into ``name``) or the
    namespace-prefix-stripped form a signature actually spells it with
    (castxml/direct-clang keep ``name`` bare) -- see
    :func:`_stripped_signature_spelling`'s own docstring for the empirical
    basis. Returning the union of both forms for every identity, rather than
    picking one, means a caller does not have to know which backend
    produced the snapshot it's matching against.

    Contract evaluation's own use case (confirming a layout-change finding
    on a stdlib type a public signature names outright, independent of
    ``surface.py``'s header-origin-scoped ``public_types`` closure, which
    deliberately excludes stdlib types as non-ABI-surface toolchain
    internals) is why this exists as a public, separately-tested function
    rather than an inline transform at the call site: reusing
    :func:`_stripped_signature_spelling` here is what keeps the two stdlib
    spelling normalizations (the one this module's own signature-matching
    index already performs internally, and the one a caller outside this
    module needs) from silently drifting apart.

    A stripped spelling that collides with an unrelated non-stdlib record's
    own signature spelling is dropped, mirroring :func:`_spelling_index`'s
    identical guard (Codex review, fresh evidence): a snapshot can carry its
    own, unrelated ``api::vector<int>`` whose bare signature spelling is the
    same ``"vector<int>"`` a real ``std::vector<int>`` strips to, and a
    ``Change`` on that unrelated user type carries that identical bare
    ``RecordType.name`` as its own ``symbol`` -- so exporting the collided
    spelling here would let contract evaluation confirm a finding about the
    user type using evidence about the unrelated stdlib type. Reuses
    :func:`_non_stdlib_signature_spellings` rather than re-deriving the
    collision set, the same reasoning :func:`_spelling_index` documents for
    its own use of it. The unstripped, fully-qualified ``identity`` is never
    guarded this way (matching :func:`_spelling_index`'s own asymmetry): a
    qualified stdlib spelling colliding with an unrelated type would require
    that type to live in a namespace literally named a stdlib prefix
    (``std::``, ...), which is reserved and not something a real snapshot
    encodes as a legitimate user declaration.

    A stripped spelling shared by **two or more distinct referenced stdlib
    identities** means neither identity's own presence in
    :func:`directly_referenced_stdlib_types`'s return value is independently
    confirmed (Codex review, fresh evidence, two rounds): e.g. a signature
    naming bare ``vector<int>`` cannot distinguish ``std::vector<int>`` from
    ``__gnu_debug::vector<int>``, so that scan correctly marks *both*
    referenced (never missing a real reference is the safe direction for
    its purpose -- deciding whether to keep a layout finding at all) purely
    because each shares the one spelling the signature actually contains,
    not because either was independently matched. A first fix dropped only
    the shared *stripped* spelling itself, reasoning each identity's own
    full qualified spelling was still safe to export -- reproduced wrong:
    a finding whose own ``symbol``/``caused_by_type`` happens to be spelled
    as the *full* qualified form of either ambiguous candidate (e.g. a
    DWARF-derived snapshot, which bakes the qualified spelling directly
    into ``name``) was then confirmed via that unconditionally-exported
    full identity, even though neither identity's reachability was ever
    independently established -- only one of the two, at most, is real, and
    the evidence cannot say which. Closed by excluding **every** spelling
    (stripped and full alike) for every identity in an ambiguous group, not
    only the shared stripped one: this function's own answer for that
    identity is exactly as unproven as the shared spelling that produced
    it. Unlike the non-stdlib collision above, this ambiguity can only be
    resolved among identities this function itself already has in hand, so
    it is computed locally rather than reusing a module-level helper.

    **Per-match-route provenance closes what an earlier revision left as a
    known conservative gap:** grouping identities purely by whether their
    stripped spelling collides with another *referenced* identity's cannot
    distinguish "reached only via the ambiguous shared spelling" from "also
    independently reached via its own unique full spelling elsewhere in the
    same snapshot" -- a whole ambiguous group would otherwise be excluded
    even when one member is separately, unambiguously confirmable another
    way. Closed with real per-identity match provenance:
    :class:`_StdlibReferenceScan` tracks, alongside its flat ``referenced()``
    set, which identities were matched at least once via their own literal,
    un-derived spelling in a real declaration's own text
    (:meth:`_StdlibReferenceScan.referenced_exact`), plus -- separately --
    which were matched only while recursively resolving a typedef's target,
    keyed by which top-level alias led there
    (:meth:`_StdlibReferenceScan.referenced_exact_typedef_aliases`). An
    identity in ``referenced_exact()`` is always kept regardless of any
    collision its *derived* spelling has, since the occurrence that proved
    it is independent of that collision. An identity reached only through a
    typedef alias is trusted the same way as soon as *any* alias that
    produced it is itself free of a collision against this function's own
    enum-aware, typedef-aware non-stdlib vocabulary -- one genuinely
    unambiguous route is real proof regardless of how many other, separately
    ambiguous routes also happened to reach the same identity. Collision
    counting spans every stdlib identity the *snapshot* carries (via
    :func:`_partition_snapshot_types`), not only the referenced subset: an
    unreferenced sibling sharing a referenced identity's bare spelling still
    makes that spelling untrustworthy for an unrelated finding's own
    ``Change.symbol`` to match against. The non-stdlib collision vocabulary
    itself spans both ``snapshot.types`` and ``snapshot.enums`` (a
    non-stdlib record and enum can share a bare backend spelling just as
    easily as two records can), and separately spans every spelling
    ``snapshot.typedefs`` could produce -- exact key or derived suffix,
    resolved or ambiguous alike (:func:`_typedef_candidate_spellings`) --
    since an unrelated typedef's own key can equally collide with a stdlib
    identity's stripped bare form. A typedef whose *resolved* target
    genuinely names the very identity being evaluated (spelled fully
    qualified, or in some other stdlib-namespaced shape reducing to the same
    bare form) is not treated as a collision at all.

    ``exclude_export_only_roots`` is forwarded to
    :func:`_run_stdlib_reference_scan` unchanged (Codex review, fresh
    evidence): contract evaluation's own public-header-domain use must set
    this, since an export-only declaration is exactly the evidence the
    separate ``exports`` domain exists to evaluate, not ``public``'s.

    ``committed_roots`` (default ``None``) mirrors
    ``compare --post-manifest``'s own scoping and is forwarded straight
    through to :func:`_run_stdlib_reference_scan` -- see
    :func:`_is_public_non_stdlib_declaration`'s own docstring for the exact
    membership rule and the gap it closes.

    Exact-match provenance must not depend on declaration order (Codex
    review, fresh evidence: reversing the order of two otherwise-unrelated
    function declarations changed this function's result for the same
    identity between "confirmed" and empty before this fix) -- closed by
    running :func:`_run_stdlib_reference_scan` with ``full_scan=True`` here,
    never for :func:`directly_referenced_stdlib_types` itself, which keeps
    the early exit ("found via any route" is all it needs).
    """
    scan = _run_stdlib_reference_scan(
        snapshot,
        full_scan=True,
        exclude_export_only=exclude_export_only_roots,
        committed_roots=committed_roots,
    )
    if scan is None:
        return frozenset()
    identities = scan.referenced()
    if not identities:
        return frozenset()
    all_stdlib_identities, non_stdlib_record_identities, _ = _partition_snapshot_types(
        snapshot
    )
    non_stdlib_enum_identities = frozenset(
        _record_identity(en.name, en.qualified_name) for en in snapshot.enums
    )
    non_stdlib_identities = non_stdlib_record_identities | non_stdlib_enum_identities
    non_stdlib_spellings = _non_stdlib_signature_spellings(non_stdlib_identities)
    # A match found via a real declaration's own literal text is always
    # trustworthy. A match found only while recursively scanning a typedef's
    # *target* string is trustworthy too, but only when at least one alias
    # that produced it is itself absent from this function's own
    # enum-aware `non_stdlib_spellings` collision set -- the scan itself
    # cannot judge that, since only this function computes that vocabulary.
    exact = _alias_confirmed_identities(
        scan.referenced_exact(),
        scan.referenced_exact_typedef_aliases(),
        non_stdlib_spellings,
    )
    # Broader sibling of `exact` above, mirroring the same alias-ambiguity
    # check but for *any* spelling (self-key or derived) rather than only
    # the self-key one -- needed below for the "stripped form collides with
    # nothing else in the snapshot" shortcut, which is a *different*
    # confirmation route than exactness and must not trust a match whose
    # own reachability was never itself proven trustworthy (Codex review,
    # fresh evidence: a stdlib type named directly in a record's own field,
    # where the record itself was reached only through an ambiguous
    # typedef alias, was previously confirmed unconditionally through this
    # shortcut regardless of that ambiguity -- confirmed empirically).
    trusted = _alias_confirmed_identities(
        scan.referenced_trusted(),
        scan.trusted_via_alias(),
        non_stdlib_spellings,
    )
    # Every spelling a typedef could be reached by -- not just its literal
    # dict key -- via the same suffix-expansion _StdlibReferenceScan itself
    # already uses to resolve a typedef alias to its target: a raw
    # `snapshot.typedefs.get(stripped)` lookup misses a namespace-qualified
    # key like `"mine::vector<int>"` whose *derived* suffix `"vector<int>"`
    # is what actually collides with a stdlib identity's own stripped
    # spelling, since the dict key itself is never equal to that suffix.
    typedef_spelling_targets = _typedef_spelling_targets(
        _merged_typedefs(snapshot), non_stdlib_identities
    )
    # The raw candidate vocabulary, separate from the resolved index above:
    # an ambiguous spelling (two typedefs disagreeing) is dropped from
    # `typedef_spelling_targets` rather than resolved, but it is exactly as
    # untrustworthy as a resolved, disagreeing one for this collision check.
    typedef_candidate_spellings = _typedef_candidate_spellings(
        _merged_typedefs(snapshot), non_stdlib_identities
    )
    # Collision counts are computed over EVERY stdlib identity the snapshot
    # carries, not just the referenced subset -- an unreferenced sibling
    # sharing a referenced identity's bare spelling still makes that
    # spelling ambiguous for any other finding's bare Change.symbol to
    # match against. `set(...)` dedupes physical RecordType entries sharing
    # one identity (an ODR-duplicate/incomplete-declaration pair,
    # `_partition_snapshot_types`'s own list-per-identity reason) so a
    # duplicate doesn't inflate a count against itself.
    stripped_by_identity, stripped_counts = _stripped_spelling_index(
        all_stdlib_identities,
        non_stdlib_spellings,
        non_stdlib_identities,
        typedef_candidate_spellings,
        typedef_spelling_targets,
    )
    return _assemble_spellings(
        identities, stripped_by_identity, stripped_counts, exact, trusted
    )
