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
from _semantic_ir_persisted import persisted_view
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
from abicheck.model.semantic_ir_declaration_facts import DECLARATION_FIELDS
from abicheck.model.semantic_ir_function_signature import SIGNATURE_FIELDS
from abicheck.model.semantic_ir_legacy_adapter import (
    legacy_function_signature_occurrences,
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
    changes = function_signature_changes(
        o.mangled,
        o.name,
        function_signature_index(old.canonical_ir, [o]).entity_for(o),
        function_signature_index(new.canonical_ir, [n]).entity_for(n),
        entity_id=o.entity_id,
    )
    return [c.kind for c in changes]


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


def test_the_declaration_wins_over_a_stale_ir_copy_only_where_it_speaks() -> None:
    """The IR occurrence is a boundary copy of the declaration: where the
    declaration establishes a fact it wins (an in-place edit after load);
    where it does not (``is_explicit`` unset), the IR's own fact stands."""
    fn = _fn("int", ["int"], "", False)
    assert fn.is_explicit is None
    entity = CanonicalEntity(
        canonical_spelling=Fact.not_collected(),
        return_type_spelling=Fact.present("long"),
        parameter_type_spellings=Fact.present(("int",)),
        parameter_kinds=Fact.present(("",)),
        ref_qualifier=Fact.present(""),
        is_variadic=Fact.present(False),
        is_explicit=Fact.present(True),
    )
    ir = SemanticIR(occurrences={OccurrenceId(fn.entity_id): entity})
    got = function_signature_index(ir, [fn]).entity_for(fn)
    assert signature_of(got).return_spelling == "int"
    assert got.is_explicit.value is True


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


class TestNormalizedIR:
    def test_a_normalized_side_compares_by_its_declarations(self) -> None:
        snap = _snap(_fn("int", ["char *"], "&", True), with_ir=True)
        (fn,) = snap.declarations.functions
        entity = function_signature_index(snap.canonical_ir, [fn]).entity_for(fn)
        assert entity.return_type_spelling.value == "int"
        assert entity.parameter_type_spellings.value == ("char *",)
        assert entity.ref_qualifier.value == "&"
        assert entity.is_variadic.value is True

    def test_unknown_variadic_stays_not_collected(self) -> None:
        snap = _snap(_fn("int", [], "", None), with_ir=True)
        (fn,) = snap.declarations.functions
        entity = function_signature_index(snap.canonical_ir, [fn]).entity_for(fn)
        assert entity.is_variadic.status is FactStatus.NOT_COLLECTED


class TestCodec:
    def test_signature_facts_are_never_written_and_compare_is_unchanged(self) -> None:
        fn = _fn("int", ["int"], "&&", False)
        projected, _ = legacy_function_signature_occurrences([fn])
        doc = semantic_ir_to_document(projected, {})
        assert doc["semantic_ir"]["version"] == 2
        (occ,) = doc["semantic_ir"]["occurrences"]
        assert not set(occ["entity"]) & {*SIGNATURE_FIELDS, *DECLARATION_FIELDS}
        ir, _ = semantic_ir_from_document(doc)
        assert ir == persisted_view(projected)
        snap = _snap(fn, with_ir=True)
        reloaded = snapshot_from_dict(snapshot_to_dict(snap))
        assert reloaded.canonical_ir == persisted_view(snap.canonical_ir)
        (live,) = snap.declarations.functions
        (loaded,) = reloaded.declarations.functions
        assert function_signature_index(reloaded.canonical_ir, [loaded]).entity_for(
            loaded
        ) == function_signature_index(snap.canonical_ir, [live]).entity_for(live)

    def test_a_document_carrying_a_projection_fact_is_refused(self) -> None:
        snap = _snap(_fn("int", [], "", False), with_ir=True)
        doc = semantic_ir_to_document(snap.canonical_ir, {})
        doc["semantic_ir"]["occurrences"][0]["entity"]["is_variadic"] = {
            "status": "present",
            "value": False,
            "diagnostics": [],
        }
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


@pytest.mark.parametrize("edit", ["return", "param", "variable"])
def test_editing_a_loaded_declaration_is_not_masked_by_its_ir_copy(edit) -> None:
    """A loaded snapshot whose ``Function``/``Variable`` is edited in place
    must compare by the edit, not by the stale boundary copy in its IR
    (``scripts/demo_libz.py``'s pattern)."""
    import copy

    from abicheck.checker import compare as _compare
    from abicheck.model import Param, Variable
    from abicheck.model.identity import entity_id_for_variable

    fn = Function(
        name="f",
        mangled="_Z1fi",
        return_type="int",
        params=[Param(name="a", type="int")],
        entity_id=entity_id_for_function((), "f", mangled_name="_Z1fi"),
    )
    var = Variable(
        name="g",
        mangled="g",
        type="int",
        entity_id=entity_id_for_variable((), "g", mangled_name="g"),
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
        variables=[var],
    )
    old = AbiSnapshot(
        library="l", version="1", functions=[fn], variables=[var], semantic_ir=ir
    )
    new = snapshot_from_dict(snapshot_to_dict(copy.deepcopy(old)))
    (nf,) = new.declarations.functions
    (nv,) = new.declarations.variables
    if edit == "return":
        nf.return_type = "long"
        expected = ChangeKind.FUNC_RETURN_CHANGED
    elif edit == "param":
        nf.params[0].type = "long"
        expected = ChangeKind.FUNC_PARAMS_CHANGED
    else:
        nv.type = "long"
        expected = ChangeKind.VAR_TYPE_CHANGED
    assert expected in {c.kind for c in _compare(old, new).changes}


_OPT_BOOL = st.sampled_from([None, False, True])


@settings(max_examples=300, deadline=None)
@given(
    ret=st.sampled_from(["int", "void", "?", "const char *", ""]),
    params=st.lists(
        st.tuples(
            st.sampled_from(["", "a"]),
            st.sampled_from(["int", "int *", "?", ""]),
            st.sampled_from([None, "", "0"]),
            _OPT_BOOL,
            _OPT_BOOL,
        ),
        max_size=3,
    ),
    flags=st.tuples(_OPT_BOOL, _OPT_BOOL, _OPT_BOOL, _OPT_BOOL),
    vtable=st.sampled_from([None, 0, 3]),
    owner=st.sampled_from([None, "", "Point"]),
    attrs=st.sampled_from([None, [], ["pre"]]),
)
def test_the_trusted_projection_always_passes_validation(
    ret, params, flags, vtable, owner, attrs
) -> None:
    """The adapter builds its entity with ``_trusted=True`` (no per-function
    validation). The oracle is ``CanonicalEntity``'s own validation, run on
    the same facts: every projection must pass it."""
    from abicheck.model.semantic_ir_legacy_adapter import (
        legacy_function_signature_entity,
    )

    explicit, override, variadic, friend = flags
    fn = Function(
        name="f",
        mangled="_Z1fv",
        return_type=ret,
        params=[
            Param(
                name=n,
                type=t,
                default=d,
                pointer_depth=t.count("*"),
                is_restrict_fact=Fact.not_collected() if r is None else Fact.present(r),
                is_va_list_fact=Fact.not_collected() if v is None else Fact.present(v),
            )
            for n, t, d, r, v in params
        ],
        is_explicit=explicit,
        is_override=override,
        is_variadic=variadic,
        is_hidden_friend=friend,
        hidden_friend_owner=owner,
        vtable_index=vtable,
        contract_attributes=attrs,
    )
    trusted = legacy_function_signature_entity(fn)
    validated = CanonicalEntity(**dict(trusted.fact_items()), producer=trusted.producer)
    assert validated == trusted


def test_signature_index_shares_one_ir_facade_per_scope(monkeypatch) -> None:
    """Every index over one IR in a compare() scope reuses one
    ``SemanticIRIndex`` (whose construction ranks the whole IR); distinct IRs
    and calls outside a scope still get their own."""
    from abicheck.compare import function_signature as fs
    from abicheck.compare.detection_memo import detection_memo_scope
    from abicheck.model.semantic_ir import SemanticIR

    built: list[object] = []
    real = fs.SemanticIRIndex

    def counting(ir):  # type: ignore[no-untyped-def]
        built.append(ir)
        return real(ir)

    monkeypatch.setattr(fs, "SemanticIRIndex", counting)
    irs = [SemanticIR(), SemanticIR(), None]
    with detection_memo_scope():
        firsts = [fs.function_signature_index(ir, []).index for ir in irs]
        for _ in range(5):
            again = [fs.function_signature_index(ir, []).index for ir in irs]
            assert all(a is b for a, b in zip(again, firsts, strict=True))
    # None and uncovered IRs both fall back to the shared empty IR.
    assert len(built) == len({id(i) for i in built})
    built.clear()
    fs.function_signature_index(None, [])
    fs.function_signature_index(None, [])
    assert len(built) == 2
