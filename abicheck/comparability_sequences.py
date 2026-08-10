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
# treat as non-fatal, and only when the new sequence extends the old one with
# new entries strictly TRAILING the old sequence, unchanged -- proving every
# EXISTING header's preprocessing context (the headers parsed before it) is
# byte-for-byte identical to before, not merely that existing headers keep
# their relative order to each other.
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
    (``d`` declared via ``--public-header`` only, never parsed) with
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
        ):
            pairs = _json_load_list(rest[len(_OWNED_HEADER_TOKEN_PREFIX) :])
            if pairs is None or not all(_is_owned_header_pair(p) for p in pairs):
                return False
            pair_tuples = [tuple(p) for p in pairs]
            if len(pair_tuples) != len(set(pair_tuples)):
                return False
    return True


def _include_sequence_is_additive_owned_growth(
    old_value: str | None, new_value: str | None, scope_new_headers: set[str] | None
) -> bool:
    """Whether *new_value* (``profile_fields["include_sequence"]``, a
    json-encoded list of ``"<slot-index>:<token>"`` strings) differs from
    *old_value* only in an OWNED-root ``"hdrs:..."`` token growing to
    include newly-added headers, in the same additive-only sense
    :func:`abicheck.comparability._scope_field_is_additive_superset` already
    applies to the scope ``"headers"`` field -- AND every newly-added owned
    pair's global identity (a pair's first element; see
    ``_slot_token_for_ancestor``) is itself in *scope_new_headers*
    (:func:`_scope_newly_added_headers`; Codex review, PR #641 follow-up,
    ninth P1), the same specific-correspondence requirement
    :func:`_header_sequence_is_additive_reorder_free` now applies to
    ``header_sequence`` -- an owned-pair addition that merely looks
    additive can still coincide with an unrelated scope addition rather
    than reflecting the same genuinely-new header (see that function's
    docstring for the concrete scenario).

    Declines (returns False) for any slot whose token isn't the owned
    ``"hdrs:"`` form (an ``"ext:"``/``"label:"`` slot differing is real,
    unrelated profile drift this carve-out has no business waiving), whose
    header list collapsed to the ``<single-header>`` sentinel (no real
    per-header identity to verify a superset against, mirroring the
    scope-side carve-out's own sentinel handling), or whose slot *count*
    changed (a different ``-I``/``--header`` topology, not merely a header
    addition within an existing one).
    """
    if old_value is None or new_value is None:
        return False
    if old_value == new_value:
        return True
    old_slots = _json_load_str_list(old_value)
    new_slots = _json_load_str_list(new_value)
    if old_slots is None or new_slots is None:
        return False
    if len(old_slots) != len(new_slots):
        return False
    if not (
        _slot_indices_match_position(old_slots)
        and _slot_indices_match_position(new_slots)
    ):
        return False
    for old_slot, new_slot in zip(old_slots, new_slots):
        if old_slot == new_slot:
            continue
        old_idx, _, old_rest = old_slot.partition(":")
        new_idx, _, new_rest = new_slot.partition(":")
        if old_idx != new_idx:
            return False
        if _OWNED_HEADER_SINGLE_SENTINEL in (old_rest, new_rest):
            return False
        if not (
            old_rest.startswith(_OWNED_HEADER_TOKEN_PREFIX)
            and new_rest.startswith(_OWNED_HEADER_TOKEN_PREFIX)
        ):
            return False
        old_pairs = _json_load_list(old_rest[len(_OWNED_HEADER_TOKEN_PREFIX) :])
        new_pairs = _json_load_list(new_rest[len(_OWNED_HEADER_TOKEN_PREFIX) :])
        if old_pairs is None or new_pairs is None:
            return False
        # Every owned pair must be an exact two-string list (Codex review,
        # PR #641 follow-up, third P2) -- `tuple(p)` alone doesn't reject a
        # malformed member the way it looks like it should: a bare string
        # like "xx" is itself iterable, so `tuple("xx")` silently succeeds
        # as `("x", "x")` instead of raising, letting malformed evidence
        # masquerade as a legitimate owned-header pair and potentially make
        # a bogus set comparison look like real superset growth.
        if not (
            all(_is_owned_header_pair(p) for p in old_pairs)
            and all(_is_owned_header_pair(p) for p in new_pairs)
        ):
            return False
        # Reject a duplicated pair before the set conversion below collapses
        # it away (Codex review, PR #641 follow-up, fourteenth P2):
        # `_slot_token_for_ancestor`'s real `owned` construction always
        # emits a deduplicated pair list, so a duplicate -- e.g. a newly
        # appended ("c.h", "c.h") listed twice -- is never genuine evidence.
        # `{tuple(p) for p in pairs}` silently discards that duplication,
        # so without this check a malformed, duplicated newly-owned pair
        # would still authorize the waiver instead of failing closed.
        old_tuples = [tuple(p) for p in old_pairs]
        new_tuples = [tuple(p) for p in new_pairs]
        if len(old_tuples) != len(set(old_tuples)) or len(new_tuples) != len(
            set(new_tuples)
        ):
            return False
        old_owned = set(old_tuples)
        new_owned = set(new_tuples)
        if not new_owned >= old_owned:
            return False
        newly_owned = new_owned - old_owned
        if newly_owned:
            if scope_new_headers is None:
                return False
            if not all(pair[0] in scope_new_headers for pair in newly_owned):
                return False
    return True
