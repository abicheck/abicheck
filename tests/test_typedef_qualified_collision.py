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
from abicheck.model import AbiSnapshot, Function, Visibility


def _snap(typedefs, typedefs_qualified, functions=None):
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        typedefs=typedefs,
        typedefs_qualified=typedefs_qualified,
        functions=functions or [],
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
        # The emitted symbol stays the bare alias -- matching how a header
        # backend spells a *reference* to the typedef in a function
        # signature -- even though matching used the qualified key
        # internally (Codex review: a qualified symbol here would silently
        # break diff_filtering._enrich_affected_symbols' bare-name matching).
        assert changed[0].symbol == "impl_value_t"

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
        assert removed[0].symbol == "impl_value_t"

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
        removed = [c for c in r.changes if c.kind == ChangeKind.TYPEDEF_REMOVED]
        # Both collide on the same emitted (bare) symbol, but each
        # declaration's own removal is still reported -- not collapsed to
        # one, which was the bug (see the module docstring and the bare-vs-
        # qualified matching note on test_genuine_qualified_change_is_still_
        # reported above).
        assert len(removed) == 2
        assert {c.symbol for c in removed} == {"impl_value_t"}
        assert {c.old_value for c in removed} == {
            "shared_ptr<A_impl>",
            "shared_ptr<B_impl>",
        }

    def test_qualified_diff_still_attributes_to_the_bare_referencing_function(self):
        """A namespaced typedef change must still be attributed to the
        exported function that names it -- but a header dumper spells a
        *reference* to a typedef bare (``ns::Alias`` is written as ``Alias``
        in a function's own return/param type, matching castxml's ``Typedef``
        branch), so the emitted Change.symbol must stay bare too, even while
        old/new matching uses the qualified key internally (Codex review:
        `diff_filtering._enrich_affected_symbols`' substring match against
        function signatures would otherwise silently stop finding the
        namespaced typedef's own reference).
        """
        old = _snap(
            typedefs={"impl_value_t": "shared_ptr<A_impl>"},
            typedefs_qualified={"A::impl_value_t": "shared_ptr<A_impl>"},
            functions=[
                Function(
                    name="use_impl",
                    mangled="use_impl",
                    return_type="impl_value_t",
                    params=[],
                    visibility=Visibility.PUBLIC,
                )
            ],
        )
        new = _snap(
            typedefs={"impl_value_t": "shared_ptr<A_impl2>"},
            typedefs_qualified={"A::impl_value_t": "shared_ptr<A_impl2>"},
        )
        r = compare(old, new)
        changed = [c for c in r.changes if c.kind == ChangeKind.TYPEDEF_BASE_CHANGED]
        assert len(changed) == 1
        assert changed[0].symbol == "impl_value_t"
        assert changed[0].affected_symbols == ["use_impl"]

    def test_legacy_bare_only_snapshot_is_unaffected(self):
        """Neither side populates typedefs_qualified (DWARF-only / legacy
        schema) -- diffing must fall back to the pre-existing bare-key
        behavior unchanged."""
        old = _snap(typedefs={"Size": "unsigned int"}, typedefs_qualified={})
        new = _snap(typedefs={"Size": "unsigned long"}, typedefs_qualified={})
        r = compare(old, new)
        assert ChangeKind.TYPEDEF_BASE_CHANGED in {c.kind for c in r.changes}
