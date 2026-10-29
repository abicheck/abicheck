"""The schema-v69 ABI fact survives a save and a load, and an old document never proves equality.

Stored-versus-live (ADR-062): a document written before schema v69 carries no
``type_abi_fact``, so its slots decode with no fact and the retype refinement
(compare/abi_equivalent_retype.py) leaves their break as it was. A live slot
and a stored one of the same declaration must agree on the fact they carry.
"""

from __future__ import annotations

import copy

import pytest

from abicheck.model import AbiSnapshot
from abicheck.model.abi_fundamental import AbiFundamental, FundamentalEncoding
from abicheck.model.declarations import Function, Param
from abicheck.model.fact import Fact, FactStatus
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.storage.fact_decode import decode_abi_slot_fact
from abicheck.storage.snapshot_schema_versions import SCHEMA_VERSION

_LONG = AbiFundamental(FundamentalEncoding.SIGNED, 8, 8)


def _snap() -> AbiSnapshot:
    fn = Function(
        name="f",
        mangled="f",
        return_type="void",
        params=[Param(name="p", type="long", type_abi_fact=Fact.present(_LONG))],
    )
    return AbiSnapshot(library="l", version="1", functions=[fn])


def test_schema_version_is_the_one_that_introduced_the_facts() -> None:
    assert SCHEMA_VERSION >= 69


def test_fact_round_trips_through_a_save_and_a_load() -> None:
    loaded = snapshot_from_dict(snapshot_to_dict(_snap()))
    (fn,) = loaded.declarations.functions
    (param,) = fn.params
    assert param.type_abi_fact is not None
    assert param.type_abi_fact.status is FactStatus.PRESENT
    assert param.type_abi_fact.value == _LONG


def test_an_absent_fact_is_not_written() -> None:
    """Every other producer's snapshot encodes exactly as before: no key."""
    snap = AbiSnapshot(
        library="l",
        version="1",
        functions=[
            Function(
                name="g",
                mangled="g",
                return_type="void",
                params=[Param(name="p", type="int *")],
            )
        ],
    )
    doc = snapshot_to_dict(snap)
    fn = next(f for f in _functions(doc) if f["name"] == "g")
    assert "type_abi_fact" not in fn["params"][0]
    assert "return_type_abi_fact" not in fn


def _functions(doc: dict) -> list[dict]:
    sections = doc.get("sections")
    if isinstance(sections, dict) and "declarations" in sections:
        return sections["declarations"]["payload"]["functions"]
    return doc["functions"]


def test_a_pre_v69_document_decodes_the_fact_as_unset() -> None:
    """A stored baseline from before schema 69 has no fact, so it proves nothing."""
    raw = {
        "status": "present",
        "value": {"encoding": "signed", "size": 8, "align": 8, "lanes": 0},
    }
    assert (
        decode_abi_slot_fact(raw, 68) is not None
    )  # present keys are read at any schema
    assert decode_abi_slot_fact(None, 68) is None  # absent at an old schema: unset
    absent = decode_abi_slot_fact(None, SCHEMA_VERSION)
    assert absent is not None and absent.status is FactStatus.NOT_COLLECTED


@pytest.mark.parametrize(
    "value",
    [
        {"encoding": "bogus", "size": 8, "align": 8, "lanes": 0},
        {"encoding": "signed", "size": 0, "align": 8, "lanes": 0},
        {"encoding": "signed"},
        "not a mapping",
    ],
)
def test_a_malformed_stored_fact_is_never_read_as_evidence(value) -> None:
    fact = decode_abi_slot_fact({"status": "present", "value": value}, SCHEMA_VERSION)
    assert fact is not None
    assert fact.status is FactStatus.NOT_COLLECTED


def test_a_stored_document_without_the_fact_keeps_its_break() -> None:
    """Stored-versus-live: the old document's slot has no fact, so the refinement declines."""
    from abicheck.compare.abi_equivalent_retype import refine_abi_equivalent_retypes
    from abicheck.model.change import Change
    from abicheck.model.change_catalog.kinds import ChangeKind

    doc = copy.deepcopy(snapshot_to_dict(_snap()))
    for fn in _functions(doc):
        for p in fn["params"]:
            p.pop("type_abi_fact", None)
    old = snapshot_from_dict(doc)
    new = _snap()
    new.declarations.functions[0].params[0] = Param(
        name="p", type="long long", type_abi_fact=Fact.present(_LONG)
    )
    change = Change(
        kind=ChangeKind.FUNC_PARAMS_CHANGED,
        symbol="f",
        description="x",
        old_value="(long)",
        new_value="(long long)",
    )
    (out,) = refine_abi_equivalent_retypes([change], old, new)
    assert out.kind is ChangeKind.FUNC_PARAMS_CHANGED
