"""P1: vtable reordering severity (abicc #66).

Explicit severity test: TYPE_VTABLE_CHANGED must be in BREAKING_KINDS.
Tests the relationship between vtable changes and BREAKING verdict.

This supplements the existing TestVtableReorderingSeverity in test_issues_e1_e4.py
with more granular severity-focused tests.
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.checker_policy import BREAKING_KINDS
from abicheck.diff_cxx_rules import _owner_descends_from, vtable_slot_is_override_reuse
from abicheck.model import AbiSnapshot, Fact, Function, Param, RecordType


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = dict(library="lib.so", version="1.0")
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


class TestVtableSeverity:
    """TYPE_VTABLE_CHANGED must always be BREAKING (abicc #66)."""

    def test_type_vtable_changed_in_breaking_kinds(self) -> None:
        """TYPE_VTABLE_CHANGED must be in BREAKING_KINDS set."""
        assert ChangeKind.TYPE_VTABLE_CHANGED in BREAKING_KINDS

    def test_vtable_reorder_verdict_breaking(self) -> None:
        """Reordering vtable entries → BREAKING verdict."""
        old = _snap(
            types=[
                RecordType(
                    name="Base",
                    kind="class",
                    vtable=["_ZN4Base4drawEv", "_ZN4Base6resizeEv"],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Base",
                    kind="class",
                    vtable=["_ZN4Base6resizeEv", "_ZN4Base4drawEv"],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.TYPE_VTABLE_CHANGED in {c.kind for c in result.changes}
        assert result.verdict == Verdict.BREAKING

    def test_vtable_entry_removed_is_breaking(self) -> None:
        """Removing a vtable entry → BREAKING."""
        old = _snap(
            types=[
                RecordType(
                    name="Widget",
                    kind="class",
                    vtable=["_ZN6Widget4drawEv", "_ZN6Widget5paintEv"],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Widget",
                    kind="class",
                    vtable=["_ZN6Widget4drawEv"],
                )
            ]
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPE_VTABLE_CHANGED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_vtable_entry_added_is_breaking(self) -> None:
        """Adding a vtable entry shifts indices of subsequent entries → BREAKING."""
        old = _snap(
            types=[
                RecordType(
                    name="Widget",
                    kind="class",
                    vtable=["_ZN6Widget4drawEv"],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Widget",
                    kind="class",
                    vtable=["_ZN6Widget4drawEv", "_ZN6Widget5paintEv"],
                )
            ]
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPE_VTABLE_CHANGED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_vtable_unchanged_no_change(self) -> None:
        """Identical vtable → no TYPE_VTABLE_CHANGED emitted."""
        old = _snap(
            types=[
                RecordType(
                    name="Engine",
                    kind="class",
                    vtable=["_ZN6Engine4initEv", "_ZN6Engine3runEv"],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Engine",
                    kind="class",
                    vtable=["_ZN6Engine4initEv", "_ZN6Engine3runEv"],
                )
            ]
        )
        result = compare(old, new)
        assert not result.changes

    def test_vtable_change_kind_value(self) -> None:
        """TYPE_VTABLE_CHANGED enum value is 'type_vtable_changed'."""
        assert ChangeKind.TYPE_VTABLE_CHANGED.value == "type_vtable_changed"


class TestVtableOverrideSlotReuse:
    """case185: an override that reuses its base's slot must not fire
    TYPE_VTABLE_CHANGED, even though the slot's mangled entry renames from
    base to derived. Mirrors diff_cxx_rules.virtual_method_addition()'s own
    exemption for the identical relationship
    (diff_cxx_rules.vtable_slot_is_override_reuse)."""

    def test_same_signature_override_reusing_slot_is_not_vtable_changed(self) -> None:
        old = _snap(
            types=[
                RecordType(
                    name="Derived",
                    kind="class",
                    bases=["Base"],
                    vtable=["_ZN4Base5paintEi"],
                )
            ],
            functions=[
                Function(
                    name="Base::paint",
                    mangled="_ZN4Base5paintEi",
                    return_type="int",
                    params=[Param(name="x", type="int")],
                    is_virtual=True,
                )
            ],
        )
        new = _snap(
            types=[
                RecordType(
                    name="Derived",
                    kind="class",
                    bases=["Base"],
                    vtable=["_ZN7Derived5paintEi"],
                )
            ],
            functions=[
                Function(
                    name="Derived::paint",
                    mangled="_ZN7Derived5paintEi",
                    return_type="int",
                    params=[Param(name="x", type="int")],
                    is_virtual=True,
                )
            ],
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPE_VTABLE_CHANGED not in kinds

    def test_partial_bases_evidence_still_recognises_override_reuse(self) -> None:
        """ADR-063 Phase 5B (Codex review on the same PR): the exact scenario
        the review flagged -- a `PARTIAL` (not `NOT_COLLECTED`) `bases_fact`
        that still carries its one known entry (`["Base"]`) must resolve
        through `vtable_slot_is_override_reuse` exactly as a `PRESENT` one
        would, at the real `vtable_slot_is_override_reuse` call site rather
        than only at `_owner_descends_from` directly."""
        old_funcs = {
            "_ZN4Base5paintEi": Function(
                name="Base::paint",
                mangled="_ZN4Base5paintEi",
                return_type="int",
                params=[Param(name="x", type="int")],
                is_virtual=True,
            )
        }
        new_funcs = {
            "_ZN7Derived5paintEi": Function(
                name="Derived::paint",
                mangled="_ZN7Derived5paintEi",
                return_type="int",
                params=[Param(name="x", type="int")],
                is_virtual=True,
            )
        }
        new_types = {
            "Derived": RecordType(
                name="Derived",
                kind="class",
                bases_fact=Fact.partial(["Base"]),
            )
        }
        assert vtable_slot_is_override_reuse(
            "_ZN4Base5paintEi",
            "_ZN7Derived5paintEi",
            old_funcs,
            new_funcs,
            {},
            new_types,
        )

    def test_different_signature_same_name_still_fires(self) -> None:
        """The negative twin: same method name but a different parameter
        list has no matching virtual_signature_key, so it's a genuine new
        slot, not a reuse -- must still be reported."""
        old = _snap(
            types=[
                RecordType(
                    name="Derived",
                    kind="class",
                    bases=["Base"],
                    vtable=["_ZN4Base5paintEi"],
                )
            ],
            functions=[
                Function(
                    name="Base::paint",
                    mangled="_ZN4Base5paintEi",
                    return_type="int",
                    params=[Param(name="x", type="int")],
                    is_virtual=True,
                )
            ],
        )
        new = _snap(
            types=[
                RecordType(
                    name="Derived",
                    kind="class",
                    bases=["Base"],
                    vtable=["_ZN4Base5paintEi", "_ZN7Derived5paintEd"],
                )
            ],
            functions=[
                Function(
                    name="Base::paint",
                    mangled="_ZN4Base5paintEi",
                    return_type="int",
                    params=[Param(name="x", type="int")],
                    is_virtual=True,
                ),
                Function(
                    name="Derived::paint",
                    mangled="_ZN7Derived5paintEd",
                    return_type="int",
                    params=[Param(name="x", type="double")],
                    is_virtual=True,
                ),
            ],
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPE_VTABLE_CHANGED in kinds

    def test_same_signature_unrelated_owner_not_treated_as_reuse(self) -> None:
        """A signature match alone must not suppress the change: a vtable
        entry swapping to a same-signature virtual from a class that is NOT
        in Derived's hierarchy is a genuine, unrelated slot replacement, not
        an override-reuse. Guards against a same-name/params collision
        between two unrelated hierarchies falsely reading as compatible.

        Verified at the helper level rather than via full compare(): this
        scenario (one symbol removed, one added, nothing else) is also where
        diff_filtering.py's unrelated add/remove-pair dedup independently
        collapses the end-to-end output before a suppressed-or-not
        TYPE_VTABLE_CHANGED would be observable either way, so a
        compare()-level assertion wouldn't isolate this specific guard.
        """
        old_funcs = {
            "_ZN4Base5paintEi": Function(
                name="Base::paint",
                mangled="_ZN4Base5paintEi",
                return_type="int",
                params=[Param(name="x", type="int")],
                is_virtual=True,
            )
        }
        new_funcs = {
            "_ZN6Other5paintEi": Function(
                name="Other::paint",
                mangled="_ZN6Other5paintEi",
                return_type="int",
                params=[Param(name="x", type="int")],
                is_virtual=True,
            )
        }
        assert not vtable_slot_is_override_reuse(
            "_ZN4Base5paintEi",
            "_ZN6Other5paintEi",
            old_funcs,
            new_funcs,
            {},
            {},
        )

    def test_sibling_base_same_signature_not_treated_as_reuse(self) -> None:
        """Both owners can independently sit somewhere in the diffed class's
        base set without one genuinely overriding the other: a class with
        sibling bases (Derived : Base1, Base2), or one whose base list itself
        changed (Derived : Base1 -> Derived : Base2), could have a slot swap
        from Base1::foo() to an unrelated, same-signature Base2::foo()
        without either being an override of the other. Base2 does not
        descend from Base1, so this must not be treated as a reuse.
        """
        old_funcs = {
            "_ZN5Base14fooEv": Function(
                name="Base1::foo",
                mangled="_ZN5Base14fooEv",
                return_type="void",
                is_virtual=True,
            )
        }
        new_funcs = {
            "_ZN5Base24fooEv": Function(
                name="Base2::foo",
                mangled="_ZN5Base24fooEv",
                return_type="void",
                is_virtual=True,
            )
        }
        old_types = {
            "Derived": RecordType(
                name="Derived",
                kind="class",
                bases=["Base1"],
                vtable=["_ZN5Base14fooEv"],
            )
        }
        new_types = {
            "Derived": RecordType(
                name="Derived",
                kind="class",
                bases=["Base2"],
                vtable=["_ZN5Base24fooEv"],
            )
        }
        assert not vtable_slot_is_override_reuse(
            "_ZN5Base14fooEv",
            "_ZN5Base24fooEv",
            old_funcs,
            new_funcs,
            old_types,
            new_types,
        )

    def test_identical_slot_entry_is_trivially_a_reuse(self) -> None:
        """The old_entry == new_entry fast path: an unchanged slot is
        trivially a 'reuse' (nothing to suppress a real change for)."""
        assert vtable_slot_is_override_reuse(
            "_ZN4Base5paintEi",
            "_ZN4Base5paintEi",
            {},
            {},
            {},
            {},
        )

    def test_different_signature_returns_false_directly(self) -> None:
        """virtual_signature_key mismatch short-circuits to False, independent
        of owner/hierarchy -- exercised directly since a differing vtable
        length (as in the compare()-level negative-twin test) never reaches
        this helper at all (_diff_type_vtable only calls it when both
        vtables are the same length)."""
        old_funcs = {
            "_ZN4Base5paintEi": Function(
                name="Base::paint",
                mangled="_ZN4Base5paintEi",
                return_type="int",
                params=[Param(name="x", type="int")],
                is_virtual=True,
            )
        }
        new_funcs = {
            "_ZN7Derived5paintEd": Function(
                name="Derived::paint",
                mangled="_ZN7Derived5paintEd",
                return_type="int",
                params=[Param(name="x", type="double")],
                is_virtual=True,
            )
        }
        assert not vtable_slot_is_override_reuse(
            "_ZN4Base5paintEi",
            "_ZN7Derived5paintEd",
            old_funcs,
            new_funcs,
            {},
            {},
        )

    def test_unresolvable_owner_returns_false(self) -> None:
        """A Function whose owner can't be determined (no '::' in its name
        and an unparseable mangled symbol) must not be treated as a reuse --
        there is nothing to verify an override edge against."""
        old_funcs = {
            "paint": Function(
                name="paint",
                mangled="not_a_mangled_name",
                return_type="int",
                params=[Param(name="x", type="int")],
                is_virtual=True,
            )
        }
        new_funcs = {
            "paint2": Function(
                name="paint",
                mangled="also_not_mangled",
                return_type="int",
                params=[Param(name="x", type="int")],
                is_virtual=True,
            )
        }
        assert not vtable_slot_is_override_reuse(
            "paint",
            "paint2",
            old_funcs,
            new_funcs,
            {},
            {},
        )


class TestOwnerDescendsFrom:
    """Direct coverage of diff_cxx_rules._owner_descends_from()'s branches."""

    def test_owner_equals_ancestor(self) -> None:
        assert _owner_descends_from("Base", "Base", {})

    def test_leaf_names_match_across_qualification(self) -> None:
        """A qualified owner and a bare-leaf ancestor with the same leaf
        component are treated as the same class (CastXML records bases as
        bare leaves; DWARF records the qualified form)."""
        assert _owner_descends_from("ns::Base", "Base", {})

    def test_unrelated_leaf_and_unresolvable_type_returns_false(self) -> None:
        assert not _owner_descends_from("Other", "Base", {})

    def test_qualified_ancestor_exact_match_in_bases_list(self) -> None:
        """DWARF records base lists with their fully-qualified spelling
        (unlike CastXML's leaf-only lists), so a qualified ``ancestor`` that
        matches a ``bases`` entry exactly is unambiguous and trusted
        directly, without needing the leaf-based corroboration fallback."""
        types = {
            "ns::Derived": RecordType(
                name="ns::Derived", kind="class", bases=["ns::Base"]
            ),
        }
        assert _owner_descends_from("ns::Derived", "ns::Base", types)

    def test_partial_bases_evidence_is_still_trusted_for_a_known_entry(self) -> None:
        """ADR-063 Phase 5B (Codex review on the same PR): `_owner_descends_from`
        reads `_transitive_bases`'s *set* of names only and discards its
        completeness flag entirely -- its own evidence-gap handling is
        scoped to the separate vtable/vptr_offset_bits slice. A `PARTIAL`
        `bases_fact` still carries real, known entries (the uncovered part
        of the scope is merely *unknown*, not the covered part being wrong),
        so a `PARTIAL`-evidenced `Derived -> Base` relationship must resolve
        here exactly as a `PRESENT` one would -- losing it would make
        `vtable_slot_is_override_reuse` (this function's own caller) return
        `False` for a real override and fabricate a `TYPE_VTABLE_CHANGED`."""
        types = {
            "ns::Derived": RecordType(
                name="ns::Derived",
                kind="class",
                bases_fact=Fact.partial(["ns::Base"]),
            ),
        }
        assert _owner_descends_from("ns::Derived", "ns::Base", types)

    def test_both_qualified_same_leaf_different_namespace_returns_false(self) -> None:
        """ns1::Base and ns2::Base share a leaf but are unrelated classes in
        different namespaces -- both sides are already fully qualified, so
        this is not the castxml-leaf-only ambiguity the leaf fallback exists
        for, and must not be treated as the same class."""
        assert not _owner_descends_from("ns2::Base", "ns1::Base", {})

    def test_bare_leaf_not_trusted_when_qualified_side_has_own_record(self) -> None:
        """A bare global name (``Base``) and a namespaced one (``ns::Base``)
        share a leaf, but if ``ns::Base`` resolves to its own type record,
        that proves this snapshot retains namespace fidelity -- the bare
        name is then provably a different, unrelated class (it isn't
        ``ns::Base``'s own base either), not the same class recorded two
        ways, and must not be treated as equal."""
        types = {"ns::Base": RecordType(name="ns::Base", kind="class")}
        assert not _owner_descends_from("ns::Base", "Base", types)

    def test_bare_leaf_not_trusted_when_qualified_side_has_own_record_via_qualified_name(
        self,
    ) -> None:
        """Same scenario as above, but shaped the way castxml snapshots
        actually store it: ``RecordType.name`` stays bare ("Base") and the
        namespaced spelling lives in the separate ``qualified_name`` field
        (model.py), so ``types`` is keyed by the bare name, not "ns::Base".
        The corroboration check must still find that record via
        ``qualified_name``, not just an exact-key lookup that can never
        match a namespaced string against a bare-keyed dict."""
        types = {
            "Base": RecordType(name="Base", qualified_name="ns::Base", kind="class")
        }
        assert not _owner_descends_from("ns::Base", "Base", types)

    def test_leaf_only_base_list_not_trusted_against_disambiguated_ancestor(
        self,
    ) -> None:
        """owner (``ns2::Derived``) declares a bare leaf-only base (``Base``,
        as CastXML would record it) -- but if BOTH ``ns1::Base`` and
        ``ns2::Base`` have their own resolvable qualified records elsewhere
        in this (mixed DWARF/header) snapshot, that bare ``Base`` entry
        can't be assumed to mean one specific one of them. Testing against
        the unrelated ``ns1::Base`` must not succeed just because its leaf
        happens to match."""
        types = {
            "ns2::Derived": RecordType(
                name="ns2::Derived", kind="class", bases=["Base"]
            ),
            "ns1::Base": RecordType(name="ns1::Base", kind="class"),
            "ns2::Base": RecordType(name="ns2::Base", kind="class"),
        }
        assert not _owner_descends_from("ns2::Derived", "ns1::Base", types)

    def test_bare_ancestor_not_trusted_against_leaf_only_base_with_namespaced_alternative(
        self,
    ) -> None:
        """owner (``ns::Derived``) declares a bare leaf-only base (``Base``,
        as CastXML would record it) -- its true base is the namespaced
        ``ns::Base`` (recorded via ``qualified_name``, castxml's own-name
        stays bare per model.py), not the unrelated global ``Base``.
        Testing the real global ``Base`` (itself bare, so the exact-match
        fast path can't apply either) against that leaf-only entry must not
        succeed just because both happen to be spelled "Base" -- the
        presence of a distinctly-qualified ``ns::Base`` record proves the
        leaf is ambiguous in this snapshot."""
        types = {
            "Derived": RecordType(
                name="Derived",
                qualified_name="ns::Derived",
                kind="class",
                bases=["Base"],
            ),
            "Base": RecordType(name="Base", qualified_name="ns::Base", kind="class"),
        }
        assert not _owner_descends_from("ns::Derived", "Base", types)

    def test_bare_ancestor_not_trusted_against_leaf_only_base_dwarf_shaped(
        self,
    ) -> None:
        """Same ambiguity as the CastXML-shaped test above, but shaped the
        way DWARF snapshots store it: ``dwarf_snapshot.py`` records
        ``RecordType.name`` as the already-qualified spelling itself
        (``qualified_name`` stays unset), while inheritance edges still keep
        only the base DIE's leaf name. The competing ``ns::Base`` record must
        still be found via ``name``, not just ``qualified_name`` -- checking
        only one field would miss whichever backend produced it."""
        types = {
            "ns::Derived": RecordType(name="ns::Derived", kind="class", bases=["Base"]),
            "ns::Base": RecordType(name="ns::Base", kind="class"),
        }
        assert not _owner_descends_from("ns::Derived", "Base", types)

    def test_owner_sharing_ancestor_leaf_is_not_treated_as_own_alternative(
        self,
    ) -> None:
        """A class that inherits from a global type sharing its own leaf
        (``namespace ns { struct Base : ::Base { ... }; }``, DWARF-shaped:
        the owner's own ``name`` is ``ns::Base``, its base list still bare
        ``Base``) is a valid, unambiguous inheritance -- the owner record's
        own qualified identity must not be mistaken for a *competing*
        alternative to itself, or a genuine override-slot reuse gets
        reported as a spurious ``TYPE_VTABLE_CHANGED``."""
        types = {
            "ns::Base": RecordType(name="ns::Base", kind="class", bases=["Base"]),
        }
        assert _owner_descends_from("ns::Base", "Base", types)


class TestClangVtableFactsReliabilityGate:
    """G31 Phase C, Codex review (fresh evidence): a persisted, pre-v21
    direct-clang snapshot's ``RecordType.vtable``/``vptr_offset_bits`` are
    unconditionally empty/None for EVERY record -- real but WRONG data for
    an already-polymorphic class, not merely absent -- so comparing it
    against a fresh dump of the SAME, unchanged headers must not read as
    every polymorphic class gaining its first vptr.
    """

    def _legacy_clang_record(self) -> RecordType:
        # Shape a pre-v21 direct-clang backend actually produced: vtable=[]
        # and vptr_offset_bits=None regardless of the class's real
        # polymorphism, since the reconstruction this flag guards didn't
        # exist yet.
        return RecordType(name="A", kind="class", vtable=[], vptr_offset_bits=None)

    def _fresh_clang_record(self) -> RecordType:
        # The same class, dumped with the fixed backend: genuinely
        # polymorphic, vtable/vptr populated for real.
        return RecordType(
            name="A", kind="class", vtable=["_ZN1A1fEv"], vptr_offset_bits=0
        )

    def test_reproduces_without_the_flag(self) -> None:
        """Establishes the bug is real absent the fix: two snapshots that
        both claim reliable clang vtable facts (the pre-fix world, where the
        flag didn't exist) genuinely produce a breaking finding for this
        old/new pair -- confirming the suppression below isn't just masking
        an already-inert case."""
        old = _snap(
            from_headers=True,
            ast_producer="clang",
            clang_vtable_facts_reliable=True,
            types=[self._legacy_clang_record()],
        )
        new = _snap(
            from_headers=True,
            ast_producer="clang",
            clang_vtable_facts_reliable=True,
            types=[self._fresh_clang_record()],
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.VPTR_INTRODUCED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_legacy_clang_baseline_suppresses_vptr_introduced(self) -> None:
        old = _snap(
            from_headers=True,
            ast_producer="clang",
            clang_vtable_facts_reliable=False,
            types=[self._legacy_clang_record()],
        )
        new = _snap(
            from_headers=True,
            ast_producer="clang",
            clang_vtable_facts_reliable=True,
            types=[self._fresh_clang_record()],
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.VPTR_INTRODUCED not in kinds
        assert ChangeKind.TYPE_VTABLE_CHANGED not in kinds

    def test_legacy_clang_baseline_suppresses_type_vtable_changed(self) -> None:
        """Same suppression, but for a class whose vtable differs in slot
        count between the two sides rather than empty-vs-non-empty (so
        _vtable_transition_is_evidenced's own guard would NOT have
        suppressed it on its own -- both sizes/virtual_bases are identical,
        which that guard treats as evidence of a real change)."""
        old = _snap(
            from_headers=True,
            ast_producer="clang",
            clang_vtable_facts_reliable=False,
            types=[
                RecordType(
                    name="A",
                    kind="class",
                    vtable=[],
                    vptr_offset_bits=None,
                    size_bits=64,
                )
            ],
        )
        new = _snap(
            from_headers=True,
            ast_producer="clang",
            clang_vtable_facts_reliable=True,
            types=[
                RecordType(
                    name="A",
                    kind="class",
                    vtable=["_ZN1A1fEv", "_ZN1A1gEv"],
                    vptr_offset_bits=0,
                    size_bits=64,
                )
            ],
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPE_VTABLE_CHANGED not in kinds

    def test_both_reliable_stays_unaffected(self) -> None:
        """A genuinely fresh-vs-fresh comparison (both True, the common
        case) must be entirely untouched by this gate."""
        old = _snap(
            from_headers=True,
            ast_producer="clang",
            types=[
                RecordType(name="A", kind="class", vtable=[], vptr_offset_bits=None)
            ],
        )
        new = _snap(
            from_headers=True,
            ast_producer="clang",
            types=[self._fresh_clang_record()],
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.VPTR_INTRODUCED in kinds

    def test_legacy_layout_unverifiable_suppressed_for_vptr_only_evidence(
        self,
    ) -> None:
        """LAYOUT_UNVERIFIABLE must not fire purely because a legacy
        clang baseline's blanket-None vptr_offset_bits appears to "gain
        evidence" on a fresh dump, when no other layout descriptor field
        (data_size_bits/base_offsets) is involved on either side."""
        old = _snap(
            from_headers=True,
            ast_producer="clang",
            clang_vtable_facts_reliable=False,
            types=[
                RecordType(
                    name="A",
                    kind="class",
                    vtable=[],
                    vptr_offset_bits=None,
                    size_bits=None,
                )
            ],
        )
        new = _snap(
            from_headers=True,
            ast_producer="clang",
            clang_vtable_facts_reliable=True,
            types=[
                RecordType(
                    name="A",
                    kind="class",
                    vtable=["_ZN1A1fEv"],
                    vptr_offset_bits=0,
                    size_bits=None,
                )
            ],
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.LAYOUT_UNVERIFIABLE not in kinds


class TestLayoutUnverifiableCorrelatedWithVtableChanged:
    """One evidence gap must not be reported at two different (and,
    read in isolation, contradictory-looking) severities for the *same*
    type with no cross-reference between them
    (``diff_types._vtable_transition_rests_on_unresolved_evidence`` +
    ``_layout_evidence_is_unverifiable`` +
    ``post_processing.AnnotateLayoutUnverifiableCoveredByVtableChanged``).

    ``_vtable_transition_is_evidenced`` (diff_types.py) treats an unknown
    ``size_bits`` on either side as "keep the finding" (BREAKING), while
    ``_check_layout_unverifiable`` (diff_layout.py) treats the identical
    asymmetric-evidence condition as calm, non-escalating RISK. Both
    findings always stay fully reported and independently scored —
    earlier designs tried demoting TYPE_VTABLE_CHANGED (unsafe: it can
    hide a real last-virtual-method removal indistinguishable from this
    same evidence gap) and folding LAYOUT_UNVERIFIABLE out of
    ``result.changes`` into ``redundant_changes`` (unsafe for a reason
    that generalizes: whether the fold reads as "safe" depends on
    downstream configuration — a `PolicyFile` override, a severity-scheme
    exit-code caller, a later pipeline step like
    ``DemoteUnreachableInternalChurn`` — chosen *after* ``compare()``
    already decided to remove the finding, so no compare()-time removal
    decision can ever be correct for every consumer). Instead, when both
    fire on the same type for the same evidence gap, the redundant
    LAYOUT_UNVERIFIABLE finding is *annotated* (``correlated_change_kind``
    set to ``"type_vtable_changed"``) rather than hidden or altered —
    every existing consumer that reads ``result.changes`` sees exactly
    what it always saw, plus one extra cross-reference field. Correlation
    uses the exact type-matched RecordType pair via ``qualified_name``,
    never a same-named-but-different type.
    """

    def test_layout_unverifiable_correlated_when_vtable_changed_covers_same_gap(
        self,
    ) -> None:
        """A type with asymmetric layout evidence and a differing vtable list
        emits TYPE_VTABLE_CHANGED (BREAKING) and LAYOUT_UNVERIFIABLE (RISK);
        both remain top-level findings, and the redundant LAYOUT_UNVERIFIABLE
        is annotated with a cross-reference to the covering finding."""
        old = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=[],
                    size_bits=None,
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=["_ZN3Foo1fEv"],
                    size_bits=None,
                    # Populates the layout descriptor on the new side only,
                    # so LAYOUT_UNVERIFIABLE's asymmetric-evidence condition
                    # fires alongside the vtable-list difference above.
                    base_offsets={"Base": 0},
                )
            ]
        )
        result = compare(old, new)
        changes_by_kind = {c.kind: c for c in result.changes}
        assert ChangeKind.LAYOUT_UNVERIFIABLE in changes_by_kind
        assert ChangeKind.TYPE_VTABLE_CHANGED in changes_by_kind

        vtable_change = changes_by_kind[ChangeKind.TYPE_VTABLE_CHANGED]
        assert vtable_change.effective_verdict is None
        assert vtable_change.modulation_reason is None  # never a modulation audit entry
        assert vtable_change.vtable_covers_unverifiable_layout_gap is True
        assert vtable_change.qualified_name == "Foo"
        assert result.verdict == Verdict.BREAKING

        layout_change = changes_by_kind[ChangeKind.LAYOUT_UNVERIFIABLE]
        assert (
            layout_change.correlated_change_kind == ChangeKind.TYPE_VTABLE_CHANGED.value
        )

        # Nothing is ever removed from result.changes for this reason.
        redundant_kinds = {c.kind for c in result.redundant_changes}
        assert ChangeKind.LAYOUT_UNVERIFIABLE not in redundant_kinds

    def test_vtable_changed_stays_breaking_without_layout_unverifiable(self) -> None:
        """No matching LAYOUT_UNVERIFIABLE for the type → TYPE_VTABLE_CHANGED
        keeps its ordinary BREAKING severity and no tagging happens
        (regression guard: the correlation must not fire universally)."""
        old = _snap(
            types=[
                RecordType(
                    name="Widget",
                    kind="class",
                    vtable=["_ZN6Widget4drawEv", "_ZN6Widget5paintEv"],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Widget",
                    kind="class",
                    vtable=["_ZN6Widget4drawEv"],
                )
            ]
        )
        result = compare(old, new)
        changes_by_kind = {c.kind: c for c in result.changes}
        assert ChangeKind.LAYOUT_UNVERIFIABLE not in changes_by_kind
        vtable_change = changes_by_kind[ChangeKind.TYPE_VTABLE_CHANGED]
        assert vtable_change.effective_verdict is None
        assert vtable_change.modulation_reason is None
        assert vtable_change.vtable_covers_unverifiable_layout_gap is False
        assert result.verdict == Verdict.BREAKING

    def test_both_sides_populated_vtable_change_not_tagged(self) -> None:
        """A real reorder (both vtable lists populated on the SAME type that
        also carries an unrelated LAYOUT_UNVERIFIABLE finding) must stay
        BREAKING, and the LAYOUT_UNVERIFIABLE finding must NOT be correlated
        — real evidence never triggers this correlation just because the
        same type also has an unresolved-size gap elsewhere (Codex review,
        P1)."""
        old = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=["_ZN3Foo1fEv", "_ZN3Foo1gEv"],
                    size_bits=None,
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=["_ZN3Foo1gEv", "_ZN3Foo1fEv"],  # reordered
                    size_bits=None,
                    # Populates the layout descriptor on the new side only, so
                    # LAYOUT_UNVERIFIABLE fires for this same type too — but the
                    # vtable reorder itself rests on real (both-populated) evidence,
                    # not the unresolved-size gap, so it must not be tagged.
                    base_offsets={"Base": 0},
                )
            ]
        )
        result = compare(old, new)
        changes_by_kind = {c.kind: c for c in result.changes}
        assert ChangeKind.LAYOUT_UNVERIFIABLE in changes_by_kind
        vtable_change = changes_by_kind[ChangeKind.TYPE_VTABLE_CHANGED]
        assert vtable_change.effective_verdict is None
        assert vtable_change.modulation_reason is None
        assert vtable_change.vtable_covers_unverifiable_layout_gap is False
        assert result.verdict == Verdict.BREAKING
        assert (
            changes_by_kind[ChangeKind.LAYOUT_UNVERIFIABLE].correlated_change_kind
            is None
        )

    def test_owned_virtual_function_signature_change_not_tagged(self) -> None:
        """A real change in the class's own virtual function set (a separate
        evidence stream from RecordType.vtable, sourced from snapshot.
        functions) is independent evidence — the co-occurring
        LAYOUT_UNVERIFIABLE for the same type must not be correlated just
        because size_bits also happens to be unknown. Distinct from the
        both-populated-vtable case above: here the vtable list itself is
        empty on one side, so only the owned-virtual-signatures branch of
        _vtable_transition_rests_on_unresolved_evidence is exercised."""
        old = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=[],
                    size_bits=None,
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=["_ZN3Foo1fEv"],
                    size_bits=None,
                    base_offsets={
                        "Base": 0
                    },  # asymmetric evidence -> LAYOUT_UNVERIFIABLE
                )
            ],
            functions=[
                Function(
                    name="Foo::f",
                    mangled="_ZN3Foo1fEv",
                    return_type="void",
                    is_virtual=True,
                )
            ],
        )
        result = compare(old, new)
        changes_by_kind = {c.kind: c for c in result.changes}
        assert ChangeKind.LAYOUT_UNVERIFIABLE in changes_by_kind
        vtable_change = changes_by_kind[ChangeKind.TYPE_VTABLE_CHANGED]
        assert vtable_change.vtable_covers_unverifiable_layout_gap is False
        assert result.verdict == Verdict.BREAKING
        assert (
            changes_by_kind[ChangeKind.LAYOUT_UNVERIFIABLE].correlated_change_kind
            is None
        )

    def test_virtual_base_change_not_tagged_even_with_unknown_size(self) -> None:
        """A genuine virtual-base addition is real, independent evidence —
        the co-occurring LAYOUT_UNVERIFIABLE for the same type must not be
        correlated just because size_bits also happens to be unknown
        (Codex review, P2 follow-up)."""
        old = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=[],
                    virtual_bases=[],
                    size_bits=None,
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=["_ZN3Foo1fEv"],
                    virtual_bases=["Base"],
                    size_bits=None,
                    base_offsets={
                        "Base": 0
                    },  # asymmetric evidence -> LAYOUT_UNVERIFIABLE
                )
            ]
        )
        result = compare(old, new)
        changes_by_kind = {c.kind: c for c in result.changes}
        assert ChangeKind.LAYOUT_UNVERIFIABLE in changes_by_kind
        vtable_change = changes_by_kind[ChangeKind.TYPE_VTABLE_CHANGED]
        assert vtable_change.effective_verdict is None
        assert vtable_change.modulation_reason is None
        assert vtable_change.vtable_covers_unverifiable_layout_gap is False
        assert result.verdict == Verdict.BREAKING
        assert (
            changes_by_kind[ChangeKind.LAYOUT_UNVERIFIABLE].correlated_change_kind
            is None
        )

    def test_ordinary_base_change_not_tagged_even_with_unknown_size(self) -> None:
        """A genuine (non-virtual) base addition is real, independent
        evidence — separately reported via TYPE_BASE_CHANGED/
        BASE_CLASS_POSITION_CHANGED — so the co-occurring LAYOUT_UNVERIFIABLE
        for the same type must not be correlated just because size_bits also
        happens to be unknown (Codex review, fresh evidence: the identical
        false-correlation risk already guarded for virtual_bases, but for
        ordinary bases)."""
        old = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=[],
                    bases=[],
                    size_bits=None,
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=["_ZN3Foo1fEv"],
                    bases=["Base"],
                    size_bits=None,
                    base_offsets={
                        "Base": 0
                    },  # asymmetric evidence -> LAYOUT_UNVERIFIABLE
                )
            ]
        )
        result = compare(old, new)
        changes_by_kind = {c.kind: c for c in result.changes}
        assert ChangeKind.LAYOUT_UNVERIFIABLE in changes_by_kind
        vtable_change = changes_by_kind[ChangeKind.TYPE_VTABLE_CHANGED]
        assert vtable_change.effective_verdict is None
        assert vtable_change.modulation_reason is None
        assert vtable_change.vtable_covers_unverifiable_layout_gap is False
        assert result.verdict == Verdict.BREAKING
        assert (
            changes_by_kind[ChangeKind.LAYOUT_UNVERIFIABLE].correlated_change_kind
            is None
        )

    def test_bare_name_collision_does_not_cross_correlate(self) -> None:
        """Two distinct types sharing only a bare leaf name in different
        namespaces must not correlate: ``ns2::Foo``'s ambiguous vtable gap
        must not tag ``ns1::Foo``'s unrelated, real LAYOUT_UNVERIFIABLE
        finding (Codex review, P2)."""
        old = _snap(
            types=[
                RecordType(
                    name="Foo",
                    qualified_name="ns1::Foo",
                    kind="class",
                    vtable=["_ZN3ns13Foo1fEv"],
                    size_bits=None,
                ),
                RecordType(
                    name="Foo",
                    qualified_name="ns2::Foo",
                    kind="class",
                    vtable=[],
                    size_bits=None,
                ),
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Foo",
                    qualified_name="ns1::Foo",
                    kind="class",
                    vtable=["_ZN3ns13Foo1fEv"],
                    size_bits=None,
                    # ns1::Foo's own unrelated asymmetric-evidence gap — must
                    # stay reported and uncorrelated, not tagged by ns2::Foo's
                    # ambiguous vtable transition below.
                    base_offsets={"Base": 0},
                ),
                RecordType(
                    name="Foo",
                    qualified_name="ns2::Foo",
                    kind="class",
                    vtable=["_ZN3ns23Foo1hEv"],
                    size_bits=None,
                    base_offsets={"Base": 0},  # ns2::Foo's own ambiguous gap
                ),
            ]
        )
        result = compare(old, new)
        layout_findings = {
            c.qualified_name: c
            for c in result.changes
            if c.kind == ChangeKind.LAYOUT_UNVERIFIABLE
        }
        # AbiSnapshot's type index is first-wins by bare name (a documented,
        # unrelated sharp edge — see model.py's "Duplicate type names"
        # warning), so only ns1::Foo is actually processed here. What this
        # test guards is narrower and still real: ns1::Foo's own finding
        # must stay uncorrelated rather than picking up a stray tag from
        # ns2::Foo's identically-named but distinct RecordType.
        assert "ns1::Foo" in layout_findings
        assert layout_findings["ns1::Foo"].correlated_change_kind is None

    def test_owned_virtual_signature_scoped_to_qualified_owner(self) -> None:
        """An unrelated virtual method gained by a *different*,
        same-leaf-name record in another namespace (``ns2::Foo::g`` — never
        itself processed as a matched ``RecordType`` here, only its function
        entry exists) must not make the genuinely-evidenced ``ns1::Foo``
        pair's owned-virtual-signature sets look different.

        Before the fix, ``_owned_virtual_signatures`` matched ownership by
        eager bare-leaf-suffix expansion against ``t_old.name`` ("Foo"),
        which carries no namespace — the unrelated ``ns2::Foo::g`` collapsed
        onto the same bare "Foo" suffix and was folded into the new side's
        owned-signature set even though ``ns1::Foo`` itself declares no
        virtuals in either snapshot. old_owned (``{}``) then differed from
        new_owned (``{"_ZN3ns23Foo1gEv"}``), which read as independent
        evidence and withheld the correlation (Codex review, fresh
        evidence). The fix (``_owned_virtual_signatures_for_record``) scopes
        matching to the exact qualified owner instead, so the unrelated
        ``ns2::Foo::g`` is correctly excluded and the shared
        unresolved-evidence gap is still recognized."""
        old = _snap(
            types=[
                RecordType(
                    name="Foo",
                    qualified_name="ns1::Foo",
                    kind="class",
                    vtable=[],
                    size_bits=None,
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Foo",
                    qualified_name="ns1::Foo",
                    kind="class",
                    vtable=["_ZN3ns13Foo1fEv"],
                    size_bits=None,
                    # ns1::Foo's own asymmetric-evidence gap — the case this
                    # correlation exists to catch.
                    base_offsets={"Base": 0},
                )
            ],
            functions=[
                # Unrelated: a *different* namespace's same-leaf-name class
                # gains a virtual method. Must not affect ns1::Foo's own
                # owned-signature comparison.
                Function(
                    name="ns2::Foo::g",
                    mangled="_ZN3ns23Foo1gEv",
                    return_type="void",
                    is_virtual=True,
                ),
            ],
        )
        result = compare(old, new)
        changes_by_kind = {c.kind: c for c in result.changes}
        assert ChangeKind.LAYOUT_UNVERIFIABLE in changes_by_kind
        vtable_change = changes_by_kind[ChangeKind.TYPE_VTABLE_CHANGED]
        assert vtable_change.vtable_covers_unverifiable_layout_gap is True
        assert (
            changes_by_kind[ChangeKind.LAYOUT_UNVERIFIABLE].correlated_change_kind
            == ChangeKind.TYPE_VTABLE_CHANGED.value
        )

    def test_owned_virtual_signature_uses_one_normalized_identity_for_both_sides(
        self,
    ) -> None:
        """A legacy stored snapshot can leave ``RecordType.qualified_name``
        unset (``None``) on one side while the fresher other side (or
        ``TypeMap``'s own ambiguity-safe bare-name fallback, upstream of
        this function) already knows both sides are the same namespaced
        ``ns::Foo`` record. Deriving each side's owner-matching identity
        independently from its own ``RecordType`` — "Foo" (old, unset) vs.
        "ns::Foo" (new, set) — would permanently mismatch a virtual method
        present unchanged on *both* sides purely because of that spelling
        difference, manufacturing a spurious "independently evidenced"
        verdict and withholding the correlation (Codex review, fresh
        evidence). The fix normalizes to one shared identity for both
        sides."""
        old = _snap(
            types=[
                RecordType(
                    name="Foo",
                    qualified_name=None,  # legacy snapshot: unset
                    kind="class",
                    vtable=[],
                    size_bits=None,
                )
            ],
            functions=[
                Function(
                    name="ns::Foo::f",
                    mangled="_ZN2ns3Foo1fEv",
                    return_type="void",
                    is_virtual=True,
                )
            ],
        )
        new = _snap(
            types=[
                RecordType(
                    name="Foo",
                    qualified_name="ns::Foo",
                    kind="class",
                    vtable=["_ZN2ns3Foo1fEv"],
                    size_bits=None,
                    # ns::Foo's own asymmetric-evidence gap — the case this
                    # correlation exists to catch.
                    base_offsets={"Base": 0},
                )
            ],
            functions=[
                # Same virtual method, unchanged from the old side.
                Function(
                    name="ns::Foo::f",
                    mangled="_ZN2ns3Foo1fEv",
                    return_type="void",
                    is_virtual=True,
                )
            ],
        )
        result = compare(old, new)
        changes_by_kind = {c.kind: c for c in result.changes}
        assert ChangeKind.LAYOUT_UNVERIFIABLE in changes_by_kind
        vtable_change = changes_by_kind[ChangeKind.TYPE_VTABLE_CHANGED]
        assert vtable_change.vtable_covers_unverifiable_layout_gap is True
        assert (
            changes_by_kind[ChangeKind.LAYOUT_UNVERIFIABLE].correlated_change_kind
            == ChangeKind.TYPE_VTABLE_CHANGED.value
        )

    def test_correlation_independent_of_policy_override(self) -> None:
        """The annotation is set purely from the two detectors' own evidence
        — it must not depend on, or be defeated/created by, a PolicyFile
        override of either kind's verdict. This is the exact class of bug
        the fold-based design could not avoid (Codex review): a compare()-
        time decision to *remove* a finding could be made wrong by
        configuration chosen after compare() returns, while a decision that
        only *annotates* a finding that stays in ``result.changes`` cannot,
        since every consumer still sees the finding either way."""
        from abicheck.policy_file import PolicyFile

        old = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=[],
                    size_bits=None,
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=["_ZN3Foo1fEv"],
                    size_bits=None,
                    base_offsets={"Base": 0},
                )
            ]
        )
        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.TYPE_VTABLE_CHANGED: Verdict.COMPATIBLE},
        )
        result = compare(old, new, policy_file=pf)
        # The policy override changes the *verdict* contribution, but both
        # findings stay fully present and the annotation is unaffected.
        assert result.verdict == Verdict.COMPATIBLE_WITH_RISK
        changes_by_kind = {c.kind: c for c in result.changes}
        assert ChangeKind.LAYOUT_UNVERIFIABLE in changes_by_kind
        assert ChangeKind.TYPE_VTABLE_CHANGED in changes_by_kind
        assert (
            changes_by_kind[ChangeKind.LAYOUT_UNVERIFIABLE].correlated_change_kind
            == ChangeKind.TYPE_VTABLE_CHANGED.value
        )

    def test_severity_exit_code_gate_sees_both_findings(self) -> None:
        """End-to-end regression for the exact gap Codex review found: the
        severity-scheme exit code (``severity.compute_exit_code``) is a
        *separate* consumer from the legacy verdict, chosen entirely after
        ``compare()`` returns, and reads ``result.changes`` directly. Since
        neither finding is ever removed from ``changes``, an
        ``abi_breaking=info`` / ``potential_breaking=error`` severity
        configuration still sees LAYOUT_UNVERIFIABLE's own error-level
        contribution regardless of how TYPE_VTABLE_CHANGED is configured."""
        from abicheck.severity import SeverityConfig, SeverityLevel, compute_exit_code

        old = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=[],
                    size_bits=None,
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=["_ZN3Foo1fEv"],
                    size_bits=None,
                    base_offsets={"Base": 0},
                )
            ]
        )
        result = compare(old, new)
        cfg = SeverityConfig(
            abi_breaking=SeverityLevel.INFO,
            potential_breaking=SeverityLevel.ERROR,
            quality_issues=SeverityLevel.INFO,
            addition=SeverityLevel.INFO,
        )
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.LAYOUT_UNVERIFIABLE in kinds
        assert ChangeKind.TYPE_VTABLE_CHANGED in kinds
        exit_code = compute_exit_code(
            result.changes,
            cfg,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
        # abi_breaking is INFO, so TYPE_VTABLE_CHANGED contributes 0; the
        # exact code (2) must come from LAYOUT_UNVERIFIABLE's own
        # potential_breaking category, not merely "some" nonzero code that
        # could also be explained by an unrelated finding (Codex review).
        assert exit_code == 2

    def test_correlation_survives_covering_finding_demoted_as_unreachable_internal(
        self,
    ) -> None:
        """When the covering TYPE_VTABLE_CHANGED is itself later demoted to
        out-of-surface by DemoteUnreachableInternalChurn (a confirmed-private
        internal-namespace type with no public leak path), LAYOUT_UNVERIFIABLE
        for the same type is independently demoted alongside it — both
        findings are ordinary members of ``changes`` right up until that
        later pipeline step runs, so there is no fold-ordering hazard to
        guard against anymore (contrast with the earlier fold-based design,
        where the fold ran before this demotion step and could orphan the
        decision — Codex review)."""
        old = _snap(
            types=[
                RecordType(
                    name="ns::detail::Foo",
                    kind="class",
                    vtable=[],
                    size_bits=None,
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="ns::detail::Foo",
                    kind="class",
                    vtable=["_ZN3ns6detail3Foo1fEv"],
                    size_bits=None,
                    base_offsets={"Base": 0},
                )
            ]
        )
        # No public function/type references ns::detail::Foo anywhere, so
        # DetectInternalLeaks finds no leak path and
        # DemoteUnreachableInternalChurn demotes both findings to
        # out_of_surface.
        result = compare(old, new)
        assert result.verdict != Verdict.BREAKING
        assert result.verdict != Verdict.COMPATIBLE_WITH_RISK
        assert result.verdict == Verdict.NO_CHANGE
        assert not result.changes
        out_of_surface_kinds = {c.kind for c in result.out_of_surface_changes}
        assert ChangeKind.TYPE_VTABLE_CHANGED in out_of_surface_kinds
        assert ChangeKind.LAYOUT_UNVERIFIABLE in out_of_surface_kinds
        # The correlation itself must also survive the demotion, matching
        # this test's own name (Codex review, fresh evidence: the name
        # promised a correlation assertion the body never made).
        layout_change = next(
            c
            for c in result.out_of_surface_changes
            if c.kind == ChangeKind.LAYOUT_UNVERIFIABLE
        )
        assert (
            layout_change.correlated_change_kind == ChangeKind.TYPE_VTABLE_CHANGED.value
        )

    def test_correlation_reaches_cached_impact_assessment(self) -> None:
        """Codex review, fresh evidence: when a configured suppression
        requires reachability evidence, MarkReachability caches each tagged
        change's whole ImpactAssessment via impact.engine.assess_change() --
        and assess_change() prefers a cached assessment's own
        correlated_change_kind over the flat Change field once one exists.
        The annotation step must therefore run *before* MarkReachability, or
        JSON/SARIF's unified impact_assessment block would silently omit the
        correlation the flat top-level field still carries correctly."""
        from abicheck.suppression import Suppression, SuppressionList

        old = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=[],
                    size_bits=None,
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=["_ZN3Foo1fEv"],
                    size_bits=None,
                    base_offsets={"Base": 0},
                )
            ]
        )
        # A broad selector forces SuppressionList.needs_reachability_evidence()
        # to True, which is what makes MarkReachability actually run (and
        # cache impact_assessment) instead of early-returning.
        suppression = SuppressionList(
            [
                Suppression(
                    namespace="unrelated::*",
                    reason="unrelated rule, present only to force reachability evidence",
                )
            ]
        )
        result = compare(old, new, suppression=suppression)
        layout_change = next(
            c for c in result.changes if c.kind == ChangeKind.LAYOUT_UNVERIFIABLE
        )
        assert (
            layout_change.correlated_change_kind == ChangeKind.TYPE_VTABLE_CHANGED.value
        )
        assert layout_change.impact_assessment is not None
        assert (
            layout_change.impact_assessment.correlated_change_kind
            == ChangeKind.TYPE_VTABLE_CHANGED.value
        )

    def test_correlation_cleared_when_covering_finding_is_suppressed(self) -> None:
        """Codex review, fresh evidence: a suppression rule can target only
        the covering TYPE_VTABLE_CHANGED (e.g. an allow_public_break waiver
        on that one finding) without touching the co-reported
        LAYOUT_UNVERIFIABLE. The early annotation runs before
        ApplySuppression (it must, to seed MarkReachability's cache
        correctly -- see AnnotateLayoutUnverifiableCoveredByVtableChanged's
        own docstring), so left uncorrected the surviving LAYOUT_UNVERIFIABLE
        finding would keep a "see also: type_vtable_changed" reference to a
        finding the report no longer shows at all. Also confirms a
        previously-cached impact_assessment is kept in sync, not left
        pointing at the now-stale value."""
        from abicheck.suppression import Suppression, SuppressionList

        old = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=[],
                    size_bits=None,
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=["_ZN3Foo1fEv"],
                    size_bits=None,
                    base_offsets={"Base": 0},
                )
            ]
        )
        suppression = SuppressionList(
            [
                Suppression(
                    symbol="Foo",
                    change_kind="type_vtable_changed",
                    allow_public_break=True,
                    reason="waiver on the vtable finding only",
                ),
                # Unrelated broad rule (CodeRabbit review, fresh evidence): the
                # symbol="Foo" rule above is narrow, so on its own it makes
                # SuppressionList.needs_reachability_evidence() return False
                # and MarkReachability.run() short-circuits before caching any
                # impact_assessment -- leaving the assertion below unreachable
                # dead code, not a real check. This rule forces the cache to
                # actually populate, matching test_correlation_reaches_
                # cached_impact_assessment's own convention.
                Suppression(
                    namespace="unrelated::*",
                    reason="force MarkReachability to cache impact_assessment",
                ),
            ]
        )
        result = compare(old, new, suppression=suppression)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPE_VTABLE_CHANGED not in kinds
        layout_change = next(
            c for c in result.changes if c.kind == ChangeKind.LAYOUT_UNVERIFIABLE
        )
        assert layout_change.correlated_change_kind is None
        assert layout_change.impact_assessment is not None
        assert layout_change.impact_assessment.correlated_change_kind is None
