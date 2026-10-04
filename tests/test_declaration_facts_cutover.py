# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-063 6B, declaration-fact cohort: deprecation, access and declared
alignment read through ``SemanticIR``.

The oracle is the documented legacy contract written out by hand (compare a
value only when both sides established it; narrowing is less accessible),
not a call into the detector.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.checker import ChangeKind, compare
from abicheck.compare.declaration_facts import (
    access_changes,
    alignment_changes,
    deprecation_changes,
)
from abicheck.compare.declined_comparisons import declined_scope
from abicheck.extract.semantic_normalizer import normalize_header_ast
from abicheck.model import AbiSnapshot, AccessLevel, Fact, Function, Variable
from abicheck.model.identity import entity_id_for_function, entity_id_for_variable
from abicheck.model.semantic_ir_declaration_facts import declaration_facts
from abicheck.model.semantic_ir_variable_payload import variable_canonical_entity
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

_DEPRECATION = st.sampled_from([None, "", "use g2"])
_ACCESS = st.sampled_from(list(AccessLevel))
_ALIGN = st.sampled_from([None, 32, 64])
_RANK = {AccessLevel.PUBLIC: 0, AccessLevel.PROTECTED: 1, AccessLevel.PRIVATE: 2}


def _var(deprecated, access, align, *, ident=True) -> Variable:
    return Variable(
        name="ns::g",
        mangled="_ZN2ns1gE",
        type="int",
        deprecated=deprecated,
        deprecated_fact=Fact.present(deprecated),
        access=access,
        access_fact=Fact.present(access),
        alignment_bits=align,
        entity_id=(
            entity_id_for_variable((), "g", mangled_name="_ZN2ns1gE") if ident else None
        ),
    )


def _snap(var: Variable, *, with_ir: bool) -> AbiSnapshot:
    ir = (
        normalize_header_ast(
            types=[],
            enums=[],
            typedefs_qualified={},
            typedef_entity_ids={},
            producer="castxml",
            variables=[var],
        )
        if with_ir
        else None
    )
    return AbiSnapshot(
        library="l",
        version="1",
        variables=[var],
        semantic_ir=ir,
        ast_producer="castxml",
    )


def _oracle(o: Variable, n: Variable) -> list[ChangeKind]:
    out = []
    if o.deprecated is None and n.deprecated is not None:
        out.append(ChangeKind.VAR_DEPRECATED_ADDED)
    elif o.deprecated is not None and n.deprecated is None:
        out.append(ChangeKind.VAR_DEPRECATED_REMOVED)
    if o.access != n.access:
        out.append(
            ChangeKind.VAR_ACCESS_CHANGED
            if _RANK[n.access] > _RANK[o.access]
            else ChangeKind.VAR_ACCESS_WIDENED
        )
    if o.alignment_bits is not None and n.alignment_bits is not None:
        if o.alignment_bits != n.alignment_bits:
            out.append(ChangeKind.VAR_ALIGNMENT_CHANGED)
    return out


def _entity(snap: AbiSnapshot):
    from abicheck.diff_symbols_variables import variable_type_index_for

    (var,) = snap.declarations.variables
    return var, variable_type_index_for(snap, [var]).entity_for(var)


@settings(max_examples=300, deadline=None)
@given(
    dep=st.tuples(_DEPRECATION, _DEPRECATION),
    acc=st.tuples(_ACCESS, _ACCESS),
    align=st.tuples(_ALIGN, _ALIGN),
    with_ir=st.tuples(st.booleans(), st.booleans()),
    ident=st.tuples(st.booleans(), st.booleans()),
)
def test_ir_and_adapter_paths_match_the_documented_contract(
    dep, acc, align, with_ir, ident
) -> None:
    o = _var(dep[0], acc[0], align[0], ident=ident[0])
    n = _var(dep[1], acc[1], align[1], ident=ident[1])
    (ov, oe), (nv, ne) = (
        _entity(_snap(o, with_ir=with_ir[0])),
        _entity(_snap(n, with_ir=with_ir[1])),
    )
    got = [
        c.kind
        for c in deprecation_changes(
            "g",
            "g",
            oe,
            ne,
            entity_id=None,
            added=ChangeKind.VAR_DEPRECATED_ADDED,
            removed=ChangeKind.VAR_DEPRECATED_REMOVED,
        )
        + access_changes("g", "g", oe, ne, entity_id=None, is_variable=True)
        + alignment_changes("g", "g", oe, ne, entity_id=None)
    ]
    assert got == _oracle(ov, nv)


def test_a_bare_deprecated_marker_is_a_deprecation() -> None:
    facts = declaration_facts(_var("", AccessLevel.PUBLIC, None))
    assert facts["deprecated"].value == ("",)
    assert (
        declaration_facts(_var(None, AccessLevel.PUBLIC, None))["deprecated"].value
        == ()
    )


@pytest.mark.parametrize(
    "status_fact",
    [Fact.not_collected(), Fact.failed("x"), Fact.unsupported("x")],
)
def test_an_unestablished_deprecation_is_not_compared(status_fact) -> None:
    present = variable_canonical_entity(_var(None, AccessLevel.PUBLIC, None), "castxml")
    var = _var(None, AccessLevel.PUBLIC, None)
    var.deprecated_fact = status_fact
    unknown = variable_canonical_entity(var, "castxml")
    with declined_scope() as declined:
        assert not deprecation_changes(
            "g",
            "g",
            present,
            unknown,
            entity_id=None,
            added=ChangeKind.VAR_DEPRECATED_ADDED,
            removed=ChangeKind.VAR_DEPRECATED_REMOVED,
        )
    assert len(declined) == 1


def test_method_access_reports_only_narrowing() -> None:
    def fn(access):
        return Function(
            name="m",
            mangled="_Z1mv",
            return_type="void",
            access=access,
            entity_id=entity_id_for_function((), "m", mangled_name="_Z1mv"),
        )

    def entity(f):
        from abicheck.compare.function_signature import function_signature_index

        return function_signature_index(None, [f]).entity_for(f)

    pub, priv = entity(fn(AccessLevel.PUBLIC)), entity(fn(AccessLevel.PRIVATE))
    kinds = [
        c.kind
        for c in access_changes("m", "m", pub, priv, entity_id=None, is_variable=False)
    ]
    assert kinds == [ChangeKind.METHOD_ACCESS_CHANGED]
    assert not access_changes("m", "m", priv, pub, entity_id=None, is_variable=False)


def test_end_to_end_compare_and_codec_round_trip() -> None:
    old = _snap(_var(None, AccessLevel.PUBLIC, 32), with_ir=True)
    new = _snap(_var("gone", AccessLevel.PUBLIC, 64), with_ir=True)
    kinds = {c.kind for c in compare(old, new).changes}
    assert {ChangeKind.VAR_DEPRECATED_ADDED, ChangeKind.VAR_ALIGNMENT_CHANGED} <= kinds
    assert snapshot_from_dict(snapshot_to_dict(new)).canonical_ir == new.canonical_ir


class TestCutoverGate:
    def _gate(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import semantic_ir_cutover

        return semantic_ir_cutover

    def test_registered_and_clean(self) -> None:
        gate = self._gate()
        from findings_report import Findings

        assert "declaration_facts" in {c.name for c in gate.MIGRATED_COHORTS}
        findings = Findings()
        gate.check_semantic_ir_cutover(findings)
        assert findings.errors == []

    @pytest.mark.parametrize(
        "source",
        ["x = v.deprecated_fact", "x = v.access_fact", "x = v.alignment_bits"],
    )
    def test_fires_on_a_legacy_read(self, source: str) -> None:
        forbidden = frozenset(
            {"deprecated_fact", "access_fact", "alignment_bits", "alignment_bits_fact"}
        )
        assert self._gate().legacy_collection_reads(ast.parse(source), forbidden)
