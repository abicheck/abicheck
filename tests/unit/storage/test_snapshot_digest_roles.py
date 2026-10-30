"""ADR-062 D16: one digest per question.

Bug class: the one snapshot digest answered two questions at once, so a
re-dump that differs only in capture time or in producer traversal order
read as different *evidence* although `compare` reports no change.

Invariants, each checked against an oracle that is not the digest code:

* evidence: every permutation of every declared unordered collection, and
  any value of a volatile capture field, leaves the digest unchanged --
  and `checker.compare` agrees there is nothing to report;
* evidence: a real fact change, including a reorder of a semantically
  ordered array, changes the digest;
* publication: keeps the historical meaning, byte for byte -- a plain
  sha256 of `snapshot_to_json` -- and does move with `created_at`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools

import pytest

from abicheck.checker import compare
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol
from abicheck.model import EnumMember, EnumType, Function, Param, RecordType, Variable
from abicheck.model.snapshot import AbiSnapshot
from abicheck.serialization import snapshot_content_digest, snapshot_to_json
from abicheck.storage.snapshot_digests import (
    UNORDERED_COLLECTIONS,
    VOLATILE_CAPTURE_FIELDS,
    snapshot_evidence_digest,
    snapshot_publication_digest,
)


def _spec(**overrides: object) -> dict[str, object]:
    """Constructor arguments of the base snapshot, built fresh each call."""
    fields: dict[str, object] = {
        "library": "libdemo.so.1",
        "version": "1.0",
        "functions": [
            Function(name=n, mangled=f"_Z1{n}v", return_type="int")
            for n in ("a", "b", "c")
        ],
        "variables": [
            Variable(name=n, mangled=n, type="int") for n in ("va", "vb", "vc")
        ],
        "types": [RecordType(name=n, kind="struct") for n in ("Ta", "Tb", "Tc")],
        "enums": [
            EnumType(name=n, members=[EnumMember(name=f"{n}_0", value=0)])
            for n in ("Ea", "Eb", "Ec")
        ],
        "elf": ElfMetadata(
            soname="libdemo.so.1",
            needed=["liba.so", "libb.so"],
            symbols=[ElfSymbol(name=n) for n in ("sa", "sb", "sc")],
            imports=[ElfImport(name=n) for n in ("ia", "ib", "ic")],
        ),
    }
    fields.update(overrides)
    return fields


def _base(**overrides: object) -> AbiSnapshot:
    return AbiSnapshot(**_spec(**overrides))  # type: ignore[arg-type]


def _permuted(parent: str | None, field: str, order: tuple[int, ...]) -> AbiSnapshot:
    spec = _spec()
    if parent is None:
        items = spec[field]
        spec[field] = [items[i] for i in order]  # type: ignore[index]
    else:
        owner = spec[parent]
        items = getattr(owner, field)
        spec[parent] = dataclasses.replace(owner, **{field: [items[i] for i in order]})  # type: ignore[call-overload]
    return AbiSnapshot(**spec)  # type: ignore[arg-type]


_PERMUTATION_CASES = [
    (parent, field, order)
    for parent, field in UNORDERED_COLLECTIONS
    for order in itertools.permutations(range(3))
    if order != (0, 1, 2)
]


@pytest.mark.parametrize(("parent", "field", "order"), _PERMUTATION_CASES)
def test_reordering_an_unordered_collection_keeps_the_evidence_digest(
    parent: str | None, field: str, order: tuple[int, ...]
) -> None:
    base = _base()
    permuted = _permuted(parent, field, order)
    assert snapshot_evidence_digest(permuted) == snapshot_evidence_digest(base)
    # The oracle: the comparison itself sees no change.
    assert compare(base, permuted).changes == []
    # Publication is the exact artifact, so the written order is part of it.
    assert snapshot_publication_digest(permuted) != snapshot_publication_digest(base)


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("created_at", ("2026-01-01T00:00:00Z", "2026-10-30T12:34:56Z")),
        ("source_mtime", (1.0, 1798000000.5)),
    ],
)
def test_volatile_capture_metadata_is_outside_the_evidence_domain(
    field: str, values: tuple[object, object]
) -> None:
    assert field in VOLATILE_CAPTURE_FIELDS
    left, right = (_base(**{field: v}) for v in values)
    assert snapshot_evidence_digest(left) == snapshot_evidence_digest(right)
    assert snapshot_evidence_digest(left) == snapshot_evidence_digest(_base())
    assert snapshot_publication_digest(left) != snapshot_publication_digest(right)


def _with_function(index: int, **changes: object) -> AbiSnapshot:
    spec = _spec()
    functions = list(spec["functions"])  # type: ignore[call-overload]
    functions[index] = dataclasses.replace(functions[index], **changes)
    spec["functions"] = functions
    return AbiSnapshot(**spec)  # type: ignore[arg-type]


def _without_first_function() -> AbiSnapshot:
    spec = _spec()
    spec["functions"] = spec["functions"][1:]  # type: ignore[index]
    return AbiSnapshot(**spec)  # type: ignore[arg-type]


def _reversed_needed() -> AbiSnapshot:
    spec = _spec()
    elf = spec["elf"]
    spec["elf"] = dataclasses.replace(elf, needed=list(reversed(elf.needed)))  # type: ignore[attr-defined,type-var]
    return AbiSnapshot(**spec)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda: _with_function(0, return_type="long"), id="return-type"),
        pytest.param(_without_first_function, id="removed"),
        pytest.param(
            lambda: _with_function(
                1, params=[Param(name="x", type="int"), Param(name="y", type="char")]
            ),
            id="params-added",
        ),
        pytest.param(_reversed_needed, id="dt-needed-order-is-semantic"),
        pytest.param(lambda: _base(version="2.0"), id="version"),
    ],
)
def test_a_real_fact_change_changes_the_evidence_digest(mutate: object) -> None:
    changed = mutate()  # type: ignore[operator]
    assert snapshot_evidence_digest(changed) != snapshot_evidence_digest(_base())


def test_parameter_order_is_semantic_not_normalized() -> None:
    params = [Param(name="x", type="int"), Param(name="y", type="char")]
    left = _with_function(0, params=params)
    right = _with_function(0, params=list(reversed(params)))
    assert snapshot_evidence_digest(left) != snapshot_evidence_digest(right)


def test_the_publication_digest_keeps_its_historical_meaning() -> None:
    """Not redefined: the same plain sha256 of the written JSON that
    `snapshot_content_digest` has always returned, under an explicit name."""
    snap = _base(created_at="2026-10-30T00:00:00Z")
    expected = hashlib.sha256(snapshot_to_json(snap).encode()).hexdigest()
    assert (
        snapshot_publication_digest(snap) == snapshot_content_digest(snap) == expected
    )


def test_the_two_roles_never_share_a_spelling() -> None:
    snap = _base()
    evidence = snapshot_evidence_digest(snap)
    publication = snapshot_publication_digest(snap)
    assert evidence.startswith("sha256:")
    assert ":" not in publication
    assert evidence.removeprefix("sha256:") != publication
