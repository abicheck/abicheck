# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-063 6B, function-signature cohort: ``FUNC_RETURN_CHANGED``/
``FUNC_PARAMS_CHANGED``/``FUNC_REF_QUAL_CHANGED``/``FUNC_VARIADIC_*`` read
through ``SemanticIR``.

The equivalence property's oracle is a hand-written table of which spellings
are the same ABI type (cv-only pointee changes are not), not a call into the
canonicalizer or the detector, so a shared bug cannot make both sides agree.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.checker import ChangeKind, compare
from abicheck.compare.function_signature import (
    function_signature_changes,
    function_signature_index,
    signature_of,
)
from abicheck.extract.semantic_normalizer import normalize_header_ast
from abicheck.model import AbiSnapshot, Fact, Function, Param
from abicheck.model.availability import FactStatus
from abicheck.model.identity import EntityKind, entity_id_for_function
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import CanonicalEntity, SemanticIR
from abicheck.model.semantic_ir_function_signature import (
    LEGACY_SIGNATURE_DIAGNOSTIC,
    with_function_signatures,
)
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.storage.semantic_ir_codec import (
    semantic_ir_from_document,
    semantic_ir_to_document,
)

#: spelling -> ABI class (same class == same ABI type); ``None`` = unknown.
_CLASS: dict[str, str | None] = {
    "int": "int",
    "long": "long",
    "double": "double",
    "char *": "charptr",
    "const char *": "charptr",
    "?": None,
}
_TYPES = st.sampled_from(sorted(_CLASS))
_PARAMS = st.lists(_TYPES, max_size=3)
_REFQ = st.sampled_from(["", "&", "&&"])
_VARIADIC = st.sampled_from([None, False, True])


def _fn(ret, params, refq, variadic, *, ident=True) -> Function:
    mangled = "_ZN2ns1fEv"
    return Function(
        name="ns::f",
        mangled=mangled,
        return_type=ret,
        params=[Param(name=f"p{i}", type=t) for i, t in enumerate(params)],
        ref_qualifier=refq,
        is_variadic=variadic,
        entity_id=entity_id_for_function((), "f", mangled_name=mangled)
        if ident
        else None,
    )


def _snap(fn: Function, *, with_ir: bool) -> AbiSnapshot:
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
    return AbiSnapshot(library="l", version="1", functions=[fn], semantic_ir=ir)


def _oracle(o: Function, n: Function) -> list[ChangeKind]:
    out = []
    a, b = _CLASS[o.return_type], _CLASS[n.return_type]
    if a is not None and b is not None and a != b:
        out.append(ChangeKind.FUNC_RETURN_CHANGED)
    op, np_ = [p.type for p in o.params], [p.type for p in n.params]
    if len(op) != len(np_) or any(
        _CLASS[x] is not None and _CLASS[y] is not None and _CLASS[x] != _CLASS[y]
        for x, y in zip(op, np_)
    ):
        out.append(ChangeKind.FUNC_PARAMS_CHANGED)
    if o.ref_qualifier != n.ref_qualifier:
        out.append(ChangeKind.FUNC_REF_QUAL_CHANGED)
    if o.is_variadic is not None and n.is_variadic is not None:
        if not o.is_variadic and n.is_variadic:
            out.append(ChangeKind.FUNC_VARIADIC_ADDED)
        elif o.is_variadic and not n.is_variadic:
            out.append(ChangeKind.FUNC_VARIADIC_REMOVED)
    return out


def _run(old: AbiSnapshot, new: AbiSnapshot) -> list[ChangeKind]:
    (o,), (n,) = old.declarations.functions, new.declarations.functions
    head, refq, variadic = function_signature_changes(
        o.mangled,
        o.name,
        function_signature_index(old.canonical_ir, [o]).entity_for(o),
        function_signature_index(new.canonical_ir, [n]).entity_for(n),
        entity_id=o.entity_id,
    )
    return [c.kind for c in head + refq + variadic]


@settings(max_examples=400, deadline=None)
@given(
    ret=st.tuples(_TYPES, _TYPES),
    params=st.tuples(_PARAMS, _PARAMS),
    refq=st.tuples(_REFQ, _REFQ),
    variadic=st.tuples(_VARIADIC, _VARIADIC),
    with_ir=st.tuples(st.booleans(), st.booleans()),
    ident=st.tuples(st.booleans(), st.booleans()),
)
def test_ir_and_adapter_paths_match_the_documented_contract(
    ret, params, refq, variadic, with_ir, ident
) -> None:
    o = _fn(ret[0], params[0], refq[0], variadic[0], ident=ident[0])
    n = _fn(ret[1], params[1], refq[1], variadic[1], ident=ident[1])
    old, new = _snap(o, with_ir=with_ir[0]), _snap(n, with_ir=with_ir[1])
    assert _run(old, new) == _oracle(o, n)


def test_oracle_is_not_vacuous() -> None:
    kinds = set()
    for a in _CLASS:
        for b in _CLASS:
            kinds |= set(_oracle(_fn(a, [a], "", False), _fn(b, [], "&", True)))
    assert kinds == {
        ChangeKind.FUNC_RETURN_CHANGED,
        ChangeKind.FUNC_PARAMS_CHANGED,
        ChangeKind.FUNC_REF_QUAL_CHANGED,
        ChangeKind.FUNC_VARIADIC_ADDED,
    }


def test_the_ir_is_the_authority_not_the_function() -> None:
    fn = _fn("int", ["int"], "", False)
    entity = CanonicalEntity(
        canonical_spelling=Fact.not_collected(),
        return_type_spelling=Fact.present("long"),
        parameter_type_spellings=Fact.present(("int",)),
        parameter_kinds=Fact.present(("",)),
        ref_qualifier=Fact.present(""),
        is_variadic=Fact.present(False),
    )
    ir = SemanticIR(occurrences={OccurrenceId(fn.entity_id): entity})
    index = function_signature_index(ir, [fn])
    assert signature_of(index.entity_for(fn)).return_spelling == "long"


def test_end_to_end_compare_reads_the_ir() -> None:
    old = _snap(_fn("int", ["int"], "", False), with_ir=True)
    new = _snap(_fn("long", ["int", "int"], "&", True), with_ir=True)
    kinds = {c.kind for c in compare(old, new).changes}
    assert {
        ChangeKind.FUNC_RETURN_CHANGED,
        ChangeKind.FUNC_PARAMS_CHANGED,
        ChangeKind.FUNC_REF_QUAL_CHANGED,
        ChangeKind.FUNC_VARIADIC_ADDED,
    } <= kinds


class TestFill:
    def test_construction_fills_a_normalized_ir(self) -> None:
        snap = _snap(_fn("int", ["char *"], "&", True), with_ir=True)
        (entity,) = snap.canonical_ir.occurrences.values()
        assert entity.return_type_spelling.value == "int"
        assert entity.parameter_type_spellings.value == ("char *",)
        assert entity.ref_qualifier.value == "&"
        assert entity.is_variadic.value is True

    def test_unknown_variadic_stays_not_collected(self) -> None:
        snap = _snap(_fn("int", [], "", None), with_ir=True)
        (entity,) = snap.canonical_ir.occurrences.values()
        assert entity.is_variadic.status is FactStatus.NOT_COLLECTED

    def test_fill_never_overrides_an_established_fact(self) -> None:
        fn = _fn("int", [], "", False)
        entity = CanonicalEntity(
            canonical_spelling=Fact.not_collected(),
            return_type_spelling=Fact.present("long"),
        )
        ir = SemanticIR(occurrences={OccurrenceId(fn.entity_id): entity})
        (filled,) = with_function_signatures(ir, [fn]).occurrences.values()
        assert filled.return_type_spelling.value == "long"
        assert filled.ref_qualifier.value == ""

    def test_conflicting_functions_under_one_identity_fill_nothing(self) -> None:
        a, b = _fn("int", [], "", False), _fn("long", [], "", False)
        ir = SemanticIR(
            occurrences={
                OccurrenceId(a.entity_id): CanonicalEntity(
                    canonical_spelling=Fact.not_collected()
                )
            }
        )
        assert with_function_signatures(ir, [a, b]) is ir


class TestCodec:
    def test_round_trip_writes_signature_only_for_functions(self) -> None:
        snap = _snap(_fn("int", ["int"], "&&", False), with_ir=True)
        doc = semantic_ir_to_document(snap.canonical_ir, {})
        assert doc["semantic_ir"]["version"] == 3
        (occ,) = doc["semantic_ir"]["occurrences"]
        assert occ["entity"]["parameter_type_spellings"]["value"] == ["int"]
        ir, _ = semantic_ir_from_document(doc)
        assert ir == snap.canonical_ir
        reloaded = snapshot_from_dict(snapshot_to_dict(snap))
        assert reloaded.canonical_ir == snap.canonical_ir

    def test_a_version_2_document_loads_and_is_filled(self) -> None:
        snap = _snap(_fn("int", ["int"], "", True), with_ir=True)
        doc = semantic_ir_to_document(snap.canonical_ir, {})
        doc["semantic_ir"]["version"] = 2
        for occ in doc["semantic_ir"]["occurrences"]:
            for name in (
                "return_type_spelling",
                "parameter_type_spellings",
                "parameter_kinds",
                "ref_qualifier",
                "is_variadic",
            ):
                del occ["entity"][name]
        ir, _ = semantic_ir_from_document(doc)
        (entity,) = ir.occurrences.values()
        assert entity.return_type_spelling.diagnostics == (LEGACY_SIGNATURE_DIAGNOSTIC,)
        filled = with_function_signatures(ir, snap.declarations.functions)
        (entity,) = filled.occurrences.values()
        assert entity.return_type_spelling.value == "int"
        assert entity.is_variadic.value is True

    def test_a_version_2_document_carrying_signature_facts_is_refused(self) -> None:
        snap = _snap(_fn("int", [], "", False), with_ir=True)
        doc = semantic_ir_to_document(snap.canonical_ir, {})
        doc["semantic_ir"]["version"] = 2
        with pytest.raises(ValueError):
            semantic_ir_from_document(doc)

    def test_a_non_boolean_variadic_is_refused(self) -> None:
        snap = _snap(_fn("int", [], "", False), with_ir=True)
        doc = semantic_ir_to_document(snap.canonical_ir, {})
        doc["semantic_ir"]["occurrences"][0]["entity"]["is_variadic"]["value"] = 1
        with pytest.raises(ValueError):
            semantic_ir_from_document(doc)

    def test_non_function_occurrences_carry_no_signature(self) -> None:
        from abicheck.model import RecordType
        from abicheck.model.identity import entity_id_for_type

        rec = RecordType(
            name="R", kind="struct", size_bits=8, entity_id=entity_id_for_type((), "R")
        )
        ir = normalize_header_ast(
            types=[rec],
            enums=[],
            typedefs_qualified={},
            typedef_entity_ids={},
            producer="castxml",
        )
        doc = semantic_ir_to_document(ir, {})
        (occ,) = doc["semantic_ir"]["occurrences"]
        assert "return_type_spelling" not in occ["entity"]
        assert next(iter(ir.occurrences)).entity_id.kind is EntityKind.TYPE


class TestCutoverGate:
    def _gate(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import semantic_ir_cutover

        return semantic_ir_cutover

    def test_registered_and_clean(self) -> None:
        gate = self._gate()
        from findings_report import Findings

        assert "function_signature" in {c.name for c in gate.MIGRATED_COHORTS}
        findings = Findings()
        gate.check_semantic_ir_cutover(findings)
        assert findings.errors == []

    @pytest.mark.parametrize(
        "source",
        [
            "x = f_old.return_type",
            "x = fn.params",
            "x = snap.function_map",
            "x = snap.declarations.functions",
            'x = getattr(f, "params")',
        ],
    )
    def test_fires_on_a_legacy_signature_read(self, source: str) -> None:
        forbidden = frozenset({"return_type", "params", "functions", "function_map"})
        assert self._gate().legacy_collection_reads(ast.parse(source), forbidden)
