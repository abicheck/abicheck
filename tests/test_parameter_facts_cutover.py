# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-063 6B, parameter cohort: defaults, renames, pointer levels,
``restrict``, ``va_list`` and ``override`` read through ``SemanticIR``.

The oracle is the documented contract written out by hand per parameter
position, not a call into the detector.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from _semantic_ir_persisted import persisted_view
from hypothesis import given, settings, strategies as st

from abicheck.checker import ChangeKind
from abicheck.compare.function_signature import function_signature_index
from abicheck.compare.parameter_facts import (
    override_changes,
    parameter_default_changes,
    parameter_rename_changes,
    parameter_view,
    pointer_level_changes,
    restrict_changes,
    va_list_changes,
)
from abicheck.extract.semantic_normalizer import normalize_header_ast
from abicheck.model import AbiSnapshot, Fact, Function, Param
from abicheck.model.identity import entity_id_for_function
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

_PARAM = st.tuples(
    st.sampled_from(["a", "b", ""]),  # name
    st.sampled_from(["int", "int *", "int **", "?"]),  # type
    st.sampled_from([None, "0", "1"]),  # default
    st.sampled_from([None, False, True]),  # restrict (None = not established)
    st.sampled_from([None, False, True]),  # va_list
)


def _depth(t: str) -> int:
    return t.count("*")


def _fn(params, override, *, ident=True) -> Function:
    return Function(
        name="f",
        mangled="_Z1fv",
        return_type="int",
        is_override=override,
        params=[
            Param(
                name=n,
                type=t,
                default=d,
                pointer_depth=_depth(t),
                is_restrict_fact=Fact.not_collected() if r is None else Fact.present(r),
                is_va_list_fact=Fact.not_collected() if v is None else Fact.present(v),
            )
            for n, t, d, r, v in params
        ],
        entity_id=entity_id_for_function((), "f", mangled_name="_Z1fv")
        if ident
        else None,
    )


def _view(fn: Function, with_ir: bool):
    ir = (
        normalize_header_ast(
            types=[],
            enums=[],
            typedefs_qualified={},
            typedef_entity_ids={},
            producer="castxml",
            functions=[fn],
        )
        if with_ir
        else None
    )
    snap = AbiSnapshot(library="l", version="1", functions=[fn], semantic_ir=ir)
    (f,) = snap.declarations.functions
    return parameter_view(
        function_signature_index(snap.canonical_ir, [f]).entity_for(f)
    )


def _oracle(o, n, o_ovr, n_ovr) -> list[ChangeKind]:
    out: list[ChangeKind] = []
    pairs = list(zip(o, n))
    for (_, _, d1, _, _), (_, _, d2, _, _) in pairs:
        if d1 is not None and d2 is None:
            out.append(ChangeKind.PARAM_DEFAULT_VALUE_REMOVED)
        elif d1 is not None and d2 is not None and d1 != d2:
            out.append(ChangeKind.PARAM_DEFAULT_VALUE_CHANGED)
    for (n1, t1, _, _, _), (n2, t2, _, _, _) in pairs:
        if t1 == t2 and n1 and n2 and n1 != n2:
            out.append(ChangeKind.PARAM_RENAMED)
    for (_, t1, _, _, _), (_, t2, _, _, _) in pairs:
        if "?" in (t1, t2):
            continue
        if _depth(t1) != _depth(t2):
            out.append(ChangeKind.PARAM_POINTER_LEVEL_CHANGED)
    for (_, _, _, r1, _), (_, _, _, r2, _) in pairs:
        if r1 is not None and r2 is not None and r1 != r2:
            out.append(
                ChangeKind.PARAM_RESTRICT_ADDED
                if r2
                else ChangeKind.PARAM_RESTRICT_CHANGED
            )
    for (_, _, _, _, v1), (_, _, _, _, v2) in pairs:
        if v1 is not None and v2 is not None and v1 != v2:
            out.append(
                ChangeKind.PARAM_BECAME_VA_LIST if v2 else ChangeKind.PARAM_LOST_VA_LIST
            )
    if o_ovr is not None and n_ovr is not None and o_ovr != n_ovr:
        out.append(
            ChangeKind.FUNC_OVERRIDE_SPECIFIER_ADDED
            if n_ovr
            else ChangeKind.FUNC_OVERRIDE_SPECIFIER_REMOVED
        )
    return out


@settings(max_examples=300, deadline=None)
@given(
    params=st.tuples(st.lists(_PARAM, max_size=3), st.lists(_PARAM, max_size=3)),
    override=st.tuples(
        st.sampled_from([None, False, True]), st.sampled_from([None, False, True])
    ),
    with_ir=st.tuples(st.booleans(), st.booleans()),
    ident=st.tuples(st.booleans(), st.booleans()),
)
def test_ir_and_adapter_paths_match_the_documented_contract(
    params, override, with_ir, ident
) -> None:
    o = _view(_fn(params[0], override[0], ident=ident[0]), with_ir[0])
    n = _view(_fn(params[1], override[1], ident=ident[1]), with_ir[1])
    kw = {"entity_id": None}
    got = (
        parameter_default_changes(
            "f", "f", o, n, value_comparison_unreliable=lambda a, b: False, **kw
        )
        + parameter_rename_changes("f", "f", o, n, **kw)
        + pointer_level_changes("f", "f", o, n, params_unconfirmed=False, **kw)
        + restrict_changes("f", "f", o, n, **kw)
        + va_list_changes("f", "f", o, n, **kw)
        + override_changes("f", "f", o, n, **kw)
    )
    assert [c.kind for c in got] == _oracle(*params, *override)


def test_round_trip_keeps_absent_defaults_and_depths() -> None:
    fn = _fn([("a", "int *", None, True, None), ("", "int", "3", None, False)], True)
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    snap = AbiSnapshot(library="l", version="1", functions=[fn], semantic_ir=ir)
    (f,) = snap.declarations.functions
    entity = function_signature_index(snap.canonical_ir, [f]).entity_for(f)
    assert entity.parameter_defaults.value == (None, "3")
    assert entity.parameter_pointer_depths.value == (1, 0)
    assert entity.parameter_restrict.value == ("true", "")
    reloaded = snapshot_from_dict(snapshot_to_dict(snap))
    assert reloaded.canonical_ir == persisted_view(snap.canonical_ir)
    (rf,) = reloaded.declarations.functions
    assert (
        function_signature_index(reloaded.canonical_ir, [rf]).entity_for(rf) == entity
    )


def test_unreliable_default_value_is_not_reported_as_changed() -> None:
    o = _view(_fn([("a", "int", "0", None, None)], None), True)
    n = _view(_fn([("a", "int", "1", None, None)], None), True)
    assert not parameter_default_changes(
        "f", "f", o, n, entity_id=None, value_comparison_unreliable=lambda a, b: True
    )


class TestCutoverGate:
    def _gate(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import semantic_ir_cutover

        return semantic_ir_cutover

    def test_registered_and_clean(self) -> None:
        gate = self._gate()
        from findings_report import Findings

        assert "parameter_facts" in {c.name for c in gate.MIGRATED_COHORTS}
        findings = Findings()
        gate.check_semantic_ir_cutover(findings)
        assert findings.errors == []

    @pytest.mark.parametrize(
        "source",
        [
            "x = f.params",
            "x = p.default",
            "x = p.pointer_depth",
            "x = p.is_va_list_fact",
        ],
    )
    def test_fires_on_a_legacy_parameter_read(self, source: str) -> None:
        forbidden = frozenset(
            {
                "params",
                "default",
                "pointer_depth",
                "is_va_list_fact",
                "is_restrict_fact",
            }
        )
        assert self._gate().legacy_collection_reads(ast.parse(source), forbidden)
