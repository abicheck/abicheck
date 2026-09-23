"""``_collect_strings`` decides a dataclass's visited fields once per type.

The oracle is the pre-change per-node implementation (same checks, split
into per-branch helpers),
so any divergence in *which* strings are collected -- or in their order,
which ``collect_anonymous_type_ordinals`` consumes -- fails.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Mapping
from typing import Any

from hypothesis import HealthCheck, given, settings

from abicheck.model import AbiSnapshot
from abicheck.model.fact import Fact
from abicheck.qualified_name_segments_walk import (
    _PAYLOAD_FIELD_EXCLUSIONS,
    _collect_strings,
    _legacy_sibling_is_payload_excluded,
)
from abicheck.storage.closure_identity import _LAMBDA_IDENTITY_FIELDS
from tests.test_property_based import snapshot_st


def _reference(value: object, out: list[str]) -> None:
    if isinstance(value, str) and not isinstance(value, enum.Enum):
        out.append(value)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        _reference_dataclass(value, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reference(item, out)
    elif isinstance(value, Mapping):
        _reference_mapping(value, out)


def _reference_dataclass(value: Any, out: list[str]) -> None:
    fields = dataclasses.fields(value)
    is_fact = type(value).__name__ == "Fact" and {f.name for f in fields} == {
        "status",
        "value",
        "diagnostics",
        "producer",
    }
    for f in fields:
        excluded = (
            f.name in _PAYLOAD_FIELD_EXCLUSIONS
            or _legacy_sibling_is_payload_excluded(f.name)
        )
        if excluded or (is_fact and f.name == "status"):
            continue
        _reference(getattr(value, f.name), out)


def _reference_mapping(value: Mapping[Any, Any], out: list[str]) -> None:
    for k, v in value.items():
        if isinstance(k, str) and not isinstance(k, enum.Enum):
            out.append(k)
        elif dataclasses.is_dataclass(k) and not isinstance(k, type):
            _reference(k, out)
        _reference(v, out)


def _both(value: object) -> tuple[list[str], list[str]]:
    got: list[str] = []
    want: list[str] = []
    _collect_strings(value, got)
    _reference(value, want)
    return got, want


class Mode(str, enum.Enum):
    A = "(lambda:a.h#1)"


class _Str(str):
    pass


@dataclasses.dataclass(frozen=True)
class Key:
    leaf: str
    deprecated: str = "(unnamed payload)"


@dataclasses.dataclass
class Node:
    name: str
    deprecated: str
    source_header: str
    source_header_fact: Any
    qualified_name_fact: Any
    mode: Mode
    kids: list[Any]
    table: dict[Any, Any]
    pair: tuple[Any, ...]
    cls: type


def test_edge_cases_match_the_reference() -> None:
    fact = Fact.present("(lambda:x.h:1:2)", producer="castxml")
    node = Node(
        name="ns::(anonymous struct:a.h:1:1)",
        deprecated="avoid (lambda:x.h:10:2)",
        source_header="/tmp/(lambda:a.h:1:2)/api.h",
        source_header_fact=fact,
        qualified_name_fact=fact,
        mode=Mode.A,
        kids=[_Str("sub"), 3, None, 2.5, [["deep"]], Key("k")],
        table={"key": "v", Key("dk"): ("t1", "t2"), Mode.A: "enum-key-value", 7: "x"},
        pair=("a", ("b",)),
        cls=Node,
    )
    got, want = _both(node)
    assert got == want
    assert "avoid (lambda:x.h:10:2)" not in got
    assert "ns::(anonymous struct:a.h:1:1)" in got
    # Second pass reuses the cached plan and must not diverge either.
    assert _both(node) == (want, want)


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(snap=snapshot_st())
def test_generated_snapshots_match_the_reference(snap: AbiSnapshot) -> None:
    for name in _LAMBDA_IDENTITY_FIELDS:
        got, want = _both(getattr(snap, name))
        assert got == want, name
