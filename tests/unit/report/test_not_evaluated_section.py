from __future__ import annotations

from abicheck.checker import Change, ChangeKind
from abicheck.contract_relevance_types import ContractRelevance
from abicheck.report.not_evaluated_section import compute_not_evaluated


def test_no_findings_yields_no_section() -> None:
    assert compute_not_evaluated([]) is None


def test_entries_keep_input_order_with_label_and_reason_suffix() -> None:
    proven = Change(
        ChangeKind.FUNC_REMOVED,
        "a",
        "func_removed on a",
        contract_relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
        contract_reason_code="internal_namespace",
    )
    unknown = Change(
        ChangeKind.FUNC_REMOVED,
        "b",
        "func_removed on b",
        contract_relevance=ContractRelevance.UNKNOWN_UNPROVEN,
    )

    section = compute_not_evaluated([proven, unknown])

    assert section is not None
    assert [(e.change, e.label, e.suffix) for e in section.entries] == [
        (proven, "PROVEN_OUT_OF_CONTRACT", " (internal_namespace)"),
        (unknown, "UNKNOWN_UNPROVEN", ""),
    ]


def test_missing_relevance_is_labelled_unknown_not_dropped() -> None:
    bare = Change(ChangeKind.FUNC_REMOVED, "c", "func_removed on c")

    section = compute_not_evaluated([bare])

    assert section is not None
    assert len(section.entries) == 1
    assert section.entries[0].change is bare
    assert section.entries[0].label == "UNKNOWN"
    assert section.entries[0].suffix == ""
