"""Regression coverage for AbiSnapshot.typedefs' bare-name collision.

``AbiSnapshot.typedefs`` is keyed by *bare* (unqualified) alias name on both
header backends, so two unrelated member typedefs sharing a bare spelling in
different classes (e.g. ``X::impl_value_t`` on many unrelated classes -- an
ordinary STL-container-shaped pattern) collapse onto one dict entry. Diffing
that collapsed dict directly can flip the surviving entry's recorded value
when an unrelated class gains or loses its own same-named alias, fabricating
a spurious ``TYPEDEF_BASE_CHANGED`` for a typedef that never itself changed.

``AbiSnapshot.typedefs_qualified`` (schema v25) carries the same set of
typedef declarations keyed by qualified name instead, which is unique per
declaration. ``diff_types._diff_typedefs`` should prefer it whenever both
sides populate it.
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, compare
from abicheck.model import AbiSnapshot


def _snap(typedefs, typedefs_qualified):
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        typedefs=typedefs,
        typedefs_qualified=typedefs_qualified,
    )


class TestTypedefQualifiedCollision:
    def test_new_colliding_alias_does_not_fabricate_a_base_change(self):
        """Adding an unrelated class with a same-named member typedef must
        not report the pre-existing typedef's underlying type as changed,
        just because the two happen to collapse onto the same bare key.
        """
        old = _snap(
            typedefs={"impl_value_t": "shared_ptr<A_impl>"},
            typedefs_qualified={"A::impl_value_t": "shared_ptr<A_impl>"},
        )
        new = _snap(
            # The bare dict collapses to whichever entry the backend visited
            # last -- here the newly-added, unrelated B::impl_value_t wins,
            # exactly the collision this fix guards against.
            typedefs={"impl_value_t": "shared_ptr<B_impl>"},
            typedefs_qualified={
                "A::impl_value_t": "shared_ptr<A_impl>",
                "B::impl_value_t": "shared_ptr<B_impl>",
            },
        )
        r = compare(old, new)
        kinds = {c.kind for c in r.changes}
        assert ChangeKind.TYPEDEF_BASE_CHANGED not in kinds
        assert ChangeKind.TYPEDEF_REMOVED not in kinds

    def test_genuine_qualified_change_is_still_reported(self):
        old = _snap(
            typedefs={"impl_value_t": "shared_ptr<A_impl>"},
            typedefs_qualified={"A::impl_value_t": "shared_ptr<A_impl>"},
        )
        new = _snap(
            typedefs={"impl_value_t": "shared_ptr<A_impl2>"},
            typedefs_qualified={"A::impl_value_t": "shared_ptr<A_impl2>"},
        )
        r = compare(old, new)
        changed = [c for c in r.changes if c.kind == ChangeKind.TYPEDEF_BASE_CHANGED]
        assert len(changed) == 1
        assert changed[0].symbol == "A::impl_value_t"

    def test_genuine_qualified_removal_is_still_reported(self):
        # Both sides still carry an (unrelated) qualified typedef so the
        # qualified-key diff path stays active -- an entirely typedef-free
        # new side is indistinguishable from "not populated" and legitimately
        # falls back to the legacy bare-key path (see the docstring above).
        old = _snap(
            typedefs={
                "impl_value_t": "shared_ptr<A_impl>",
                "other": "int",
            },
            typedefs_qualified={
                "A::impl_value_t": "shared_ptr<A_impl>",
                "other": "int",
            },
        )
        new = _snap(
            typedefs={"other": "int"},
            typedefs_qualified={"other": "int"},
        )
        r = compare(old, new)
        removed = [c for c in r.changes if c.kind == ChangeKind.TYPEDEF_REMOVED]
        assert len(removed) == 1
        assert removed[0].symbol == "A::impl_value_t"

    def test_all_qualified_typedefs_removed_are_each_reported(self):
        """When every typedef is removed, the new side's typedefs_qualified
        is legitimately empty -- indistinguishable, by non-emptiness alone,
        from "field not populated". Both colliding old-side declarations must
        still be reported individually rather than collapsing to whichever
        one the legacy bare map happened to retain (Codex review)."""
        old = _snap(
            typedefs={"impl_value_t": "shared_ptr<B_impl>"},
            typedefs_qualified={
                "A::impl_value_t": "shared_ptr<A_impl>",
                "B::impl_value_t": "shared_ptr<B_impl>",
            },
        )
        new = _snap(typedefs={}, typedefs_qualified={})
        r = compare(old, new)
        removed = {
            c.symbol for c in r.changes if c.kind == ChangeKind.TYPEDEF_REMOVED
        }
        assert removed == {"A::impl_value_t", "B::impl_value_t"}

    def test_legacy_bare_only_snapshot_is_unaffected(self):
        """Neither side populates typedefs_qualified (DWARF-only / legacy
        schema) -- diffing must fall back to the pre-existing bare-key
        behavior unchanged."""
        old = _snap(typedefs={"Size": "unsigned int"}, typedefs_qualified={})
        new = _snap(typedefs={"Size": "unsigned long"}, typedefs_qualified={})
        r = compare(old, new)
        assert ChangeKind.TYPEDEF_BASE_CHANGED in {c.kind for c in r.changes}
