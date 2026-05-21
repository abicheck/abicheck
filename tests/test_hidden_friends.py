"""Tests for HIDDEN_FRIEND_ADDED / HIDDEN_FRIEND_REMOVED.

Synthetic snapshots — no compiler needed. Exercises the
``is_hidden_friend`` flag captured from castxml's ``befriending``
attribute and the diff logic in ``diff_symbols.py``.
"""

from abicheck.checker import compare
from abicheck.checker_policy import (
    ADDITION_KINDS,
    API_BREAK_KINDS,
    ChangeKind,
    Verdict,
)
from abicheck.model import AbiSnapshot, Function, Param, Visibility


def _snap(version: str, functions: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions,
        variables=[],
        types=[],
    )


def _friend_op_eq(
    mangled: str = "_ZN5mylibeqERKNS_5pointES2_",
    is_hidden_friend: bool | None = True,
    visibility: Visibility = Visibility.HIDDEN,
) -> Function:
    return Function(
        name="mylib::operator==",
        mangled=mangled,
        return_type="bool",
        params=[
            Param(name="a", type="const mylib::point&"),
            Param(name="b", type="const mylib::point&"),
        ],
        visibility=visibility,
        is_hidden_friend=is_hidden_friend,
    )


class TestHiddenFriendDetector:
    def test_removed_hidden_friend_is_api_break(self) -> None:
        """An inline hidden friend disappears entirely. visibility=HIDDEN
        (no .so symbol) so the standard FUNC_REMOVED path skips it; the
        dedicated detector must still emit a HIDDEN_FRIEND_REMOVED finding.
        """
        old = _snap("1.0", [_friend_op_eq()])
        new = _snap("2.0", [])
        r = compare(old, new)
        assert any(c.kind == ChangeKind.HIDDEN_FRIEND_REMOVED for c in r.changes)
        assert ChangeKind.HIDDEN_FRIEND_REMOVED in API_BREAK_KINDS
        # No spurious FUNC_REMOVED because visibility was HIDDEN.
        assert not any(c.kind == ChangeKind.FUNC_REMOVED for c in r.changes)
        assert r.verdict == Verdict.API_BREAK

    def test_added_hidden_friend_is_compatible_addition(self) -> None:
        old = _snap("1.0", [])
        new = _snap("2.0", [_friend_op_eq()])
        r = compare(old, new)
        assert any(c.kind == ChangeKind.HIDDEN_FRIEND_ADDED for c in r.changes)
        assert ChangeKind.HIDDEN_FRIEND_ADDED in ADDITION_KINDS

    def test_no_finding_when_friend_unchanged(self) -> None:
        old = _snap("1.0", [_friend_op_eq()])
        new = _snap("2.0", [_friend_op_eq()])
        r = compare(old, new)
        assert not any(
            c.kind in (
                ChangeKind.HIDDEN_FRIEND_ADDED,
                ChangeKind.HIDDEN_FRIEND_REMOVED,
            )
            for c in r.changes
        )

    def test_friend_transition_for_matched_symbol(self) -> None:
        """A function present on both sides flips its friend status — the
        signature-level checker emits the corresponding transition."""
        old = _snap(
            "1.0",
            [_friend_op_eq(is_hidden_friend=False, visibility=Visibility.PUBLIC)],
        )
        new = _snap(
            "2.0",
            [_friend_op_eq(is_hidden_friend=True, visibility=Visibility.PUBLIC)],
        )
        r = compare(old, new)
        assert any(c.kind == ChangeKind.HIDDEN_FRIEND_ADDED for c in r.changes)

    def test_none_on_either_side_suppresses_transition(self) -> None:
        """Tri-state: an unknown ``is_hidden_friend`` on either side must
        not fire the transition detector. This mirrors the same
        Codex-flagged concern that the explicit-ctor detector handles —
        DWARF-only / older snapshots set the field to ``None``.
        """
        old = _snap(
            "1.0",
            [_friend_op_eq(is_hidden_friend=None, visibility=Visibility.PUBLIC)],
        )
        new = _snap(
            "2.0",
            [_friend_op_eq(is_hidden_friend=True, visibility=Visibility.PUBLIC)],
        )
        r = compare(old, new)
        assert not any(
            c.kind in (
                ChangeKind.HIDDEN_FRIEND_ADDED,
                ChangeKind.HIDDEN_FRIEND_REMOVED,
            )
            for c in r.changes
        )

    def test_out_of_line_friend_emits_both_kinds(self) -> None:
        """A hidden friend that was also defined out-of-line (so it has a
        real exported symbol) registers BOTH FUNC_REMOVED (binary-level
        ADL+link break) AND HIDDEN_FRIEND_REMOVED (source-level ADL
        break). These are intentionally complementary findings, per the
        registry impact text."""
        old = _snap(
            "1.0",
            [_friend_op_eq(visibility=Visibility.PUBLIC)],
        )
        new = _snap("2.0", [])
        r = compare(old, new)
        kinds = {c.kind for c in r.changes}
        assert ChangeKind.HIDDEN_FRIEND_REMOVED in kinds
        assert ChangeKind.FUNC_REMOVED in kinds
