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
            c.kind
            in (
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
            c.kind
            in (
                ChangeKind.HIDDEN_FRIEND_ADDED,
                ChangeKind.HIDDEN_FRIEND_REMOVED,
            )
            for c in r.changes
        )

    def test_friend_transition_for_inline_only_same_symbol(self) -> None:
        """Regression (Codex review): an inline-only hidden friend (no
        exported symbol on either side, so ``check_hidden_friend_change``'s
        public-symbol pairing never sees it at all) keeps the same mangled
        key across versions but flips ``is_hidden_friend`` — e.g. an
        in-class ``friend`` declaration pulled out to file scope, which
        preserves the mangled name since a hidden friend already mangles
        under its enclosing namespace, not the class. ``diff_inline_hidden_
        friends`` must still catch this via the same-key pair, not just the
        symbol-appears/disappears cases."""
        old = _snap("1.0", [_friend_op_eq(is_hidden_friend=True)])
        new = _snap("2.0", [_friend_op_eq(is_hidden_friend=False)])
        r = compare(old, new)
        assert any(c.kind == ChangeKind.HIDDEN_FRIEND_REMOVED for c in r.changes)

    def test_friend_transition_for_inline_only_same_symbol_added(self) -> None:
        """Symmetric case: the friend is added, not removed, for the same
        inline-only same-mangled-key shape."""
        old = _snap("1.0", [_friend_op_eq(is_hidden_friend=False)])
        new = _snap("2.0", [_friend_op_eq(is_hidden_friend=True)])
        r = compare(old, new)
        assert any(c.kind == ChangeKind.HIDDEN_FRIEND_ADDED for c in r.changes)

    def test_public_pairing_transition_not_duplicated_by_inline_pass(self) -> None:
        """The full-function-map pass (``diff_inline_hidden_friends``) must
        skip a same-key pair already covered by the public-symbol pairing
        (both sides ``PUBLIC``/``ELF_ONLY``) — otherwise the transition
        already tested by ``test_friend_transition_for_matched_symbol``
        would be emitted twice."""
        old = _snap(
            "1.0",
            [_friend_op_eq(is_hidden_friend=False, visibility=Visibility.PUBLIC)],
        )
        new = _snap(
            "2.0",
            [_friend_op_eq(is_hidden_friend=True, visibility=Visibility.PUBLIC)],
        )
        r = compare(old, new)
        kinds = [c.kind for c in r.changes if c.kind == ChangeKind.HIDDEN_FRIEND_ADDED]
        assert len(kinds) == 1

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
