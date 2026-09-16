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

"""Workstream G S1: the "what changed / review actions" surface-first
section (``report.surface_changes``).

Report invariant under test (``docs/contribute/plans/
vision-api-abi-evolution.md``, workstream G): *"Compatible additions are
visible changes: a compatible run still itemizes what was added; '0
breaking' is not 'nothing happened'."* The section groups every detected
finding into additions/removals/modifications regardless of severity, so a
fully-compatible run still lists its additions, and a reviewer can read each
entry's old/new declaration directly rather than opening the raw JSON
``changes`` array.
"""

from __future__ import annotations

import json

import pytest

from abicheck.checker import compare
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.report.surface_changes import (
    MAX_COMPACT_SURFACE_ITEMS,
    SurfaceChangeEntry,
    SurfaceChangeSection,
    compute_surface_changes,
    render_surface_changes_lines,
    render_surface_changes_section,
)


def _snapshots() -> tuple[AbiSnapshot, AbiSnapshot]:
    """One addition, one removal, one signature-changed function, one
    unchanged function."""
    old = AbiSnapshot(library="libfoo", version="1.0")
    new = AbiSnapshot(library="libfoo", version="2.0")

    gone = Function(
        name="foo::gone",
        mangled="_ZN3foo4goneEv",
        return_type="void",
        visibility=Visibility.PUBLIC,
    )
    old.functions.append(gone)

    stay = Function(
        name="foo::stay",
        mangled="_ZN3foo4stayEv",
        return_type="void",
        visibility=Visibility.PUBLIC,
    )
    old.functions.append(stay)
    new.functions.append(stay)

    changed_old = Function(
        name="foo::changed",
        mangled="_ZN3foo7changedEv",
        return_type="int",
        visibility=Visibility.PUBLIC,
    )
    changed_new = Function(
        name="foo::changed",
        mangled="_ZN3foo7changedEv",
        return_type="double",
        visibility=Visibility.PUBLIC,
    )
    old.functions.append(changed_old)
    new.functions.append(changed_new)

    new_fn = Function(
        name="foo::brand_new",
        mangled="_ZN3foo9brand_newEv",
        return_type="void",
        visibility=Visibility.PUBLIC,
    )
    new.functions.append(new_fn)

    return old, new


def test_compute_surface_changes_groups_by_review_action() -> None:
    old, new = _snapshots()
    result = compare(old, new)
    section = compute_surface_changes(result)

    assert {e.symbol for e in section.additions} == {"_ZN3foo9brand_newEv"}
    assert {e.symbol for e in section.removals} == {"_ZN3foo4goneEv"}
    assert {e.symbol for e in section.modifications} == {"_ZN3foo7changedEv"}
    assert section.total == 3


def test_catalog_operation_not_kind_suffix_or_severity_controls_surface() -> None:
    """ELF-only public removal is a removal; an import is not public API."""
    from abicheck.checker_policy import ChangeKind, Verdict
    from abicheck.checker_types import Change, DiffResult

    changes = [
        Change(ChangeKind.FUNC_REMOVED_ELF_ONLY, "_Z3foov", "export disappeared"),
        Change(ChangeKind.IMPORTED_SYMBOL_REMOVED, "write", "import disappeared"),
    ]
    # A presentation/policy override must not rewrite the observed operation.
    changes[0].effective_verdict = Verdict.COMPATIBLE
    section = compute_surface_changes(
        DiffResult(old_version="1", new_version="2", library="lib", changes=changes)
    )
    assert [entry.symbol for entry in section.removals] == ["_Z3foov"]
    assert all(
        entry.symbol != "write"
        for entry in (*section.additions, *section.removals, *section.modifications)
    )


def test_compact_surface_list_is_bounded_and_discloses_omissions() -> None:
    from abicheck.checker_policy import ChangeKind
    from abicheck.checker_types import Change, DiffResult

    result = DiffResult(
        old_version="1",
        new_version="2",
        library="lib",
        changes=[
            Change(ChangeKind.FUNC_ADDED, f"function_{i}", "added")
            for i in range(10_000)
        ],
    )
    section = compute_surface_changes(result)
    lines = render_surface_changes_lines(section)
    assert len(lines) < 30
    assert "9988 more additions omitted" in "\n".join(lines)
    assert "function_9999" not in "\n".join(lines)


def test_removal_entry_carries_its_old_declaration() -> None:
    old, new = _snapshots()
    result = compare(old, new)
    section = compute_surface_changes(result)
    (removal,) = section.removals
    assert removal.old_declaration is not None
    assert removal.new_declaration is None


def test_modification_entry_carries_both_declarations() -> None:
    old, new = _snapshots()
    result = compare(old, new)
    section = compute_surface_changes(result)
    (modification,) = section.modifications
    assert modification.old_declaration is not None
    assert modification.new_declaration is not None
    assert modification.old_declaration != modification.new_declaration


def test_compatible_additions_are_visible_even_on_a_clean_run() -> None:
    """The report invariant, exercised directly: a run with only an
    addition (no breaking/removed/modified finding at all) still lists it,
    the way a severity-grouped summary might collapse it into "0 breaking"
    and stop there."""
    old = AbiSnapshot(library="libfoo", version="1.0")
    new = AbiSnapshot(library="libfoo", version="2.0")
    new.functions.append(
        Function(
            name="foo::only_addition",
            mangled="_ZN3foo13only_additionEv",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
    )
    result = compare(old, new)
    assert result.verdict.value.lower() == "compatible"
    section = compute_surface_changes(result)
    assert section.removals == ()
    assert section.modifications == ()
    assert len(section.additions) == 1
    assert section.additions[0].symbol == "_ZN3foo13only_additionEv"


def test_surface_change_section_round_trips_through_to_dict() -> None:
    old, new = _snapshots()
    result = compare(old, new)
    section = compute_surface_changes(result)
    rebuilt = SurfaceChangeSection.from_dict(section.to_dict())
    assert rebuilt == section


def test_json_report_carries_the_surface_changes_block() -> None:
    from abicheck import reporter

    old, new = _snapshots()
    result = compare(old, new)
    report = json.loads(reporter.to_json(result))
    block = report["surface_changes"]
    assert block["total"] == 3
    assert len(block["additions"]) == 1
    assert len(block["removals"]) == 1
    assert len(block["modifications"]) == 1


def test_review_digest_and_full_markdown_itemize_every_group() -> None:
    from abicheck.reporter_markdown import (
        _to_markdown_root_cause,
        to_markdown,
        to_review_digest,
    )

    old, new = _snapshots()
    result = compare(old, new)

    digest = to_review_digest(result)
    assert "## Surface changes" not in digest  # digest has no section heading
    assert "**Review:**" in digest
    assert "brand_new" in digest and "gone" in digest
    assert "func_added" in digest
    assert "func_removed" in digest

    text = to_markdown(result)
    assert "## Related review groups" in text
    assert "brand_new" in text and "gone" in text
    root = _to_markdown_root_cause(result)
    assert "## Surface changes" in root
    assert "_ZN3foo9brand_newEv" in root


def test_surface_changes_honors_show_only_like_every_other_section() -> None:
    """A finding ``--show-only`` filtered out of the severity groups must
    not reappear in ``surface_changes`` -- the section must group the same
    *displayed* findings the rest of the view shows, not the raw
    ``result.changes``."""
    from abicheck import reporter
    from abicheck.reporter_markdown import (
        _to_markdown_root_cause,
        to_markdown,
    )

    old, new = _snapshots()
    result = compare(old, new)

    for payload_fn in (
        lambda: reporter.to_json(result, show_only="breaking"),
        lambda: reporter.to_json(
            result, report_mode="root-cause", show_only="breaking"
        ),
    ):
        block = json.loads(payload_fn())["surface_changes"]
        assert block["additions"] == [], payload_fn

    for render in (to_markdown, _to_markdown_root_cause):
        text = render(result, show_only="breaking")
        assert "_ZN3foo9brand_newEv" not in text, render.__name__


def test_review_digest_document_round_trips_without_a_surface_changes_key() -> None:
    """A document built before this field existed (schema < 3.9) has no
    ``surface_changes`` key at all -- the round trip must still project
    cleanly, per ``_review_digest_from_mapping``'s own docstring."""
    from abicheck.report.document import ReportDocument
    from abicheck.report.render_markdown_document import (
        render_review_digest_document,
    )

    old, new = _snapshots()
    result = compare(old, new)

    from abicheck.report.render_markdown_document import build_review_digest_document

    doc = build_review_digest_document(result)
    mapping = doc.to_mapping()
    del mapping["surface_changes"]
    pre_39_doc = ReportDocument.from_mapping(mapping)

    text = render_review_digest_document(pre_39_doc)
    assert "ABI review" in text
    assert "Additions" not in text  # nothing to itemize without the field


def test_root_cause_document_round_trips_without_a_surface_changes_key() -> None:
    """Same backward-compatibility guarantee as the review digest, for the
    view preamble (``_render_view_preamble``). Stated against the
    root-cause document since plan slice 7o retired the leaf one."""
    from abicheck.report.document import ReportDocument
    from abicheck.report.render_markdown_alternate import (
        build_root_cause_document,
        render_root_cause_document,
    )

    old, new = _snapshots()
    result = compare(old, new)

    doc = build_root_cause_document(result)
    mapping = doc.to_mapping()
    del mapping["surface_changes"]
    pre_39_doc = ReportDocument.from_mapping(mapping)

    text = render_root_cause_document(pre_39_doc)
    assert "ABI Report" in text
    assert "## Surface changes" not in text


def test_render_lines_and_section_are_both_empty_for_an_empty_section() -> None:
    empty = SurfaceChangeSection(additions=(), removals=(), modifications=())
    assert render_surface_changes_lines(empty) == []
    assert render_surface_changes_section(empty) == []
    assert render_surface_changes_section(None) == []


def test_a_run_with_no_changes_renders_no_surface_changes_section() -> None:
    old = AbiSnapshot(library="libfoo", version="1.0")
    new = AbiSnapshot(library="libfoo", version="1.0")
    result = compare(old, new)
    section = compute_surface_changes(result)
    assert section.total == 0

    from abicheck.reporter_markdown import to_markdown

    assert "## Surface changes" not in to_markdown(result)


# ---------------------------------------------------------------------------
# Bounded rendering: `report.shared_display_budget_starvation`
# (`tests/regressions/manifest_report.py`). These state the *contract* of the
# per-group cap rather than pinning the one reported input: the original
# defect was a single budget consumed in group-declaration order, under which
# 12+ additions rendered a breaking removal as "all 1 omitted". A fixed-input
# regression test for "20 additions plus 1 removal" would have re-passed
# against any per-group cap *and* against several still-wrong shared-budget
# variants, so the properties below are checked over a generated population
# instead, against an oracle (`min(len(group), cap)`) that is deliberately not
# the renderer's own slicing expression.
# ---------------------------------------------------------------------------


def _surface_entry(symbol: str) -> SurfaceChangeEntry:
    return SurfaceChangeEntry(
        kind="func_removed",
        symbol=symbol,
        description="d",
        verdict="BREAKING",
        category="c",
        old_declaration="old",
        new_declaration="new",
        source_location=None,
    )


def _section(n_add: int, n_rem: int, n_mod: int) -> SurfaceChangeSection:
    return SurfaceChangeSection(
        additions=tuple(_surface_entry(f"add{i}") for i in range(n_add)),
        removals=tuple(_surface_entry(f"rem{i}") for i in range(n_rem)),
        modifications=tuple(_surface_entry(f"mod{i}") for i in range(n_mod)),
    )


def _shown_symbols(lines: list[str], prefix: str) -> list[str]:
    """The symbols actually itemized, read back out of the rendered Markdown."""
    return [
        line.split("**")[1]
        for line in lines
        if line.startswith("- **") and line.split("**")[1].startswith(prefix)
    ]


#: Deliberately spans the boundaries the cap can be wrong at: empty, below,
#: exactly at, and above `MAX_COMPACT_SURFACE_ITEMS`, in every combination.
_POPULATION_SIZES = (
    0,
    1,
    MAX_COMPACT_SURFACE_ITEMS - 1,
    MAX_COMPACT_SURFACE_ITEMS,
    MAX_COMPACT_SURFACE_ITEMS + 8,
)


@pytest.mark.parametrize("cap", [1, 3, MAX_COMPACT_SURFACE_ITEMS, 1000])
def test_every_group_gets_its_own_budget_regardless_of_the_other_groups(
    cap: int,
) -> None:
    """No group's shown count may depend on any other group's size.

    This is the property the shared budget violated. The oracle is
    ``min(len(group), cap)`` -- derived from the documented contract, not
    from the renderer's slicing.
    """
    failures: list[str] = []
    for n_add in _POPULATION_SIZES:
        for n_rem in _POPULATION_SIZES:
            for n_mod in _POPULATION_SIZES:
                lines = render_surface_changes_lines(
                    _section(n_add, n_rem, n_mod), limit=cap
                )
                for prefix, size in (("add", n_add), ("rem", n_rem), ("mod", n_mod)):
                    expected = min(size, cap)
                    actual = len(_shown_symbols(lines, prefix))
                    if actual != expected:
                        failures.append(
                            f"cap={cap} sizes=({n_add},{n_rem},{n_mod}) "
                            f"group={prefix!r}: showed {actual}, expected {expected}"
                        )
    assert not failures, "per-group budget violated:\n" + "\n".join(failures)


def test_a_starved_group_is_not_possible_for_any_addition_count() -> None:
    """The reported shape, generalized over the starving group's size.

    A single removal must stay visible no matter how many additions precede
    it -- the additions are what consumed the shared budget originally.
    """
    for n_add in range(0, MAX_COMPACT_SURFACE_ITEMS * 3):
        lines = render_surface_changes_lines(_section(n_add, 1, 1))
        assert _shown_symbols(lines, "rem") == ["rem0"], (
            f"a removal was starved by {n_add} additions"
        )
        assert _shown_symbols(lines, "mod") == ["mod0"]


def test_group_headings_always_state_the_complete_count_even_when_capped() -> None:
    """A capped body must never make the heading under-report the total."""
    for n_add in _POPULATION_SIZES:
        for n_rem in _POPULATION_SIZES:
            text = "\n".join(render_surface_changes_lines(_section(n_add, n_rem, 0)))
            if n_add or n_rem:
                assert f"**Additions** ({n_add})" in text
                assert f"**Removals** ({n_rem})" in text


def test_omissions_are_always_disclosed_and_the_disclosed_count_is_exact() -> None:
    """Whatever is not shown must be reported, with the right number."""
    for size in _POPULATION_SIZES:
        for cap in (0, 1, MAX_COMPACT_SURFACE_ITEMS):
            lines = render_surface_changes_lines(_section(0, size, 0), limit=cap)
            text = "\n".join(lines)
            omitted = size - min(size, cap)
            if omitted and min(size, cap):
                assert f"… {omitted} more removals omitted" in text
            elif omitted:
                assert f"… all {omitted} removals omitted" in text
            else:
                assert "omitted" not in text


def test_the_oracle_is_not_vacuous() -> None:
    """Guard the guard: the population must actually exercise capping.

    An oracle accidentally reduced to "never caps" would make every property
    above pass while asserting nothing.
    """
    assert any(size > MAX_COMPACT_SURFACE_ITEMS for size in _POPULATION_SIZES), (
        "no population size exceeds the cap, so capping is never exercised"
    )
    capped = render_surface_changes_lines(_section(0, MAX_COMPACT_SURFACE_ITEMS + 8, 0))
    assert "omitted" in "\n".join(capped)


def test_every_change_entity_is_classified_as_surface_or_non_surface() -> None:
    """Exhaustiveness, not just correctness of today's membership.

    The filter drops any finding whose entity is not in
    ``_PUBLIC_SURFACE_ENTITIES``. A `ChangeEntity` member added later would
    therefore vanish from this section silently -- no test, gate, or runtime
    error anywhere -- which is the `registry.kind_completeness` failure shape
    (`tests/regressions/manifest.py`): a missing classification that fails
    nothing. Adding a member must force a decision here.
    """
    from abicheck.change_registry import ChangeEntity
    from abicheck.report.surface_changes import (
        _NON_SURFACE_ENTITIES,
        _PUBLIC_SURFACE_ENTITIES,
    )

    declared = {e.value for e in ChangeEntity}
    classified = _PUBLIC_SURFACE_ENTITIES | _NON_SURFACE_ENTITIES
    assert _PUBLIC_SURFACE_ENTITIES.isdisjoint(_NON_SURFACE_ENTITIES), (
        "an entity is classified as both surface and non-surface"
    )
    assert classified == declared, (
        "ChangeEntity members missing a surface/non-surface classification: "
        f"{sorted(declared - classified)}; unknown members classified: "
        f"{sorted(classified - declared)}"
    )


def test_the_entity_filter_keeps_real_breaks_and_drops_only_non_declarations() -> None:
    """Suppressing noise and hiding a real break look identical from the
    noisy side, so both directions are asserted on one real comparison.

    The filter's purpose is to keep non-declaration dimensions (binary/build/
    source/analysis facts) out of the *declaration* surface listing. It must
    not cost a single real declaration change: every finding whose entity is
    a declaration entity has to survive into one of the three groups, and the
    groups together must account for exactly those findings — no more, no
    fewer.
    """
    from abicheck.report.change_operation import entity_for_change
    from abicheck.report.finding import report_findings_for
    from abicheck.report.surface_changes import _PUBLIC_SURFACE_ENTITIES

    old, new = _snapshots()
    result = compare(old, new)
    section = compute_surface_changes(result)

    listed = {
        e.symbol
        for e in (*section.additions, *section.removals, *section.modifications)
    }

    # Real break preserved: the removal is present, and in the right group.
    assert "_ZN3foo4goneEv" in {e.symbol for e in section.removals}

    # And the grouping is the catalog's answer, not a name-suffix guess.
    assert "_ZN3foo9brand_newEv" in {e.symbol for e in section.additions}
    assert "_ZN3foo7changedEv" in {e.symbol for e in section.modifications}

    # Nothing with a declaration entity was dropped, and nothing without one
    # was kept -- computed from the same findings the section was built from.
    expected = set()
    for finding in report_findings_for(result):
        kind = finding.change.kind
        kind_value = kind.value if hasattr(kind, "value") else str(kind)
        if entity_for_change(finding.change, kind_value) in _PUBLIC_SURFACE_ENTITIES:
            expected.add(finding.change.symbol)
    assert listed == expected, (
        f"filter dropped real declarations {sorted(expected - listed)} "
        f"or kept non-declarations {sorted(listed - expected)}"
    )
    assert section.total == len(
        section.additions + section.removals + section.modifications
    )
