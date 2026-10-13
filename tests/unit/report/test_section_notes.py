# SPDX-License-Identifier: Apache-2.0
"""Direct tests for the notes compute half in ``report/section_notes.py``.

The four ``compute_*`` functions return ``None`` for an absent section and a
struct for a present one. Inputs are built by hand so each branch is reached
without a full comparison run, and expected values are literals rather than
the helpers under test.
"""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import DiffResult
from abicheck.contract_relevance_types import ContractRelevance
from abicheck.model.change import Change
from abicheck.report.section_notes import (
    compute_out_of_surface_note,
    compute_redundancy_note,
    compute_rtti_note,
    compute_suppression_note,
)


def _result(**kwargs: object) -> DiffResult:
    return DiffResult(old_version="1.0", new_version="2.0", library="libx.so", **kwargs)


def _change(symbol: str) -> Change:
    return Change(kind=ChangeKind.EXPORTED_NOT_PUBLIC, symbol=symbol, description="d")


# -- redundancy -------------------------------------------------------------


def test_redundancy_note_absent_when_nothing_hidden() -> None:
    assert compute_redundancy_note(_result(redundant_count=0)) is None


def test_redundancy_note_reports_hidden_count() -> None:
    note = compute_redundancy_note(_result(redundant_count=3))
    assert note is not None
    assert note.redundant_count == 3
    assert note.reasons  # a non-empty reason string is always present


# -- out of surface ---------------------------------------------------------


def test_out_of_surface_note_absent_when_scope_is_off() -> None:
    result = _result(scope_to_public_surface=False, out_of_surface_count=5)
    assert compute_out_of_surface_note(result) is None


def test_out_of_surface_note_absent_when_none_excluded() -> None:
    result = _result(scope_to_public_surface=True, out_of_surface_count=0)
    assert compute_out_of_surface_note(result) is None


def test_out_of_surface_note_reports_excluded_count() -> None:
    result = _result(scope_to_public_surface=True, out_of_surface_count=7)
    note = compute_out_of_surface_note(result)
    assert note is not None and note.count == 7


# -- suppression ------------------------------------------------------------


def test_suppression_note_absent_without_a_suppression_file() -> None:
    result = _result(suppression_file_provided=False, suppressed_count=2)
    assert compute_suppression_note(result) is None


def test_suppression_note_with_no_matches_has_no_entries() -> None:
    result = _result(suppression_file_provided=True, suppressed_count=0)
    note = compute_suppression_note(result)
    assert note is not None
    assert note.suppressed_count == 0
    assert note.entries == ()


def test_suppression_note_lists_each_suppressed_change() -> None:
    change = _change("_Z3barv")
    result = _result(
        suppression_file_provided=True,
        suppressed_count=1,
        suppressed_changes=[change],
    )
    note = compute_suppression_note(result)
    assert note is not None
    assert note.suppressed_count == 1
    assert len(note.entries) == 1
    entry = note.entries[0]
    assert entry.symbol == "_Z3barv"
    assert entry.description == "d"
    # No contract relevance on the change, so no contract decision text.
    assert entry.contract_text is None


def test_suppression_entry_carries_contract_decision_text() -> None:
    change = _change("_Z3barv")
    change.contract_relevance = ContractRelevance.PROVEN_OUT_OF_CONTRACT
    change.contract_reason_code = "private-entity"
    result = _result(
        suppression_file_provided=True,
        suppressed_count=1,
        suppressed_changes=[change],
    )
    note = compute_suppression_note(result)
    assert note is not None
    assert note.entries[0].contract_text == (
        f"{ContractRelevance.PROVEN_OUT_OF_CONTRACT.value} (private-entity)"
    )


# -- RTTI -------------------------------------------------------------------


def test_rtti_note_absent_when_no_rtti_or_internal_symbols() -> None:
    assert compute_rtti_note([_change("_Z3barv")]) is None


def test_rtti_note_counts_rtti_internal_and_public() -> None:
    # `_ZTI3Foo` is a typeinfo (RTTI) symbol; `_ZN4daal8internal3FooEv` has an
    # `internal` namespace component; `_Z3barv` is an ordinary public symbol.
    changes = [
        _change("_ZTI3Foo"),
        _change("_ZN4daal8internal3FooEv"),
        _change("_Z3barv"),
    ]
    note = compute_rtti_note(changes)
    assert note is not None
    assert (note.rtti, note.internal, note.public, note.total) == (1, 1, 1, 3)
