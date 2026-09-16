from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change
from abicheck.policy.severity import IssueCategory
from abicheck.report.finding import ReportFinding
from abicheck.report.review_groups import build_review_groups


def _finding(
    kind: ChangeKind, symbol: str, *, library: str | None = None
) -> ReportFinding:
    change = Change(kind, symbol, kind.value, library=library)
    return ReportFinding(change, Verdict.BREAKING, IssueCategory.ABI_BREAKING)


def test_export_and_visibility_evidence_group_without_dropping_members() -> None:
    removed = _finding(ChangeKind.FUNC_REMOVED_ELF_ONLY, "_ZN1A3addEv")
    visibility = _finding(ChangeKind.FUNC_VISIBILITY_CHANGED, "_ZN1A3addEv")
    visibility.change.surface_facts = {
        "declared_in_headers": "true",
        "in_public_contract": "true",
        "binary_exported": "false",
    }
    groups = build_review_groups((removed, visibility))
    assert len(groups) == 1
    assert groups[0].member_kinds == (
        "func_removed_elf_only",
        "func_visibility_changed",
    )
    assert len(groups[0].member_finding_ids) == 2


def test_export_relationship_without_declaration_fact_remains_ambiguous() -> None:
    assert (
        len(
            build_review_groups(
                (
                    _finding(ChangeKind.FUNC_REMOVED_ELF_ONLY, "_ZN1A3addEv"),
                    _finding(ChangeKind.FUNC_VISIBILITY_CHANGED, "_ZN1A3addEv"),
                )
            )
        )
        == 2
    )


def test_vtable_evidence_groups_but_same_name_in_different_dsos_does_not() -> None:
    groups = build_review_groups(
        (
            _finding(ChangeKind.TYPE_VTABLE_CHANGED, "N::V", library="a.so"),
            _finding(ChangeKind.VTABLE_SLOT_COUNT_CHANGED, "N::V", library="a.so"),
            _finding(ChangeKind.TYPE_VTABLE_CHANGED, "N::V", library="b.so"),
        )
    )
    assert [len(group.member_kinds) for group in groups] == [2, 1]


def test_overloads_and_namespaces_remain_separate() -> None:
    groups = build_review_groups(
        (
            _finding(ChangeKind.FUNC_REMOVED_ELF_ONLY, "_ZN1A3addEi"),
            _finding(ChangeKind.FUNC_REMOVED_ELF_ONLY, "_ZN1B3addEi"),
            _finding(ChangeKind.FUNC_REMOVED_ELF_ONLY, "_ZN1A3addEf"),
        )
    )
    assert len(groups) == 3


def test_vtable_append_and_size_evidence_is_precise_not_reordering() -> None:
    header = _finding(ChangeKind.TYPE_VTABLE_CHANGED, "N::V")
    header.change.old_value = "f(), g()"
    header.change.new_value = "f(), g(), h(), i()"
    header.change.review_evidence = {
        "kind": "declared_vtable_sequence",
        "old_entries": ["f(int, float)", "g()"],
        "new_entries": ["f(int, float)", "g()", "h()", "i()"],
    }
    size = _finding(ChangeKind.VTABLE_SLOT_COUNT_CHANGED, "_ZTVN1N1VE")
    size.change.qualified_name = "N::V"
    size.change.old_value = "88"
    size.change.new_value = "104"
    size.change.review_evidence = {
        "kind": "elf_vtable_group_size",
        "old_bytes": 88,
        "new_bytes": 104,
    }
    (group,) = build_review_groups((header, size))
    assert "appended 2 entries" in group.transition
    assert "2 prior entries retain order" in group.transition
    assert "88 to 104 bytes" in group.transition
    assert "cannot identify slots or inheritance" in group.transition
    assert "reordered" not in group.transition


def test_vtable_reorder_removal_replacement_and_unknown_are_distinct() -> None:
    cases = (
        (("a, b", "b, a"), "reordered"),
        (("a, b", "a"), "removed 1 trailing entry"),
        (("a, b", "a, c"), "replaced"),
        ((None, None), "incomplete layout evidence"),
    )
    for (old, new), phrase in cases:
        finding = _finding(ChangeKind.TYPE_VTABLE_CHANGED, f"N::{phrase}")
        finding.change.old_value = old
        finding.change.new_value = new
        assert phrase in build_review_groups((finding,))[0].transition


def test_contradictory_vtable_directions_are_not_grouped() -> None:
    header = _finding(ChangeKind.TYPE_VTABLE_CHANGED, "N::V")
    header.change.review_evidence = {
        "kind": "declared_vtable_sequence",
        "old_entries": ["f()"],
        "new_entries": ["f()", "g()"],
    }
    size = _finding(ChangeKind.VTABLE_SLOT_COUNT_CHANGED, "_ZTVN1N1VE")
    size.change.qualified_name = "N::V"
    size.change.review_evidence = {
        "kind": "elf_vtable_group_size",
        "old_bytes": 104,
        "new_bytes": 88,
    }
    assert len(build_review_groups((header, size))) == 2


def test_inheritance_size_evidence_does_not_claim_an_exact_base_change() -> None:
    finding = _finding(ChangeKind.RTTI_INHERITANCE_CHANGED, "_ZTIN1N1VE")
    finding.change.qualified_name = "N::V"
    finding.change.old_value = "16"
    finding.change.new_value = "24"
    (group,) = build_review_groups((finding,))
    assert "RTTI inheritance shape changed" in group.transition
    assert "not the exact base transition" in group.transition


# ---------------------------------------------------------------------------
# `build_review_groups` returns a *sorted* tuple, so its key must be total
# over every group it can itself construct. A finding with no name at all —
# an analysis- or container-level fact carries no symbol — made every source
# of `display_name` None, which both violated the field's declared `str` type
# and raised `TypeError: '<' not supported between instances of 'NoneType'
# and 'str'` the moment such a group met a named one. The crash needed two
# groups to surface, so a single-finding test could not have found it.
# ---------------------------------------------------------------------------


def _unnamed_finding(kind: ChangeKind, *, library: str | None = None):
    change = Change(kind, None, kind.value, library=library)
    return ReportFinding(change, Verdict.BREAKING, IssueCategory.ABI_BREAKING)


def test_a_finding_with_no_name_still_groups_and_sorts() -> None:
    """The reported crash, and the property behind it."""
    groups = build_review_groups(
        [
            _unnamed_finding(ChangeKind.FUNC_REMOVED),
            _finding(ChangeKind.FUNC_REMOVED, "_ZN1A3addEv"),
        ]
    )
    assert groups, "an unnamed finding must still produce a group"
    for group in groups:
        assert isinstance(group.display_name, str) and group.display_name, (
            f"display_name is not a non-empty str: {group.display_name!r}"
        )


def test_the_sort_is_total_over_every_mix_of_named_and_unnamed() -> None:
    """Order-independence too: the result must not depend on input order.

    Stated over the combinations rather than the one reported pair — the
    crash surfaced only when an unnamed group was compared against a named
    one, so which side of the comparison each lands on is the axis.
    """
    import itertools

    population = [
        _unnamed_finding(ChangeKind.FUNC_REMOVED),
        _unnamed_finding(ChangeKind.VAR_REMOVED, library="libb.so"),
        _finding(ChangeKind.FUNC_REMOVED, "_ZN1A3addEv"),
        _finding(ChangeKind.VAR_REMOVED, "global_x", library="liba.so"),
    ]
    baseline = [g.group_id for g in build_review_groups(population)]
    for order in itertools.permutations(population):
        assert [g.group_id for g in build_review_groups(list(order))] == baseline, (
            f"group order depends on input order for {order}"
        )


def test_the_unnamed_case_is_really_unnamed() -> None:
    """Vacuity guard: the fixture must actually exercise the None path."""
    change = Change(ChangeKind.FUNC_REMOVED, None, ChangeKind.FUNC_REMOVED.value)
    assert change.symbol is None
    assert not getattr(change, "qualified_name", None)
    assert not getattr(change, "demangled_symbol", None)
