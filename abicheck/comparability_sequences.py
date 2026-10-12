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

"""ADR-050 D2 — the ``header_sequence``/``include_sequence`` additive-growth
carve-outs, split out of :mod:`abicheck.comparability` (Codex review, PR
#641 follow-up: the parent module crossed the file-size hard cap once this
round's identity-coordinate-system/duplicate-validation fixes landed).

Scope here is exactly the two ``profile_fields`` sequence carve-outs and
their shared shape validators; :func:`abicheck.comparability.
check_contracts_comparable` imports and composes these with the rest of
the gate's carve-outs (scope, platform-identity, build-context).
"""

from __future__ import annotations

from dataclasses import dataclass

from .comparability_json import (
    _OWNED_HEADER_SINGLE_SENTINEL,
    _OWNED_HEADER_TOKEN_PREFIX,
    _SCOPE_SINGLE_ENTRY_SENTINELS,
    _is_owned_header_pair,
    _is_valid_digest_payload,
    _json_load_list,
    _json_load_str_list,
)

# The only profile_fields key the header-sequence-growth carve-out (PR #641
# follow-up, third round -- Codex review: the F8 scope carve-out's own "pure
# header addition" case unavoidably changes profile_fields["header_sequence"]
# too, since profile_fingerprint tracks declared-header ORDER as a genuine
# extraction-context fact distinct from scope_fingerprint's order-independent
# declared SET -- see compute_extraction_contract's docstring) is allowed to
# waive, and only for growth `_header_sequence_is_scope_confirmed_growth`
# accepts: the new sequence is the old one with scope-confirmed new entries
# added and every EXISTING header keeping its relative order, whether those
# entries trail the old sequence or sort into the middle of it. The waiver was
# trailing-append-only for two releases; see that function's own docstring for
# the measurement that retired the distinction, and for what stays refused (an
# existing header that MOVED, growth the declared surface does not confirm as
# new, and every other profile field).
_HEADER_SEQUENCE_FIELDS = frozenset({"header_sequence"})


def _scope_newly_added_headers(
    old_value: str | None, new_value: str | None
) -> set[str] | None:
    """The set of header identities present in *new_value*
    (``scope_fields["headers"]``) but not *old_value* -- ``None`` if either
    side can't be safely decoded to a real per-header identity list (absent,
    malformed, or collapsed to the ``<single-header>`` sentinel).

    Both sequence carve-outs below must verify not just THAT the scope grew,
    but that the SPECIFIC entries they're waiving correspond to entries
    genuinely new to the declared surface (Codex review, PR #641 follow-up,
    ninth P1): ``_scope_growth_corroborated`` alone only proves the scope
    grew by *some* header, not that it grew by the *same* header the
    sequence carve-out is waiving. For example, old scope ``{a, b, d}``
    (``d`` declared public only, never parsed) with
    sequence ``[a, b]``, growing to new scope ``{a, b, c, d}`` (``c`` newly
    declared) with sequence ``[a, b, d]`` (``d`` now fed to the L2 frontend)
    satisfies scope-growth corroboration (``c`` is new) and the trailing-
    append shape (``d`` appended) independently, but the two have nothing
    to do with each other: ``d``'s content was never parsed on the old
    side, and ``c`` was never parsed on EITHER side, so real removals in
    both would still be invisible. Requiring the sequence carve-outs'
    specific appended entries to be a subset of this newly-added set closes
    that gap.
    """
    if old_value is None or new_value is None:
        return None
    old_list = _json_load_str_list(old_value)
    new_list = _json_load_str_list(new_value)
    if old_list is None or new_list is None:
        return None
    if len(old_list) == 1 and old_list[0] in _SCOPE_SINGLE_ENTRY_SENTINELS:
        return None
    if len(new_list) == 1 and new_list[0] in _SCOPE_SINGLE_ENTRY_SENTINELS:
        return None
    return set(new_list) - set(old_list)


def _header_sequence_is_additive_reorder_free(
    old_value: str | None, new_value: str | None, scope_new_headers: set[str] | None
) -> bool:
    """Whether *new_value* (``profile_fields["header_sequence"]``, a
    json-encoded order-preserving-deduplicated header identity list) is
    *old_value* with new entries appended STRICTLY AFTER it, unchanged --
    never with a new header inserted before or between existing ones, and
    never with an EXISTING header reordered relative to another (Codex
    review, PR #641 follow-up, seventh P1) -- AND every appended entry is
    itself in *scope_new_headers* (:func:`_scope_newly_added_headers`;
    Codex review, PR #641 follow-up, ninth P1): the appended sequence
    entries must correspond to headers genuinely new to the declared
    surface, not merely additive-shaped growth that happens to coincide
    with an unrelated scope addition (see that function's own docstring for
    the concrete scenario this rules out).

    Trailing-append is the only shape that's actually safe: the aggregate
    driver TU the dumper generates parses declared headers sequentially, so
    a new header's macros/pragmas can change how every header parsed AFTER
    it resolves. Merely preserving the *relative* order of the existing
    headers to each other (this function's original, insufficient check)
    is not enough -- ``[a.h, c.h]`` -> ``[a.h, b.h, c.h]`` keeps ``a.h``
    before ``c.h`` on both sides, but ``c.h`` is now parsed with ``b.h``'s
    macros/pragmas already in effect, a genuinely different extraction
    context that can produce a real, non-additive ABI difference despite
    looking like a pure addition. Only requiring every new entry to land
    strictly after the entire unchanged old sequence rules this out: the
    old sequence's own internal parsing context never changes. Declines
    (like :func:`abicheck.comparability._scope_field_is_additive_superset`)
    whenever either side is the ``<single-header>`` sentinel, since there
    is no real order to verify there.

    Also declines whenever *old_list* or *new_list* contains a duplicate
    entry (Codex review, PR #641 follow-up, thirteenth P2): a genuine
    ``header_sequence`` value is always order-preserving-deduplicated by
    construction (see this parameter's own docstring above), so a
    duplicate is never real evidence -- e.g. appending ``"c.h"`` twice
    (``["a.h", "b.h"]`` -> ``["a.h", "b.h", "c.h", "c.h"]``) is malformed.
    Without this check, the final ``set(...) <= scope_new_headers``
    comparison collapses the duplicate away silently, so it would
    otherwise still authorize the waiver instead of failing closed on
    unverifiable evidence.
    """
    if old_value is None or new_value is None:
        return False
    old_list = _json_load_str_list(old_value)
    new_list = _json_load_str_list(new_value)
    if old_list is None or new_list is None:
        return False
    if len(old_list) != len(set(old_list)) or len(new_list) != len(set(new_list)):
        return False
    if len(old_list) == 1 and old_list[0] in _SCOPE_SINGLE_ENTRY_SENTINELS:
        return False
    if len(new_list) == 1 and new_list[0] in _SCOPE_SINGLE_ENTRY_SENTINELS:
        return False
    if len(new_list) < len(old_list):
        return False
    if new_list[: len(old_list)] != old_list:
        return False
    if scope_new_headers is None:
        return False
    return set(new_list[len(old_list) :]) <= scope_new_headers


# The only profile_fields key the include-sequence-owned-growth carve-out
# (PR #641 follow-up, fourth round -- Codex review) is allowed to treat as
# non-fatal: `resolve_inferred_header_roots` (cli_dump_helpers.py) auto-adds
# a header-owning include directory, whose slot token in `include_sequence`
# encodes the declared-header set IT owns (`_slot_token_for_ancestor`) --
# so a pure header addition changes this token too, independently of
# `header_sequence`. The real F8 CLI shape (`-H old=<dir> -H new=<dir>`)
# hits exactly this: both `header_sequence` and `include_sequence` differ
# together, and the header-sequence-growth carve-out alone (which only ever
# considered `differing <= _HEADER_SEQUENCE_FIELDS`) declined to apply
# because `include_sequence` was also in `differing`.
_INCLUDE_SEQUENCE_FIELDS = frozenset({"include_sequence"})


def _slot_indices_match_position(slots: list[str]) -> bool:
    """Whether every ``"<slot-index>:<token>"`` entry in *slots* carries the
    literal decimal index matching its own position in the list (the real
    ``_slot_token_for_ancestor`` construction always emits
    ``f"{idx}:{token}"`` from ``enumerate(declared_includes)``, so a
    genuine ``include_sequence`` value's indices are always exactly
    ``"0"``, ``"1"``, ``"2"``, ... in order) -- EXCEPT for a single
    trailing, unnumbered ``"sys:..."`` entry, which the real construction
    appends for the system-header bucket whenever the depfile contains any
    header outside every declared include root (Codex review, PR #641
    follow-up, tenth P1): unlike the numbered ``label:``/``hdrs:``/``ext:``
    slots, this bucket has no owning ``IncludeDir`` and thus no position of
    its own to number -- it is always the list's last element, if present
    at all. Excluding it from the position check is what the real ``sys:``
    shape actually is, not a loosened restriction: this function's WHOLE
    JOB is verifying a genuine ``include_sequence`` value's real shape, and
    an entry with no index by construction cannot be held to an index
    requirement without treating an ordinary, extremely common production
    output (any header pulled from outside every declared root -- systemd
    headers, the C standard library, ...) as malformed.

    :func:`_include_sequence_is_additive_owned_growth`'s per-slot loop only
    checked that a slot's index was *unchanged* between the old and new
    side (``old_idx != new_idx``), never that the index was itself a real,
    well-formed position rather than an arbitrary string (Codex review, PR
    #641 follow-up, seventh P2): an externally-constructed contract with a
    slot literally named ``"bogus:hdrs:[...]"`` on both sides passed that
    check trivially (``"bogus" == "bogus"``) and could still reach the
    owned-header superset comparison below. Requiring every index to match
    its position closes this without changing the carve-out's own additive
    semantics, since a genuine ``include_sequence`` always satisfies it.

    Also requires each numbered slot to actually carry a ``":"`` delimiter
    AND a recognized token shape (``hdrs:``/``ext:``/``label:``) after it
    (Codex review, PR #641 follow-up, eleventh P2): a malformed slot with
    NO colon at all, e.g. the bare string ``"0"``, still passes a check
    that only compares ``slot.partition(":")[0]`` against the position --
    ``"0".partition(":")[0]`` is ``"0"`` itself, matching position 0 -- and
    if that slot happens to be byte-identical on both sides, the caller's
    per-slot loop's own equality short-circuit (``if old_slot == new_slot:
    continue``) never even looks at it again, so an unchanged malformed
    slot could ride alongside a genuinely growing one and still return
    additive-safe. Real ``include_sequence`` slots produced by
    ``compute_extraction_contract`` always have exactly one of these three
    token prefixes after the delimiter, so requiring one here is the real
    shape, not a new restriction.

    Also requires an ``"ext:"`` slot's (or the trailing ``"sys:"`` entry's)
    payload to be a well-formed :func:`~abicheck.comparability._sha256_of`
    digest (Codex review, PR #641 follow-up, twelfth P2): the previous
    round only checked the ``"ext:"``/``"sys:"`` PREFIX, not that what
    follows it is a genuine digest -- an unchanged malformed payload like
    ``"ext:bogus"`` or ``"sys:not-a-sha256"`` passed that check trivially
    and, exactly like the delimiter-less case above, could ride unexamined
    alongside a genuinely-growing ``"hdrs:"`` slot. ``"label:"`` (arbitrary
    user-supplied text) and ``"hdrs:"`` (its own JSON-shape, validated
    separately) are deliberately excluded from this digest check -- see
    :func:`~abicheck.comparability_json._is_valid_digest_payload`.

    Also requires an ``"hdrs:"`` slot's own payload to actually decode to
    the real owned-header shape -- either the ``<single-header>`` sentinel
    or a JSON list of valid ``(identity, relative_path)`` pairs, with no
    duplicate pair (Codex review, PR #641 follow-up, thirteenth and
    fifteenth P2): the JSON-list-of-pairs shape was previously only decoded
    and validated by :func:`_include_sequence_is_additive_owned_growth`'s
    per-slot loop, and that loop's equality short-circuit (``if old_slot ==
    new_slot: continue``) never reaches an *unchanged* slot -- so a
    malformed, byte-identical payload like ``"0:hdrs:not-json"``, or one
    with a duplicated pair like ``[["x.h", "x.h"], ["x.h", "x.h"]]`` (a
    genuine ``hdrs:`` slot from ``_slot_token_for_ancestor`` is always
    deduplicated), rode alongside a genuinely-growing separate ``"hdrs:"``
    slot completely unexamined, exactly the same unverifiable-evidence gap
    the ``ext:``/``sys:`` digest check above closed for its own token
    shape, and the same duplicate-pair gap
    :func:`_include_sequence_is_additive_owned_growth` closed for a
    *changed* slot's own pair list.
    """
    if slots and slots[-1].startswith("sys:"):
        numbered_slots = slots[:-1]
        if not _is_valid_digest_payload(slots[-1][len("sys:") :]):
            return False
    else:
        numbered_slots = slots
    for i, slot in enumerate(numbered_slots):
        idx, sep, rest = slot.partition(":")
        if sep != ":" or not rest.startswith(("hdrs:", "ext:", "label:")):
            return False
        if idx != str(i):
            return False
        if rest.startswith("ext:") and not _is_valid_digest_payload(
            rest[len("ext:") :]
        ):
            return False
        if (
            rest.startswith(_OWNED_HEADER_TOKEN_PREFIX)
            and rest != _OWNED_HEADER_SINGLE_SENTINEL
            and _owned_header_pairs(rest) is None
        ):
            return False
    return True


def _owned_header_pairs(token_rest: str) -> set[tuple[str, ...]] | None:
    """Decode one owned ``"hdrs:"`` slot token's pair list into a set, or
    ``None`` when the token isn't a decodable owned pair list at all.

    ``None`` covers four distinct "no usable per-header evidence here" cases,
    all of which callers treat identically (fail closed):

    * the ``<single-header>`` sentinel -- no real per-header identity to verify
      a superset against, mirroring the scope-side carve-out's own sentinel
      handling;
    * a non-owned token shape (``ext:``/``label:``) -- a differing one is real,
      unrelated profile drift no carve-out here has business waiving;
    * a payload that isn't a JSON list, or a member that isn't an exact
      two-string list (Codex review, PR #641 follow-up, third P2) -- ``tuple(p)``
      alone doesn't reject a malformed member the way it looks like it should:
      a bare string like ``"xx"`` is itself iterable, so ``tuple("xx")``
      silently succeeds as ``("x", "x")`` instead of raising, letting malformed
      evidence masquerade as a legitimate owned-header pair;
    * a *duplicated* pair (Codex review, PR #641 follow-up, fourteenth P2):
      ``_slot_token_for_ancestor``'s real ``owned`` construction always emits a
      deduplicated pair list, so a duplicate -- e.g. a newly appended
      ``("c.h", "c.h")`` listed twice -- is never genuine evidence, and the set
      conversion here would otherwise silently collapse it away.
    """
    if token_rest == _OWNED_HEADER_SINGLE_SENTINEL or not token_rest.startswith(
        _OWNED_HEADER_TOKEN_PREFIX
    ):
        return None
    pairs = _json_load_list(token_rest[len(_OWNED_HEADER_TOKEN_PREFIX) :])
    if pairs is None or not all(_is_owned_header_pair(p) for p in pairs):
        return None
    pair_tuples = [tuple(p) for p in pairs]
    owned = set(pair_tuples)
    if len(pair_tuples) != len(owned):
        return None
    return owned


def _include_sequence_is_additive_owned_growth(
    old_value: str | None, new_value: str | None, scope_new_headers: set[str] | None
) -> bool:
    """Whether *new_value* (``profile_fields["include_sequence"]``) differs from
    *old_value* only in OWNED ``"hdrs:..."`` slots growing by headers
    *scope_new_headers* confirms as newly declared (Codex review, PR #641
    follow-up, ninth P1) -- the growth-only case (nothing removed) of
    :func:`include_sequence_is_scope_confirmed_evolution`, which owns the
    shape checks: an ``"ext:"``/``"label:"`` slot differing, a
    ``<single-header>`` sentinel, a slot count change, or a malformed slot
    all decline.
    """
    added = frozenset(scope_new_headers or ())
    return include_sequence_is_scope_confirmed_evolution(
        old_value, new_value, HeaderSetDelta(added, frozenset())
    )


def _header_sequence_is_interior_insertion(
    old_value: str | None, new_value: str | None, scope_new_headers: set[str] | None
) -> bool:
    """Whether *new_value* is *old_value* with genuinely-new headers INSERTED,
    preserving every existing header's relative order -- the strictly weaker
    shape :func:`_header_sequence_is_additive_reorder_free` deliberately
    rejects.

    Same guards as that function (both sides decodable, neither holding a
    duplicate, neither collapsed to the ``<single-header>`` sentinel, every
    extra entry itself a scope-confirmed newly-added header), with one
    difference: the old sequence need only be an order-preserving
    *subsequence* of the new one, not its exact prefix. ``[data.h, log.h]``
    -> ``[data.h, json.h, log.h]`` is this shape; ``[a.h, b.h]`` ->
    ``[b.h, a.h]`` is not, and neither is a growth whose extra entries the
    declared *surface* never confirms as new.

    **What this shape establishes, and what it does not.** A trailing append
    proves every existing header's preprocessing context is byte-for-byte
    what it was; an interior insertion proves only that no existing header
    MOVED relative to another -- ``log.h`` is now parsed with ``json.h``'s
    macros/pragmas already in effect, which can in principle change its AST.
    What it does establish is that the divergence is a header ADDITION rather
    than a reordering or an unrelated compile-context drift.

    This docstring used to say the shape "is not a comparability waiver and
    must never be used as one", and for two releases it was not: the
    divergence first refused the comparison outright, then (PR #1274) bounded
    it to a ``fatal=False`` assurance reduction on the declaration/layout
    dimensions. Both priced a hazard this contract does not price anywhere
    else -- a declared header's *content* is not part of
    ``profile_fingerprint`` at all, so an EXISTING header that gains a
    ``#define``/``#pragma pack`` does the same thing to every header after it
    and is compared at full assurance by design. So this predicate is now one
    half of :func:`_header_sequence_is_scope_confirmed_growth`, the waiver the
    ``header_sequence`` carve-out is stated over, and the bounded branch is
    deleted rather than left as a second path. That function's docstring
    carries the full reasoning and the condition under which this would
    genuinely reopen (header content joining the fingerprint --
    ``tests/test_comparability_gate_header_insertion.py``'s
    ``test_an_existing_headers_leaking_edit_is_compared_at_full_assurance``
    fails the day it does).

    Returns False for a pure trailing append too (``new_list[:len(old)] ==
    old_list``): that is :func:`_header_sequence_is_additive_reorder_free`'s
    own shape, and the two predicates partition the growth shapes between
    them so their union has no overlap to make an outcome order-dependent.
    """
    if old_value is None or new_value is None:
        return False
    old_list = _json_load_str_list(old_value)
    new_list = _json_load_str_list(new_value)
    if old_list is None or new_list is None:
        return False
    if len(old_list) != len(set(old_list)) or len(new_list) != len(set(new_list)):
        return False
    if len(old_list) == 1 and old_list[0] in _SCOPE_SINGLE_ENTRY_SENTINELS:
        return False
    if len(new_list) == 1 and new_list[0] in _SCOPE_SINGLE_ENTRY_SENTINELS:
        return False
    if len(new_list) <= len(old_list):
        return False
    if new_list[: len(old_list)] == old_list:
        return False  # a pure trailing append: the full-assurance waiver's shape
    if not set(old_list) <= set(new_list):
        return False
    # Order-preserving subsequence check: every old entry must still appear,
    # in the same relative order, among the new entries.
    it = iter(new_list)
    if not all(entry in it for entry in old_list):
        return False
    if scope_new_headers is None:
        return False
    return (set(new_list) - set(old_list)) <= scope_new_headers


def _header_sequence_is_scope_confirmed_growth(
    old_value: str | None, new_value: str | None, scope_new_headers: set[str] | None
) -> bool:
    """Whether the declared-header sequence grew by nothing but headers the
    declared *surface* independently confirms as new -- whether they landed
    after the old sequence (:func:`_header_sequence_is_additive_reorder_free`)
    or inside it (:func:`_header_sequence_is_interior_insertion`).

    This is the predicate the ``header_sequence`` carve-out is stated in
    terms of, and it deliberately does **not** distinguish the two
    placements. The distinction was load-bearing for one release (PR #1274
    granted a trailing append full assurance and bounded an interior
    insertion to reduced ``declaration``/``layout`` assurance) and is not
    sound, because the hazard it prices -- "``log.h`` is now parsed with
    ``json.h``'s macros/pragmas already in effect" -- is neither specific to
    an insertion nor something this contract prices anywhere else:

    * A declared header's **content** is not part of ``profile_fingerprint``
      at all (:func:`abicheck.comparability_fields._header_identities` keys a
      header by its root-relative path, never its bytes). So an existing
      ``data.h`` that gains a ``#define`` or a ``#pragma pack`` between two
      versions changes the parse context of *every* header after it, and is
      compared at FULL assurance today, by design -- that is what "the
      declared surface's content is the subject of the comparison, not
      evidence about the extraction environment" means. Adding those same
      declarations as a new file instead is the same fact in a different
      shape; pricing one and not the other is an inconsistency, not a
      safeguard.
    * The added header's own position is an incidental product of the
      project's discovered, sorted public-header set -- ``json.h`` sorts
      between ``data.h`` and ``log.h`` -- not an extraction-configuration
      choice anyone made. Making assurance depend on it makes it depend on
      the new header's spelling.
    * The scope axis already treats the very same addition as ordinary
      evolution at full assurance
      (:func:`abicheck.comparability._scope_field_is_additive_superset`).

    What stays refused is unchanged and is what the order fact exists for:
    an EXISTING header that moved relative to another, growth whose extra
    entries the scope fingerprint does not confirm as newly declared, a
    duplicate/sentinel/undecodable sequence, and -- because this predicate
    only ever removes ``header_sequence`` from the caller's working set --
    any other profile field (compiler, standard, macros, target, include
    topology) diverging alongside it.
    """
    return _header_sequence_is_additive_reorder_free(
        old_value, new_value, scope_new_headers
    ) or _header_sequence_is_interior_insertion(old_value, new_value, scope_new_headers)


# ---------------------------------------------------------------------------
# Header-set EVOLUTION (additions and removals), not only growth
# ---------------------------------------------------------------------------
#
# oneDAL 2026.0 deleted 122 public headers. Every waiver above was stated over
# *growth* only, so a release that removed public headers -- flags, defines and
# include topology unchanged -- was refused as "manifest/CLI-flag drift" (exit
# 16), after both sides had already been parsed. A removed public header is
# evidence of an API removal, not of a misconfigured extraction (AGENTS.md
# "Absent is not removed" / "Record before disposing"): the comparison must run
# so the diff engine reports the removed declarations.
#
# What the order/ownership facts guard is unchanged and is now stated for both
# directions: a header the scope declares on BOTH sides but that is parsed on
# only one (re-routed between public-header provenance and ``-H``) is refused,
# since its content would be compared against nothing; an EXISTING header that
# moved relative to another is refused; and every non-header profile field
# (compiler, standard, macros, ``ext:``/``label:`` include slots, the ``sys:``
# bucket, slot topology) still has to match.


@dataclass(frozen=True)
class HeaderSetDelta:
    """The verified header-set change between two ``scope_fields["headers"]``
    values, expressed in ONE identity coordinate system.

    Identities are paths relative to each side's common header root, so a
    removal (or addition) that moves that root respells every surviving
    identity (``"y/b.h"`` -> ``"b.h"``). :func:`scope_header_delta` realigns
    the sides by a directory prefix first; :meth:`canon` maps a side's
    identity into the shared coordinates.

    ``opaque`` marks a delta where a side collapsed to the ``<single-header>``
    sentinel (no per-file identity): added/removed are then unknown, and
    callers accept it only when both sides parsed exactly their declared scope.
    """

    added: frozenset[str]
    removed: frozenset[str]
    old_prefix: str = ""
    new_prefix: str = ""
    opaque: bool = False

    def canon(self, identity: str, *, side: str) -> str:
        return (self.old_prefix if side == "old" else self.new_prefix) + identity


def _dir_prefixes(identities: list[str]) -> set[str]:
    out: set[str] = set()
    for ident in identities:
        parts = ident.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            out.add("/".join(parts[:i]) + "/")
    return out


def _realign(old_list: list[str], new_list: list[str]) -> tuple[str, str]:
    """``(old_prefix, new_prefix)`` putting both lists in one coordinate system:
    empty when they already share an identity (or either is empty); otherwise
    the directory prefix that maximizes shared identities when prepended to the
    side whose root moved deeper. No overlap under any prefix is a complete
    replacement, compared as-is."""
    old_set, new_set = set(old_list), set(new_list)
    if not old_set or not new_set or old_set & new_set:
        return "", ""
    best: tuple[int, str, str] = (0, "", "")
    for prefix in sorted(_dir_prefixes(old_list)):
        hits = len(old_set & {prefix + n for n in new_set})
        if hits > best[0]:
            best = (hits, "", prefix)
    for prefix in sorted(_dir_prefixes(new_list)):
        hits = len(new_set & {prefix + o for o in old_set})
        if hits > best[0]:
            best = (hits, prefix, "")
    return best[1], best[2]


def _decode_identities(value: str | None) -> list[str] | None:
    """A duplicate-free identity list (a sentinel kept verbatim), or ``None``."""
    if value is None:
        return None
    decoded = _json_load_str_list(value)
    if decoded is None or len(decoded) != len(set(decoded)):
        return None
    return decoded


def _is_sentinel(identities: list[str]) -> bool:
    return len(identities) == 1 and identities[0] in _SCOPE_SINGLE_ENTRY_SENTINELS


def scope_header_delta(
    old_value: str | None, new_value: str | None
) -> HeaderSetDelta | None:
    """The verified change between two ``scope_fields["headers"]`` values
    (``"[]"`` for a side declaring no header), or ``None`` when either side is
    absent, malformed, or holds a duplicate."""
    old_list = _decode_identities(old_value)
    new_list = _decode_identities(new_value)
    if old_list is None or new_list is None:
        return None
    if _is_sentinel(old_list) or _is_sentinel(new_list):
        return HeaderSetDelta(frozenset(), frozenset(), opaque=True)
    old_prefix, new_prefix = _realign(old_list, new_list)
    old_set = {old_prefix + o for o in old_list}
    new_set = {new_prefix + n for n in new_list}
    return HeaderSetDelta(
        frozenset(new_set - old_set),
        frozenset(old_set - new_set),
        old_prefix=old_prefix,
        new_prefix=new_prefix,
    )


def _side_is_fully_parsed(scope_value: str | None, sequence_value: str | None) -> bool:
    """Whether one side parsed exactly the headers its scope declares, so no
    declared header can have been re-routed between provenance and ``-H``."""
    scope = _decode_identities(scope_value)
    sequence = _decode_identities(sequence_value)
    return scope is not None and sequence is not None and set(scope) == set(sequence)


def header_sequence_is_scope_confirmed_evolution(
    old_value: str | None,
    new_value: str | None,
    delta: HeaderSetDelta | None,
    *,
    old_scope: str | None = None,
    new_scope: str | None = None,
) -> bool:
    """Whether ``header_sequence`` changed only by headers the declared scope
    independently confirms as added or removed, every surviving header keeping
    its relative order.

    Generalizes :func:`_header_sequence_is_scope_confirmed_growth` (its
    ``removed == {}`` case) to removals: an entry no longer parsed must be one
    the scope removed, an entry newly parsed one the scope added. A header
    still declared on both sides but parsed on only one is neither, and stays
    refused. An *opaque* delta (a ``<single-header>`` side) is accepted only
    when both sides parsed exactly their declared scope (*old_scope*/
    *new_scope*).
    """
    if delta is None:
        return False
    old_list = _decode_identities(old_value)
    new_list = _decode_identities(new_value)
    if old_list is None or new_list is None:
        return False
    if delta.opaque:
        return _side_is_fully_parsed(old_scope, old_value) and _side_is_fully_parsed(
            new_scope, new_value
        )
    if _is_sentinel(old_list) or _is_sentinel(new_list):
        return False
    old_c = [delta.canon(o, side="old") for o in old_list]
    new_c = [delta.canon(n, side="new") for n in new_list]
    old_set, new_set = set(old_c), set(new_c)
    if not (old_set - new_set) <= delta.removed:
        return False
    if not (new_set - old_set) <= delta.added:
        return False
    return [o for o in old_c if o in new_set] == [n for n in new_c if n in old_set]


def _owned_token_evolution_is_confirmed(
    old_rest: str, new_rest: str, delta: HeaderSetDelta
) -> bool:
    """One differing owned ``hdrs:`` token changed only by scope-confirmed
    added/removed headers."""
    if delta.opaque:
        # Only a <single-header> side makes a delta opaque, and that side's
        # owning slot is the sentinel too; the header_sequence check already
        # required both sides to parse exactly their declared scope.
        return _OWNED_HEADER_SINGLE_SENTINEL in (old_rest, new_rest) and all(
            rest == _OWNED_HEADER_SINGLE_SENTINEL
            or _owned_header_pairs(rest) is not None
            for rest in (old_rest, new_rest)
        )
    old_owned = _owned_header_pairs(old_rest)
    new_owned = _owned_header_pairs(new_rest)
    if old_owned is None or new_owned is None:
        return False
    old_c = {(delta.canon(p[0], side="old"), p[1]) for p in old_owned}
    new_c = {(delta.canon(p[0], side="new"), p[1]) for p in new_owned}
    return all(p[0] in delta.removed for p in old_c - new_c) and all(
        p[0] in delta.added for p in new_c - old_c
    )


def _owned_slot_only_holds(rest: str, delta: HeaderSetDelta, *, side: str) -> bool:
    """Whether an owned ``hdrs:`` slot owns nothing but headers the scope
    confirms as removed (``side="old"``) or added (``side="new"``) -- the
    header-owning root ``resolve_inferred_header_roots`` adds exists only
    because those headers do, so it appears or disappears with them."""
    if rest == _OWNED_HEADER_SINGLE_SENTINEL:
        return delta.opaque
    owned = _owned_header_pairs(rest)
    if not owned or delta.opaque:
        return False
    confirmed = delta.removed if side == "old" else delta.added
    return all(delta.canon(p[0], side=side) in confirmed for p in owned)


def _split_slots(slots: list[str]) -> tuple[list[str], str | None]:
    """(numbered slots' tokens without their index, trailing ``sys:`` entry)."""
    if slots and slots[-1].startswith("sys:"):
        return [s.partition(":")[2] for s in slots[:-1]], slots[-1]
    return [s.partition(":")[2] for s in slots], None


def include_sequence_is_scope_confirmed_evolution(
    old_value: str | None, new_value: str | None, delta: HeaderSetDelta | None
) -> bool:
    """Whether ``include_sequence`` differs only in owned ``hdrs:`` slots whose
    owned headers were scope-confirmed added/removed -- the evolution
    generalization of :func:`_include_sequence_is_additive_owned_growth`.

    A slot may appear or disappear only when it owns nothing but
    scope-confirmed added/removed headers (an inferred header root whose whole
    subtree was added/deleted); the remaining slots must then line up in
    order. Every other shape -- an ``ext:``/``label:`` slot differing, the
    ``sys:`` bucket differing, any other slot count or position change, a
    malformed slot -- stays refused: that is include configuration drift, not
    header-set evolution."""
    if old_value is None or new_value is None or delta is None:
        return False
    if old_value == new_value:
        return True
    old_slots = _json_load_str_list(old_value)
    new_slots = _json_load_str_list(new_value)
    if old_slots is None or new_slots is None:
        return False
    if not (
        _slot_indices_match_position(old_slots)
        and _slot_indices_match_position(new_slots)
    ):
        return False
    old_rests, old_sys = _split_slots(old_slots)
    new_rests, new_sys = _split_slots(new_slots)
    if old_sys != new_sys:
        return False
    if len(old_rests) != len(new_rests):
        old_rests = [
            r for r in old_rests if not _owned_slot_only_holds(r, delta, side="old")
        ]
        new_rests = [
            r for r in new_rests if not _owned_slot_only_holds(r, delta, side="new")
        ]
        if len(old_rests) != len(new_rests):
            return False
    return all(
        old_rest == new_rest
        or _owned_token_evolution_is_confirmed(old_rest, new_rest, delta)
        for old_rest, new_rest in zip(old_rests, new_rests, strict=True)
    )
