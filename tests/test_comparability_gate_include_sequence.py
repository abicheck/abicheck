"""ADR-050 D2 (G32 Phase A, slice 1) -- _include_sequence_is_additive_owned_growth.

Split out of test_comparability_gate.py (Codex review, PR #641 follow-up:
the parent file crossed the file-size hard cap once this round's hdrs:
payload-shape regression tests landed) -- see that file's own module
docstring for the rest of the gate's carve-out tests this one doesn't
repeat.

Scope here is exactly `_include_sequence_is_additive_owned_growth` (PR #641
follow-up, fourth round): resolve_inferred_header_roots (cli_dump_helpers.py)
auto-adds a header-owning include directory, whose slot token in
include_sequence encodes the declared-header set IT owns -- so a pure
header addition changes this field too, independently of header_sequence.
"""

from __future__ import annotations

import json

from abicheck.comparability import (
    _include_sequence_is_additive_owned_growth,
    _sha256_of,
)
from abicheck.comparability_json import _OWNED_HEADER_SINGLE_SENTINEL
from tests._comparability_gate_helpers import _ANY_NEW_HEADERS, _hdrs_slot


def test_include_sequence_additive_owned_growth_true_for_pure_append():
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")])])
    new = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h"), ("c.h", "c.h")])])
    assert _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_true_when_unchanged():
    same = json.dumps([_hdrs_slot(0, [("a.h", "a.h")])])
    assert _include_sequence_is_additive_owned_growth(same, same, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_removal():
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")])])
    new = json.dumps([_hdrs_slot(0, [("a.h", "a.h")])])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_non_owned_slot_drift():
    # An "ext:" (external, non-owned) slot differing is real, unrelated
    # profile drift -- this carve-out has no business waiving it.
    old = json.dumps(["0:ext:" + "a" * 16])
    new = json.dumps(["0:ext:" + "b" * 16])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_slot_count_change():
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h")])])
    new = json.dumps([_hdrs_slot(0, [("a.h", "a.h")]), "1:ext:" + "a" * 16])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_single_header_sentinel():
    old = json.dumps(["0:hdrs:<single-header>"])
    new = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")])])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_declines_on_none():
    some = json.dumps([_hdrs_slot(0, [("a.h", "a.h")])])
    assert not _include_sequence_is_additive_owned_growth(None, some, _ANY_NEW_HEADERS)
    assert not _include_sequence_is_additive_owned_growth(some, None, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_true_when_one_slot_unchanged():
    # Multi-slot case: slot 0 (an external, non-owned dir) is byte-identical
    # on both sides; only slot 1 (the owned root) grows. The per-slot
    # equality short-circuit must let the unchanged slot through without
    # re-parsing it. The "ext:" payload is a real _sha256_of digest shape
    # (Codex review, PR #641 follow-up, twelfth P2 -- payload format is now
    # validated even for unchanged slots).
    ext_slot = "0:ext:" + _sha256_of("dir-contents")
    old = json.dumps([ext_slot, _hdrs_slot(1, [("a.h", "a.h")])])
    new = json.dumps([ext_slot, _hdrs_slot(1, [("a.h", "a.h"), ("b.h", "b.h")])])
    assert _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_index_mismatch():
    # A malformed/reordered slot list (index labels don't line up
    # positionally) can never be safely verified.
    old = json.dumps(['0:hdrs:[["a.h", "a.h"]]'])
    new = json.dumps(['1:hdrs:[["a.h", "a.h"], ["b.h", "b.h"]]'])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_non_positional_index():
    # Codex review, PR #641 follow-up (seventh P2): a slot index that is
    # IDENTICAL on both sides but not a real positional index (the
    # per-slot loop only checks old_idx == new_idx, never that the shared
    # index is actually valid) must still be declined -- otherwise a
    # fabricated slot label lets malformed evidence through as if it were
    # genuine additive owned-header growth.
    old = json.dumps(['bogus:hdrs:[["a.h", "a.h"]]'])
    new = json.dumps(['bogus:hdrs:[["a.h", "a.h"], ["b.h", "b.h"]]'])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_true_with_unchanged_trailing_sys_bucket():
    # Codex review, PR #641 follow-up (tenth P1): the real production
    # construction appends an unnumbered trailing "sys:..." entry for any
    # depfile header outside every declared include root -- the seventh
    # P2 fix's _slot_indices_match_position required EVERY slot's prefix
    # to equal its position, which this "sys:" entry never can (it owns no
    # IncludeDir and thus no position). An unchanged system bucket must
    # not block an otherwise-legitimate owned-header addition. The payload
    # is a real _sha256_of digest shape (Codex review, PR #641 follow-up,
    # twelfth P2 -- payload format is now validated even for unchanged
    # slots).
    sys_bucket = "sys:" + _sha256_of("system-headers")
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")]), sys_bucket])
    new = json.dumps(
        [_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h"), ("c.h", "c.h")]), sys_bucket]
    )
    assert _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_when_sys_bucket_not_trailing():
    # A "sys:"-prefixed entry anywhere but the last position is not the
    # real production shape (the bucket is always appended last, if
    # present) -- must still be declined as an unverifiable position.
    old = json.dumps(["sys:" + "a" * 16, _hdrs_slot(1, [("a.h", "a.h")])])
    new = json.dumps(
        ["sys:" + "a" * 16, _hdrs_slot(1, [("a.h", "a.h"), ("b.h", "b.h")])]
    )
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_unchanged_delimiterless_slot():
    # Codex review, PR #641 follow-up (eleventh P2): a malformed slot with
    # NO ":" delimiter at all, e.g. the bare string "0", still passes
    # slot.partition(":")[0] == str(i) trivially ("0".partition(":")[0] is
    # "0" itself). If that slot happens to be byte-identical on both
    # sides, the per-slot loop's own equality short-circuit never
    # re-examines it, so it could ride alongside a genuinely-growing slot
    # 1 and still return additive-safe. Confirmed by direct repro before
    # any fix: this was accepted as safe growth.
    old = json.dumps(["0", _hdrs_slot(1, [("a.h", "a.h")])])
    new = json.dumps(["0", _hdrs_slot(1, [("a.h", "a.h"), ("b.h", "b.h")])])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_unchanged_malformed_ext_payload():
    # Codex review, PR #641 follow-up (twelfth P2): the delimiter/token-
    # shape fix only checked the "ext:" PREFIX, not that what follows it is
    # a genuine _sha256_of digest. An unchanged malformed payload like
    # "ext:bogus" still passes that check trivially and, exactly like the
    # delimiter-less case above, could ride unexamined alongside a
    # genuinely-growing "hdrs:" slot. Confirmed by direct repro before any
    # fix: this was accepted as safe growth.
    old = json.dumps(["0:ext:bogus", _hdrs_slot(1, [("a.h", "a.h")])])
    new = json.dumps(["0:ext:bogus", _hdrs_slot(1, [("a.h", "a.h"), ("b.h", "b.h")])])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_unchanged_malformed_sys_payload():
    # Same gap as above, for the trailing "sys:" bucket's payload.
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h")]), "sys:not-a-sha256"])
    new = json.dumps(
        [_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")]), "sys:not-a-sha256"]
    )
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_unchanged_malformed_hdrs_payload():
    # Codex review, PR #641 follow-up (thirteenth P2): the "ext:"/"sys:"
    # digest check above only covers those two token shapes -- an "hdrs:"
    # slot's own JSON-list-of-pairs payload was previously only decoded and
    # validated by this function's per-slot loop, which never re-examines
    # an *unchanged* slot (the `if old_slot == new_slot: continue`
    # short-circuit). A malformed, byte-identical payload like
    # "0:hdrs:not-json" rode alongside a genuinely-growing separate "hdrs:"
    # slot completely unexamined. Confirmed by direct repro before any fix:
    # this was accepted as safe growth.
    old = json.dumps(["0:hdrs:not-json", _hdrs_slot(1, [("a.h", "a.h")])])
    new = json.dumps(
        ["0:hdrs:not-json", _hdrs_slot(1, [("a.h", "a.h"), ("b.h", "b.h")])]
    )
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_true_for_unchanged_single_header_sentinel_slot():
    # The <single-header> sentinel is the genuine, valid "hdrs:" shape when
    # there's no per-header identity to track -- it must NOT be rejected as
    # a malformed payload by the new hdrs: JSON-shape check.
    sentinel_slot = "0:" + _OWNED_HEADER_SINGLE_SENTINEL
    old = json.dumps([sentinel_slot, _hdrs_slot(1, [("a.h", "a.h")])])
    new = json.dumps([sentinel_slot, _hdrs_slot(1, [("a.h", "a.h"), ("b.h", "b.h")])])
    assert _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_declines_when_scope_new_headers_is_none():
    # Codex review, PR #641 follow-up (ninth P1): a shape-valid owned-pair
    # growth must still decline when the caller has no verified set of
    # newly-added scope headers to check the newly-owned pair against.
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")])])
    new = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h"), ("c.h", "c.h")])])
    assert not _include_sequence_is_additive_owned_growth(old, new, None)


def test_include_sequence_additive_owned_growth_declines_when_new_pair_not_in_scope_new_headers():
    # Codex review, PR #641 follow-up (ninth P1): the exact scenario --
    # c.h is newly owned in the include_sequence slot, but the scope only
    # grew by d.h (an unrelated header), so this must decline even though
    # the shape itself (owned-pair superset growth) is otherwise valid.
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")])])
    new = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h"), ("c.h", "c.h")])])
    assert not _include_sequence_is_additive_owned_growth(old, new, frozenset({"d.h"}))


def test_include_sequence_additive_owned_growth_true_when_new_pair_matches_scope_new_headers():
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")])])
    new = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h"), ("c.h", "c.h")])])
    assert _include_sequence_is_additive_owned_growth(old, new, frozenset({"c.h"}))


def test_include_sequence_additive_owned_growth_false_for_duplicated_newly_owned_pair():
    # Codex review, PR #641 follow-up (fourteenth P2): `_slot_token_for_
    # ancestor`'s real `owned` construction always emits a deduplicated
    # pair list, so a duplicated newly-appended pair (e.g. "c.h" listed
    # twice) is never genuine evidence. The `{tuple(p) for p in pairs}`
    # set conversion silently collapses that duplication away, so without
    # a duplicate check first, this duplicated evidence still authorized
    # the waiver. Confirmed by direct repro before any fix: this was
    # accepted as safe growth.
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h")])])
    new = json.dumps(
        [f"0:hdrs:{json.dumps([['a.h', 'a.h'], ['c.h', 'c.h'], ['c.h', 'c.h']])}"]
    )
    assert not _include_sequence_is_additive_owned_growth(old, new, frozenset({"c.h"}))


def test_include_sequence_additive_owned_growth_false_for_duplicated_old_pair():
    # Same gap as above, for a duplicate already present on the old side.
    old = json.dumps([f"0:hdrs:{json.dumps([['a.h', 'a.h'], ['a.h', 'a.h']])}"])
    new = json.dumps(
        [f"0:hdrs:{json.dumps([['a.h', 'a.h'], ['a.h', 'a.h'], ['c.h', 'c.h']])}"]
    )
    assert not _include_sequence_is_additive_owned_growth(old, new, frozenset({"c.h"}))


def test_include_sequence_additive_owned_growth_false_for_unchanged_slot_with_duplicate_pairs():
    # Codex review, PR #641 follow-up (fifteenth P2): the duplicate-pair
    # check above lives inside this function's per-slot diff loop, which
    # only reaches a slot whose payload actually DIFFERS between old and
    # new (`if old_slot == new_slot: continue`). An unchanged, malformed
    # slot 0 with a duplicated pair (["x.h", "x.h"] listed twice) rode
    # alongside a genuinely-growing separate slot 1 completely unexamined.
    # Confirmed by direct repro before any fix: this was accepted as safe
    # growth. Fixed in _slot_indices_match_position, which validates every
    # slot (including unchanged ones), not just the per-slot loop.
    malformed_slot0 = "0:hdrs:" + json.dumps([["x.h", "x.h"], ["x.h", "x.h"]])
    old = json.dumps([malformed_slot0, _hdrs_slot(1, [("a.h", "a.h")])])
    new = json.dumps([malformed_slot0, _hdrs_slot(1, [("a.h", "a.h"), ("c.h", "c.h")])])
    assert not _include_sequence_is_additive_owned_growth(old, new, frozenset({"c.h"}))
