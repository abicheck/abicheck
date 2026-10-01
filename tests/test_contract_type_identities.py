# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Schema v54's per-slot resolved type identities, end to end.

Bug class: a type slot's spelling is the bare source text (``Cache *``), so
when two records share a leaf (``ns1::Cache``/``ns2::Cache``) nothing
downstream could say which one a public signature reaches, and the
``public`` contract domain left a real break on the reached record
``UNKNOWN_UNRESOLVED`` -- withheld from the gate (exit ``1`` instead of
``4``). The fix records the compiler's own answer at extraction
(``extract/headers/castxml/type_resolution.type_identities``) and lets the
exact-reachability walk and the evaluator use it.

The invariants, each checked against an oracle independent of the code
under test:

* **extraction**: for every wrapper chain over every target shape, the
  identity is the target's qualified name -- and equals the
  ``qualified_name`` the record/enum parser itself stamps on that entity;
* **soundness**: a change on a same-leaf record the signature does *not*
  reach is never confirmed, however many siblings exist and in whatever
  order they are declared;
* **completeness**: a change on the reached record is confirmed, through
  every slot kind (return, parameter, variable, field chain);
* **no evidence, no change**: with no ``PRESENT`` identity fact the answer
  is exactly the pre-v54 one;
* **no laundering**: a compiler-generated member's identity never seeds;
* **storage**: captured identities round-trip; uncaptured ones are not
  written and read back as ``NOT_COLLECTED`` (``None`` for a pre-v54
  document); malformed ones read as ``NOT_COLLECTED``.
"""

from __future__ import annotations

import itertools
import json
import random
from xml.etree.ElementTree import Element, SubElement

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.checker import compare
from abicheck.contract_relevance_types import ContractMode, ContractRelevance
from abicheck.dumper import _CastxmlParser
from abicheck.extract.headers.castxml.context import CastxmlParserContext
from abicheck.extract.headers.castxml.type_resolution import type_identities
from abicheck.model import (
    AbiSnapshot,
    Fact,
    FactStatus,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.policy.captured_type_identities import present_identities
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

# ── extraction: an exhaustive small domain of castxml type graphs ──────────


def _context(root: Element) -> CastxmlParserContext:
    ctx = CastxmlParserContext(root, set(), set())
    ctx.build_id_map()
    return ctx


_WRAPPERS = (
    "PointerType",
    "ReferenceType",
    "RValueReferenceType",
    "CvQualifiedType",
    "ElaboratedType",
    "ArrayType",
    "Typedef",
)

#: (label, castxml tag, namespace or None, leaf name or "" for anonymous)
_TARGETS = (
    ("ns_struct", "Struct", "ns1", "Cache"),
    ("other_ns_class", "Class", "ns2", "Cache"),
    ("global_union", "Union", None, "Blob"),
    ("ns_enum", "Enumeration", "ns1", "Mode"),
    ("anonymous_struct", "Struct", "ns1", ""),
    ("fundamental", "FundamentalType", None, "int"),
)


def _graph(target: tuple[str, str, str | None, str], chain: tuple[str, ...]):
    """A castxml document whose slot id ``slot`` leads through *chain* to
    *target*; returns (root, slot id)."""
    _label, tag, ns, leaf = target
    root = Element("CastXML", attrib={"format": "1.4.0"})
    SubElement(root, "File", attrib={"id": "f1", "name": "lib.h"})
    SubElement(root, "Namespace", attrib={"id": "_g", "name": "::"})
    ctx_id = "_g"
    if ns:
        SubElement(root, "Namespace", attrib={"id": "_ns", "name": ns, "context": "_g"})
        ctx_id = "_ns"
    attrs = {"id": "_t", "name": leaf, "context": ctx_id, "file": "f1", "line": "1"}
    if tag in ("Struct", "Class", "Union"):
        attrs.update(size="32", align="32")
    SubElement(root, tag, attrib=attrs)
    inner = "_t"
    for i, wrapper in enumerate(reversed(chain)):
        wid = f"_w{i}"
        wattrs = {"id": wid, "type": inner}
        if wrapper == "Typedef":
            wattrs.update(name=f"Alias{i}", context=ctx_id, file="f1", line="2")
        if wrapper == "CvQualifiedType":
            wattrs["const"] = "1"
        if wrapper == "ArrayType":
            wattrs.update(min="0", max="3")
        SubElement(root, wrapper, attrib=wattrs)
        inner = wid
    return root, inner


def _expected(target: tuple[str, str, str | None, str], chain: tuple[str, ...]):
    """The oracle: written from the target spec, not from castxml's graph."""
    _label, tag, ns, leaf = target
    if tag == "FundamentalType":
        return ()
    if not leaf:
        if tag == "Enumeration":
            return ()
        typedef_positions = [i for i, w in enumerate(chain) if w == "Typedef"]
        if not typedef_positions:
            return ()
        # The alias nearest the record names it. `_graph` numbers wrappers
        # from the target outwards, so the chain's last typedef is the
        # nearest, and its index from the inside out is its suffix.
        nearest = typedef_positions[-1]
        leaf = f"Alias{len(chain) - 1 - nearest}"
    return (f"{ns}::{leaf}",) if ns else (leaf,)


_CHAINS = tuple(
    chain for n in range(4) for chain in itertools.product(_WRAPPERS, repeat=n)
)


class TestTypeIdentityExtraction:
    @pytest.mark.parametrize("target", _TARGETS, ids=[t[0] for t in _TARGETS])
    def test_every_wrapper_chain_resolves_to_the_target_identity(self, target) -> None:
        # 400 chains per target, all failures reported at once.
        wrong = []
        for chain in _CHAINS:
            root, slot = _graph(target, chain)
            ctx = _context(root)
            got = type_identities(ctx, slot)
            if got != _expected(target, chain):
                wrong.append((chain, got, _expected(target, chain)))
        assert not wrong, wrong[:10]

    @pytest.mark.parametrize(
        "target",
        [t for t in _TARGETS if t[1] in ("Struct", "Class", "Union") and t[3]],
        ids=lambda t: t[0],
    )
    def test_the_identity_is_the_qualified_name_the_record_parser_stamps(
        self, target
    ) -> None:
        # "Matches the model entry by construction" is the claim the whole
        # consumer side rests on, so check it against the parser's own output
        # rather than against the formula in `_expected`.
        root, slot = _graph(target, ("PointerType",))
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        (rec,) = parser.parse_types()
        ctx = _context(root)
        assert type_identities(ctx, slot) == ((rec.qualified_name or rec.name),)

    def test_an_unresolved_or_empty_id_is_a_captured_empty_answer(self) -> None:
        root, _ = _graph(_TARGETS[0], ())
        ctx = _context(root)
        assert type_identities(ctx, "") == ()
        assert type_identities(ctx, "_missing") == ()

    def test_the_parser_wires_every_slot_kind(self) -> None:
        root, _ = _graph(_TARGETS[0], ())
        SubElement(root, "PointerType", attrib={"id": "_p", "type": "_t"})
        fn = SubElement(
            root,
            "Function",
            attrib={
                "id": "_f",
                "name": "api",
                "returns": "_p",
                "context": "_g",
                "file": "f1",
                "line": "3",
                "mangled": "_Z3apiPN3ns15CacheE",
            },
        )
        SubElement(fn, "Argument", attrib={"name": "c", "type": "_p"})
        SubElement(
            root,
            "Field",
            attrib={"id": "_fld", "name": "next", "type": "_p", "context": "_t"},
        )
        root.find("Struct").set("members", "_fld")  # type: ignore[union-attr]
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        (func,) = parser.parse_functions()
        captured = Fact.present(("ns1::Cache",))
        assert func.return_type == "Cache*"  # the spelling stays bare...
        assert func.return_type_identities_fact == captured  # ...the identity does not
        assert func.params[0].type_identities_fact == captured
        (rec,) = parser.parse_types()
        assert [f.type_identities_fact for f in rec.fields] == [captured]


# ── the consumer: soundness and completeness over generated namespaces ─────


def _cache(ns: str, size: int) -> RecordType:
    # No source_header: not a public seed on its own, so only reachability
    # from a signature can put it in the contract.
    return RecordType(
        name="Cache",
        qualified_name=f"{ns}::Cache",
        kind="struct",
        size_bits=size,
        fields=[TypeField(name="slot", type="int")],
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


def _api(slot: str, identities: tuple[str, ...] | None, *, generated: bool = False):
    """Public declarations reaching *identities* through *slot*."""
    reached = None if identities is None else Fact.present(identities)
    fn = Function(
        name="api",
        mangled="_Z3apiv",
        return_type="void",
        visibility=Visibility.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
        is_compiler_generated=True if generated else None,
    )
    variables: list[Variable] = []
    holder: list[RecordType] = []
    if slot == "return":
        fn.return_type = "Cache *"
        fn.return_type_identities_fact = reached
    elif slot == "param":
        fn.params = [Param(name="c", type="Cache *", type_identities_fact=reached)]
    elif slot == "variable":
        variables.append(
            Variable(
                name="g_cache",
                mangled="g_cache",
                type="Cache *",
                visibility=Visibility.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
                type_identities_fact=reached,
            )
        )
    elif slot == "field":
        # Two hops: a unique holder whose own field is the ambiguous one.
        fn.return_type = "Holder *"
        fn.return_type_identities_fact = Fact.present(("Holder",))
        holder.append(
            RecordType(
                name="Holder",
                kind="struct",
                size_bits=64,
                fields=[
                    TypeField(name="c", type="Cache *", type_identities_fact=reached)
                ],
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        )
    return fn, variables, holder


def _relevance_of_size_change(
    namespaces: list[str],
    reached: int | None,
    changed: int,
    slot: str,
    *,
    generated: bool = False,
    seed: int = 0,
) -> ContractRelevance | None:
    identities = None if reached is None else (f"{namespaces[reached]}::Cache",)

    def side(version: str, grown: bool) -> AbiSnapshot:
        fn, variables, holder = _api(slot, identities, generated=generated)
        types = [
            _cache(ns, 128 if (grown and i == changed) else 64)
            for i, ns in enumerate(namespaces)
        ]
        random.Random(seed).shuffle(types)
        return AbiSnapshot(
            library="libid",
            version=version,
            functions=[fn],
            variables=variables,
            types=types + holder,
        )

    result = compare(
        side("1", False),
        side("2", True),
        scope_to_public_surface=True,
        contract_evaluation=True,
        contract_mode=ContractMode.PUBLIC.value,
        cross_source_checks=False,
    )
    (change,) = [c for c in result.changes if c.kind.value == "type_size_changed"]
    assert change.qualified_name == f"{namespaces[changed]}::Cache"
    return change.contract_relevance


_SLOTS = ("return", "param", "variable", "field")


@st.composite
def _scenario(draw):
    n = draw(st.integers(min_value=2, max_value=4))
    namespaces = draw(
        st.lists(
            st.sampled_from(["ns1", "ns2", "alpha", "beta", "v2", "impl_x"]),
            min_size=n,
            max_size=n,
            unique=True,
        )
    )
    reached = draw(st.integers(min_value=0, max_value=n - 1))
    changed = draw(st.integers(min_value=0, max_value=n - 1))
    slot = draw(st.sampled_from(_SLOTS))
    seed = draw(st.integers(min_value=0, max_value=2**16))
    return namespaces, reached, changed, slot, seed


class TestIdentityDrivenConfirmation:
    @settings(max_examples=60, deadline=None)
    @given(_scenario())
    def test_confirmed_exactly_when_the_changed_record_is_the_reached_one(
        self, scenario
    ) -> None:
        namespaces, reached, changed, slot, seed = scenario
        relevance = _relevance_of_size_change(
            namespaces, reached, changed, slot, seed=seed
        )
        if changed == reached:
            assert relevance is ContractRelevance.IN_CONTRACT
        else:
            assert relevance is not ContractRelevance.IN_CONTRACT

    @pytest.mark.parametrize("slot", _SLOTS)
    def test_without_identity_evidence_the_reached_break_stays_unresolved(
        self, slot: str
    ) -> None:
        # The pre-v54 answer, unchanged: a spelling-only snapshot (clang
        # JSON, DWARF, an old baseline) cannot say which Cache is meant.
        assert (
            _relevance_of_size_change(["ns1", "ns2"], None, 0, slot)
            is not ContractRelevance.IN_CONTRACT
        )

    @pytest.mark.parametrize("slot", ("return", "param"))
    def test_a_compiler_generated_member_never_seeds(self, slot: str) -> None:
        # A castxml-synthesized implicit member reaches `in_public_surface`
        # only through the unknown-export fallback; its identity is not
        # confirmation (found on a real dump, see `policy.captured_type_identities.captured_identity_seeds`).
        assert (
            _relevance_of_size_change(["ns1", "ns2"], 0, 0, slot, generated=True)
            is not ContractRelevance.IN_CONTRACT
        )

    def test_declaration_order_does_not_change_the_answer(self) -> None:
        answers = {
            _relevance_of_size_change(["ns1", "ns2", "ns3"], 1, 1, "return", seed=s)
            for s in range(12)
        }
        assert answers == {ContractRelevance.IN_CONTRACT}


# ── storage ────────────────────────────────────────────────────────────────


def _identity_snapshot(identities: tuple[str, ...] | None) -> AbiSnapshot:
    fact = None if identities is None else Fact.present(identities)
    return AbiSnapshot(
        library="libid",
        version="1",
        functions=[
            Function(
                name="api",
                mangled="_Z3apiv",
                return_type="Cache *",
                return_type_identities_fact=fact,
                params=[Param(name="c", type="Cache *", type_identities_fact=fact)],
            )
        ],
        variables=[
            Variable(name="g", mangled="g", type="Cache *", type_identities_fact=fact)
        ],
        types=[
            RecordType(
                name="Holder",
                kind="struct",
                fields=[TypeField(name="c", type="Cache *", type_identities_fact=fact)],
            )
        ],
    )


def _slots(snap: AbiSnapshot) -> list:
    fn = snap.declarations.functions[0]
    return [
        fn.return_type_identities_fact,
        fn.params[0].type_identities_fact,
        snap.declarations.variables[0].type_identities_fact,
        snap.declarations.types[0].fields[0].type_identities_fact,
    ]


def _slot_dicts(doc: dict) -> list[tuple[dict, str]]:
    fn = doc["functions"][0]
    return [
        (fn, "return_type_identities_fact"),
        (fn["params"][0], "type_identities_fact"),
        (doc["variables"][0], "type_identities_fact"),
        (doc["types"][0]["fields"][0], "type_identities_fact"),
    ]


def _round_trip(snap: AbiSnapshot) -> dict:
    return json.loads(json.dumps(snapshot_to_dict(snap)))


class TestTypeIdentityStorage:
    @pytest.mark.parametrize("identities", [("ns1::Cache",), ("a::X", "b::Y"), ()])
    def test_captured_identities_round_trip(self, identities) -> None:
        loaded = snapshot_from_dict(_round_trip(_identity_snapshot(identities)))
        assert _slots(loaded) == [Fact.present(identities)] * 4

    def test_uncaptured_identities_are_not_written_and_read_as_not_collected(
        self,
    ) -> None:
        # Omitted keeps every non-castxml snapshot byte-identical to v53; the
        # v54 decoder then reads the absence as "not collected", never as a
        # captured empty answer.
        doc = _round_trip(_identity_snapshot(None))
        assert "type_identities" not in json.dumps(doc)
        assert [f.status for f in _slots(snapshot_from_dict(doc))] == [
            FactStatus.NOT_COLLECTED
        ] * 4

    def test_a_pre_v54_document_reads_as_absent(self) -> None:
        doc = _round_trip(_identity_snapshot(("ns1::Cache",)))
        doc["schema_version"] = 53
        for owner, key in _slot_dicts(doc):
            del owner[key]
        assert _slots(snapshot_from_dict(doc)) == [None] * 4

    @pytest.mark.parametrize("bad", ["ns1::Cache", [1], [""], {"a": 1}, [None]])
    def test_a_malformed_value_loads_as_not_collected(self, bad) -> None:
        doc = _round_trip(_identity_snapshot(("ns1::Cache",)))
        for owner, key in _slot_dicts(doc):
            owner[key]["value"] = bad
        assert [f.status for f in _slots(snapshot_from_dict(doc))] == [
            FactStatus.NOT_COLLECTED
        ] * 4

    def test_identities_are_provenance_not_abi_content(self) -> None:
        # compare=False: two snapshots differing only in captured evidence
        # describe the same ABI, so no declaration-equality diff can fire.
        a = _identity_snapshot(("ns1::Cache",))
        b = _identity_snapshot(None)
        assert a.declarations.functions == b.declarations.functions
        assert a.declarations.variables == b.declarations.variables
        assert a.declarations.types == b.declarations.types


class TestPresentIdentities:
    @pytest.mark.parametrize(
        ("fact", "expected"),
        [
            (None, ()),
            (Fact.not_collected(), ()),
            (Fact.unsupported(), ()),
            (Fact.present(()), ()),
            (Fact.present(("ns1::Cache",)), ("ns1::Cache",)),
        ],
    )
    def test_only_a_present_fact_contributes(self, fact, expected) -> None:
        assert present_identities(fact) == expected
