"""Every finding the AST/DWARF step collapses is in the ledger, with its reason.

The step drops a DWARF-lane finding that a declared ``subsumed_by`` relation
makes redundant (ADR-093 D4). ADR-067 requires that drop to stay visible: each
dropped finding is recorded as ``deduplicated`` with a ``reason_code`` naming
the subsumer and the declared key. Here the count of records must equal the
count of drops, and each record must name the subsumer the registry declares.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from abicheck.compare.identity_memo import IdentityMemo
from abicheck.model import AbiSnapshot
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind as K
from abicheck.policy.disposition_ledger import DispositionLedger
from abicheck.policy.disposition_types import Disposition
from abicheck.policy.finding_disposition.dedup_ast_dwarf_step import (
    DeduplicateAstDwarf,
)


def _change(kind: K, symbol: str, field: str | None = None) -> Change:
    return Change(kind=kind, symbol=symbol, description="d", field_name=field)


_SNAP = AbiSnapshot(library="lib.so", version="1")


def _run(changes: list[Change]) -> tuple[list[Change], DispositionLedger]:
    ledger = DispositionLedger()
    ctx = SimpleNamespace(
        old=_SNAP,
        new=_SNAP,
        disposition_ledger=ledger,
        identity_memo=IdentityMemo(),
    )
    kept = DeduplicateAstDwarf().run(list(changes), ctx)
    return kept, ledger


@pytest.mark.parametrize(
    ("dropped", "kept_kind", "key"),
    [
        (
            _change(K.STRUCT_SIZE_CHANGED, "Point"),
            K.TYPE_SIZE_CHANGED,
            "entity",
        ),
        (
            _change(K.STRUCT_FIELD_OFFSET_CHANGED, "Point", "x"),
            K.TYPE_FIELD_OFFSET_CHANGED,
            "entity_field",
        ),
        (
            _change(K.STRUCT_FIELD_TYPE_CHANGED, "Point", "x"),
            K.UNION_FIELD_TYPE_CHANGED,
            "entity_field",
        ),
    ],
    ids=["size", "field_offset", "field_type_union_partner"],
)
def test_dropped_duplicate_is_recorded_with_its_declared_subsumer(
    dropped: Change, kept_kind: K, key: str
) -> None:
    partner = _change(kept_kind, dropped.symbol, dropped.field_name)
    kept, ledger = _run([dropped, partner])
    assert [c.kind for c in kept] == [kept_kind]
    records = [r for r in ledger.records if r.kind == dropped.kind.value]
    assert len(records) == 1
    assert records[0].disposition is Disposition.DEDUPLICATED
    assert records[0].reason_code == f"subsumed_by:{kept_kind.value}:{key}"


def test_record_count_equals_drop_count_over_a_mixed_input() -> None:
    changes = [
        _change(K.STRUCT_SIZE_CHANGED, "A"),
        _change(K.TYPE_SIZE_CHANGED, "A"),
        _change(K.STRUCT_FIELD_REMOVED, "B", "x"),
        _change(K.TYPE_FIELD_REMOVED, "B", "x"),
        _change(K.STRUCT_SIZE_CHANGED, "Unpaired"),
    ]
    kept, ledger = _run(changes)
    dropped = len(changes) - len(kept)
    assert dropped == 2
    subsumed = [
        r
        for r in ledger.records
        if r.reason_code is not None and r.reason_code.startswith("subsumed_by:")
    ]
    assert len(subsumed) == dropped
    assert {r.kind for r in subsumed} == {
        K.STRUCT_SIZE_CHANGED.value,
        K.STRUCT_FIELD_REMOVED.value,
    }
    # The unpaired DWARF finding is kept and recorded by no subsumption.
    unpaired = [r for r in ledger.records if r.symbol == "Unpaired"]
    assert all(r.reason_code is None for r in unpaired)
