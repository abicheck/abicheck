"""Schema v52: ``AbiSnapshot.extraction_scope`` and per-entity ownership
round-trip, and a pre-v52 snapshot loads as *unrecorded*/*unknown*
(ADR-075 D1/D2)."""

from __future__ import annotations

import json

import pytest

from abicheck.model import AbiSnapshot, EnumType, Function, RecordType, Variable
from abicheck.model.extraction_scope import (
    EntityOwnership,
    ExtractionScope,
    ownership_of,
)
from abicheck.model.fact import Fact
from abicheck.model.ownership_rules import DependencyRoots, OwnershipRules
from abicheck.serialization import snapshot_from_dict, snapshot_to_json
from abicheck.storage.snapshot_schema_versions import SCHEMA_VERSION

_TARGET = EntityOwnership("target", "public", "target_root:include")
_DEP = EntityOwnership("dependency:fmt", "external", "dependency:fmt:third/fmt")


def _snapshot() -> AbiSnapshot:
    f1 = Function(name="api", mangled="api", return_type="int")
    f2 = Function(name="fmt::format", mangled="_ZN3fmt6formatEv", return_type="int")
    f3 = Function(name="unclassified", mangled="unclassified", return_type="int")
    f1.ownership_fact = Fact.present(_TARGET)
    f2.ownership_fact = Fact.present(_DEP)
    v = Variable(name="g", mangled="g", type="int")
    v.ownership_fact = Fact.present(_TARGET)
    t = RecordType(name="S", kind="struct")
    t.ownership_fact = Fact.present(_DEP)
    e = EnumType(name="E", members=[])
    snap = AbiSnapshot(
        library="lib",
        version="1",
        functions=[f1, f2, f3],
        variables=[v],
        types=[t],
        enums=[e],
        from_headers=True,
    )
    snap.extraction_scope = ExtractionScope(
        ownership_rules=OwnershipRules(
            target_roots=("include",),
            dependencies=(DependencyRoots("fmt", ("third/fmt",)),),
            private_namespaces=("lib::detail",),
        ),
        diagnostics=(
            "x is declared in a target file but in dependency namespace 'fmt'",
        ),
    )
    return snap


def _round_trip(snap: AbiSnapshot) -> AbiSnapshot:
    return snapshot_from_dict(json.loads(snapshot_to_json(snap)))


def test_schema_version_is_52() -> None:
    assert SCHEMA_VERSION == 52


def test_scope_and_every_decision_round_trip() -> None:
    snap = _snapshot()
    back = _round_trip(snap)
    assert back.extraction_scope == snap.extraction_scope
    assert back.extraction_scope is not None
    assert back.extraction_scope.fingerprint == snap.extraction_scope.fingerprint
    for kind in ("functions", "variables", "types", "enums"):
        expected = [ownership_of(d) for d in getattr(snap, kind)]
        assert [ownership_of(d) for d in getattr(back, kind)] == expected, kind


def test_unclassified_declaration_stays_unknown_after_round_trip() -> None:
    back = _round_trip(_snapshot())
    assert ownership_of(back.functions[2]) is None
    assert ownership_of(back.enums[0]) is None


def test_declaration_lists_carry_no_per_entity_field() -> None:
    doc = json.loads(snapshot_to_json(_snapshot()))
    text = json.dumps(doc)
    assert "ownership_fact" not in text
    block = _find(doc, "extraction_scope")
    ownership = block["entity_ownership"]
    # Interned: two distinct decisions for five classified declarations.
    assert len(ownership["decisions"]) == 2
    assert ownership["functions"][2] == -1
    # A list with no classified declaration is omitted, not written as -1s.
    assert "enums" not in ownership


def test_unrecorded_snapshot_writes_no_block() -> None:
    snap = AbiSnapshot(library="lib", version="1", from_headers=True)
    assert "extraction_scope" not in json.dumps(json.loads(snapshot_to_json(snap)))
    assert _round_trip(snap).extraction_scope is None


def _find(doc: object, key: str) -> dict:
    """The first mapping stored under *key* anywhere in *doc* (the sectioned
    envelope nests metadata under its section)."""
    if isinstance(doc, dict):
        if key in doc and isinstance(doc[key], dict):
            return doc[key]
        for value in doc.values():
            try:
                return _find(value, key)
            except KeyError:
                continue
    if isinstance(doc, list):
        for value in doc:
            try:
                return _find(value, key)
            except KeyError:
                continue
    raise KeyError(key)


def _flat(snap: AbiSnapshot) -> dict:
    from abicheck.storage.snapshot_encode import snapshot_to_dict

    return snapshot_to_dict(snap)


def test_pre_v52_snapshot_loads_unrecorded_and_unknown() -> None:
    d = _flat(_snapshot())
    d.pop("extraction_scope")
    d["schema_version"] = 51
    back = snapshot_from_dict(d)
    assert back.extraction_scope is None
    assert all(
        ownership_of(x) is None
        for x in (*back.functions, *back.variables, *back.types, *back.enums)
    )


def test_list_length_mismatch_leaves_that_list_unclassified() -> None:
    d = _flat(_snapshot())
    d["extraction_scope"]["entity_ownership"]["functions"].append(0)
    back = snapshot_from_dict(d)
    assert [ownership_of(f) for f in back.functions] == [None, None, None]
    # Other lists are unaffected.
    assert ownership_of(back.variables[0]) == _TARGET


@pytest.mark.parametrize("bad", [7, -3, "0", None])
def test_out_of_range_or_malformed_index_is_unclassified(bad: object) -> None:
    d = _flat(_snapshot())
    d["extraction_scope"]["entity_ownership"]["functions"][0] = bad
    back = snapshot_from_dict(d)
    assert ownership_of(back.functions[0]) is None
    assert ownership_of(back.functions[1]) == _DEP


def test_a_mode_this_build_cannot_produce_is_kept_verbatim() -> None:
    d = _flat(_snapshot())
    d["extraction_scope"]["dependency_evidence"] = "referenced"
    back = snapshot_from_dict(d)
    assert back.extraction_scope is not None
    assert back.extraction_scope.dependency_evidence == "referenced"


def test_fingerprint_ignores_order_and_duplicates() -> None:
    a = ExtractionScope(
        OwnershipRules(
            target_roots=("b", "a", "a"),
            dependencies=(
                DependencyRoots("y", ("2", "1")),
                DependencyRoots("x", ("3",)),
            ),
        )
    )
    b = ExtractionScope(
        OwnershipRules(
            target_roots=("a", "b"),
            dependencies=(
                DependencyRoots("x", ("3",)),
                DependencyRoots("y", ("1", "2")),
            ),
        )
    )
    assert a.fingerprint == b.fingerprint
    c = ExtractionScope(OwnershipRules(target_roots=("a",)))
    assert c.fingerprint != a.fingerprint
