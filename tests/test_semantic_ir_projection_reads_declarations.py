# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Bug class: the function/variable facts the SemanticIR detectors compare
must be projected from the declaration store at comparison time, never read
from a copy stored with (or cached on) the snapshot (one-semantic-pipeline.md,
6B closure follow-up, option (a)).

Two halves:

* a snapshot that is loaded and then *edited* in its declaration store
  compares exactly like a freshly built snapshot carrying the same edit --
  the oracle is the comparison of an independently built snapshot, not the
  projection helper; checked over several independent edits (return type,
  parameter type, noexcept, variable type);
* the persisted shape #1486 briefly wrote is refused per storage's
  schema-version rules: a v57 snapshot as newer than this build (57 is never
  reused), an IR document version 3 as unknown, and an IR entity carrying a
  projection fact as a stale copy that must never be read.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from abicheck.checker import compare
from abicheck.errors import IncompatibleSnapshotSchemaError
from abicheck.extract.semantic_normalizer import normalize_header_ast
from abicheck.model import AbiSnapshot, Function, Param, Variable
from abicheck.model.identity import entity_id_for_function, entity_id_for_variable
from abicheck.model.semantic_ir import PROJECTION_FIELD_NAMES
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict
from abicheck.storage.semantic_ir_codec import (
    IR_DOCUMENT_VERSION,
    semantic_ir_from_document,
    semantic_ir_to_document,
)


def _fn() -> Function:
    return Function(
        name="ns::f",
        mangled="_ZN2ns1fEi",
        return_type="int",
        params=[Param(name="a", type="int")],
        is_noexcept=False,
        entity_id=entity_id_for_function((), "f", mangled_name="_ZN2ns1fEi"),
    )


def _var() -> Variable:
    return Variable(
        name="ns::g",
        mangled="_ZN2ns1gE",
        type="int",
        is_const=False,
        entity_id=entity_id_for_variable((), "g", mangled_name="_ZN2ns1gE"),
    )


def _snap(fn: Function, var: Variable) -> AbiSnapshot:
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
        variables=[var],
    )
    return AbiSnapshot(
        library="l",
        version="1",
        functions=[fn],
        variables=[var],
        semantic_ir=ir,
        ast_producer="castxml",
    )


def _ret(f: Function, v: Variable) -> None:
    f.return_type = "long"


def _param(f: Function, v: Variable) -> None:
    f.params[0].type = "double"


def _noexcept(f: Function, v: Variable) -> None:
    f.is_noexcept = True


def _vtype(f: Function, v: Variable) -> None:
    v.type = "long"


_EDITS: list[Callable[[Function, Variable], None]] = [
    _ret,
    _param,
    _noexcept,
    _vtype,
]


def _findings(old: AbiSnapshot, new: AbiSnapshot) -> list[tuple[str, str]]:
    return sorted((c.kind.value, c.symbol) for c in compare(old, new).changes)


@pytest.mark.parametrize("edit", _EDITS, ids=lambda e: e.__name__.strip("_"))
def test_loaded_then_edited_snapshot_compares_like_a_fresh_one(edit) -> None:
    old = _snap(_fn(), _var())

    fresh_fn, fresh_var = _fn(), _var()
    edit(fresh_fn, fresh_var)
    expected = _findings(old, _snap(fresh_fn, fresh_var))
    assert expected, "the edit must be observable at all"

    loaded = snapshot_from_dict(snapshot_to_dict(_snap(_fn(), _var())))
    # Compare once first so any lazily built index exists before the edit.
    assert _findings(old, loaded) == []
    edit(loaded.declarations.functions[0], loaded.declarations.variables[0])
    assert _findings(old, loaded) == expected
    # The edited copy still round-trips to the same answer.
    assert _findings(old, snapshot_from_dict(snapshot_to_dict(loaded))) == expected


def test_v57_snapshot_is_refused_as_newer_than_this_build() -> None:
    assert SCHEMA_VERSION == 56, "57 must never be reused: bump straight to 58"
    doc = copy.deepcopy(snapshot_to_dict(_snap(_fn(), _var())))
    doc["schema_version"] = 57
    with pytest.raises(IncompatibleSnapshotSchemaError, match="57"):
        snapshot_from_dict(doc)


def test_ir_document_version_3_is_refused() -> None:
    assert IR_DOCUMENT_VERSION == 2
    document = semantic_ir_to_document(_snap(_fn(), _var()).semantic_ir, {})
    document["semantic_ir"]["version"] = 3
    with pytest.raises(ValueError, match="version 3"):
        semantic_ir_from_document(document)


def _first_entity(node: Any) -> dict[str, Any] | None:
    if isinstance(node, dict):
        if "canonical_spelling" in node:
            return node
        values = list(node.values())
    elif isinstance(node, list):
        values = node
    else:
        return None
    for value in values:
        found = _first_entity(value)
        if found is not None:
            return found
    return None


@pytest.mark.parametrize("fact", sorted(PROJECTION_FIELD_NAMES))
def test_ir_entity_carrying_a_projection_fact_is_refused(fact: str) -> None:
    document = semantic_ir_to_document(_snap(_fn(), _var()).semantic_ir, {})
    entity = _first_entity(document)
    assert entity is not None
    entity[fact] = {"status": "present", "value": "stale"}
    with pytest.raises(ValueError, match="never persisted"):
        semantic_ir_from_document(document)
