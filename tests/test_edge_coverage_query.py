"""Coverage-aware edge queries (evidence-entity-model Phase 4, invariant I4).

"Absence is typed": a query for edge kind K in scope S answers ``present``,
``proven_absent`` or ``unknown``, and ``proven_absent`` requires the producer
of K to have covered S.

Every expectation here is stated by hand from the fixture or by the
independent truth table / oracle below -- never recomputed through
``compare.edge_query.decide`` or a coverage record's own ``covered_units`` --
so the oracle cannot share a bug with the implementation.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.compare.surface_graph import EDGE_EVIDENCE_CLASS
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.macho_metadata import MachoExport, MachoMetadata
from abicheck.model import AbiSnapshot, Function, Param, RecordType, Visibility
from abicheck.model.dwarf_facts import DwarfMetadata, StructLayout
from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.source_graph import SourceGraphSummary

PRESENT, ABSENT, UNKNOWN = "present", "proven_absent", "unknown"

#: Strict xfail until the query API lands (tests-first, flipped per commit).
_PENDING = pytest.mark.xfail(
    strict=True,
    raises=ImportError,
    reason="I4 query API (compare/edge_query.py) not implemented yet",
)


def _evidence(snap, graph=None):
    from abicheck.compare.edge_query import EdgeEvidence

    return EdgeEvidence(snap, source_graph=graph)


def _answer(evidence, kind, subject, **kw):
    return evidence.query(kind, subject, **kw).answer.value


def _fn(name, mangled, *params, header="api.h"):
    return Function(
        name=name,
        mangled=mangled,
        return_type="int",
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
        source_header=header,
    )


def _rec(name, size_bits=64, header="api.h"):
    return RecordType(
        name=name, kind="struct", size_bits=size_bits, source_header=header
    )


def _snap(functions=(), types=(), *, elf=None, macho=None, dwarf=None,
          from_headers=True, dependency_scope="filtered", platform=None):  # fmt: skip
    return AbiSnapshot(
        library="libx.so",
        version="1",
        functions=list(functions),
        types=list(types),
        elf=elf,
        macho=macho,
        dwarf=dwarf,
        from_headers=from_headers,
        dependency_scope=dependency_scope,
        platform=platform,
    )


def _elf(*names, machine="EM_X86_64"):
    return ElfMetadata(symbols=[ElfSymbol(name=n) for n in names], machine=machine)


# ---------------------------------------------------------------------------
# A producer that did not run answers unknown, never proven_absent
# ---------------------------------------------------------------------------


@_PENDING
class TestProducerDidNotRun:
    def test_no_export_table_makes_every_declaration_unknown(self):
        ev = _evidence(_snap([_fn("run", "run"), _fn("go", "go")]))
        assert _answer(ev, "exports", "decl://run") == UNKNOWN
        assert _answer(ev, "exports", "decl://go") == UNKNOWN

    def test_owed_but_missing_table_is_unknown(self):
        # platform says ELF, but no ELF block was captured.
        ev = _evidence(_snap([_fn("run", "run")], platform="elf"))
        res = ev.query("exports", "decl://run")
        assert res.answer.value == UNKNOWN
        assert [(r.run.value, r.reason) for r in res.records] == [
            ("not_run", "no_export_table")
        ]

    def test_no_header_ast_makes_declares_and_references_unknown(self):
        snap = _snap(
            [_fn("run", "run", "S")], [_rec("S"), _rec("T")], from_headers=False
        )
        ev = _evidence(snap)
        assert _answer(ev, "declares", "decl://run") == PRESENT  # still observed
        snap.functions[0].source_header = None
        ev = _evidence(snap)
        assert _answer(ev, "declares", "decl://run") == UNKNOWN
        assert _answer(ev, "references", "type://S") == PRESENT
        assert _answer(ev, "references", "type://T") == UNKNOWN

    def test_inferred_from_headers_is_not_a_header_run(self):
        snap = _snap([_fn("run", "run", header=None)])
        snap.from_headers_inferred = True
        assert _answer(_evidence(snap), "declares", "decl://run") == UNKNOWN

    def test_export_with_no_declaration_is_unknown_without_headers(self):
        snap = _snap([], elf=_elf("orphan"), from_headers=False)
        ev = _evidence(snap)
        assert _answer(ev, "exports", "binary_symbol://elf/orphan") == UNKNOWN

    def test_unrecorded_l5_pass_is_unknown(self):
        graph = SourceGraphSummary()
        graph.add_node(GraphNode(id="d:a", kind="source_decl"))
        graph.add_node(GraphNode(id="d:b", kind="source_decl"))
        ev = _evidence(_snap(), graph)
        assert _answer(ev, "DECL_CALLS_DECL", "d:a", target="d:b") == UNKNOWN

    def test_no_source_graph_is_unknown(self):
        ev = _evidence(_snap())
        for kind in ("DECL_CALLS_DECL", "TYPE_INHERITS", "COMPILE_UNIT_INCLUDES_FILE"):
            assert _answer(ev, kind, "d:a") == UNKNOWN


# ---------------------------------------------------------------------------
# Partial coverage answers unknown for the uncovered part
# ---------------------------------------------------------------------------


def _l5_graph(*, ran=(), narrowed=(), scope=(), degraded=(), edges=()):
    graph = SourceGraphSummary()
    for nid in ("d:a", "d:b", "d:c"):
        graph.add_node(GraphNode(id=nid, kind="source_decl"))
    for src, dst in edges:
        graph.add_edge(GraphEdge(src=src, dst=dst, kind="DECL_CALLS_DECL"))
    for p in ran:
        graph.extractor_passes[p] = True
    for p in narrowed:
        graph.narrowed_passes[p] = True
        graph.narrowed_scope[p] = frozenset(scope)
    for p in degraded:
        graph.degraded_passes[p] = True
    return graph


@_PENDING
class TestPartialCoverage:
    def test_narrowed_pass_covers_only_its_units(self):
        graph = _l5_graph(narrowed=["call_graph"], scope=["src/a.cpp"])
        ev = _evidence(_snap(), graph)
        q = dict(target="d:b")
        assert (
            _answer(ev, "DECL_CALLS_DECL", "d:a", scope=frozenset({"src/a.cpp"}), **q)
            == ABSENT
        )
        assert (
            _answer(ev, "DECL_CALLS_DECL", "d:a", scope=frozenset({"src/b.cpp"}), **q)
            == UNKNOWN
        )
        assert (
            _answer(
                ev,
                "DECL_CALLS_DECL",
                "d:a",
                scope=frozenset({"src/a.cpp", "src/b.cpp"}),
                **q,
            )
            == UNKNOWN
        )
        assert _answer(ev, "DECL_CALLS_DECL", "d:a", **q) == UNKNOWN  # whole project

    def test_header_only_pass_is_body_blind_for_calls(self):
        graph = _l5_graph(ran=["header_call_graph"])
        ev = _evidence(_snap(), graph)
        assert _answer(ev, "DECL_CALLS_DECL", "d:a", target="d:b") == UNKNOWN

    def test_header_only_pass_covers_structural_kinds(self):
        graph = _l5_graph(ran=["header_type_graph"])
        ev = _evidence(_snap(), graph)
        assert _answer(ev, "TYPE_INHERITS", "d:a", target="d:b") == ABSENT
        assert _answer(ev, "DECL_REFERENCES_DECL", "d:a", target="d:b") == UNKNOWN

    def test_one_of_two_export_tables_unread(self):
        # ELF read, Mach-O block present but never parsed.
        snap = _snap([_fn("run", "run")], elf=_elf("other"), macho=MachoMetadata())
        ev = _evidence(snap)
        assert _answer(ev, "exports", "decl://run") == UNKNOWN
        assert _answer(ev, "exports", "decl://run", scope=frozenset({"elf"})) == ABSENT
        assert (
            _answer(ev, "exports", "decl://run", scope=frozenset({"macho"})) == UNKNOWN
        )

    def test_filtered_dependency_headers_do_not_prove_a_toolchain_export_undeclared(
        self,
    ):
        snap = _snap([], elf=_elf("_ZNSt6thread4joinEv", "mylib_init"))
        ev = _evidence(snap)
        assert (
            _answer(ev, "exports", "binary_symbol://elf/_ZNSt6thread4joinEv") == UNKNOWN
        )
        assert _answer(ev, "exports", "binary_symbol://elf/mylib_init") == ABSENT

    def test_full_dependency_scope_proves_a_toolchain_export_undeclared(self):
        snap = _snap([], elf=_elf("_ZNSt6thread4joinEv"), dependency_scope="full")
        ev = _evidence(snap)
        assert (
            _answer(ev, "exports", "binary_symbol://elf/_ZNSt6thread4joinEv") == ABSENT
        )


# ---------------------------------------------------------------------------
# A failed extractor gives unknown, never an empty "absent"
# ---------------------------------------------------------------------------


@_PENDING
class TestFailedExtractor:
    def test_default_elf_block_is_not_an_empty_table(self):
        # A parse-failed / default ElfMetadata(): no symbols, no machine.
        snap = _snap([_fn("run", "run")], elf=ElfMetadata())
        res = _evidence(snap).query("exports", "decl://run")
        assert res.answer.value == UNKNOWN
        assert [(r.run.value, r.reason) for r in res.records] == [
            ("failed", "export_table_not_read")
        ]

    def test_parsed_library_exporting_nothing_is_a_covered_empty_table(self):
        snap = _snap([_fn("run", "run")], elf=_elf())  # machine set, no symbols
        assert _answer(_evidence(snap), "exports", "decl://run") == ABSENT

    def test_degraded_pass_is_unknown_but_its_edges_stay_present(self):
        graph = _l5_graph(degraded=["call_graph"], edges=[("d:a", "d:b")])
        ev = _evidence(_snap(), graph)
        assert _answer(ev, "DECL_CALLS_DECL", "d:a", target="d:b") == PRESENT
        assert _answer(ev, "DECL_CALLS_DECL", "d:a", target="d:c") == UNKNOWN
        res = ev.query("DECL_CALLS_DECL", "d:a", target="d:c")
        assert [(r.run.value, r.reason) for r in res.records] == [
            ("failed", "pass_degraded")
        ]


# ---------------------------------------------------------------------------
# A stripped binary makes every debug_type_of query unknown
# ---------------------------------------------------------------------------


@_PENDING
class TestStrippedBinary:
    @pytest.mark.parametrize(
        "dwarf",
        [None, DwarfMetadata(has_dwarf=False)],
        ids=["no-block", "has_dwarf=False"],
    )
    def test_every_header_type_is_unknown(self, dwarf):
        types = [_rec(n) for n in ("A", "B", "C", "D")]
        ev = _evidence(_snap(types=types, elf=_elf("x"), dwarf=dwarf))
        answers = {
            t.name: _answer(ev, "debug_type_of", f"type://{t.name}") for t in types
        }
        assert answers == dict.fromkeys("ABCD", UNKNOWN)

    def test_with_debug_info_a_missing_type_is_proven_absent(self):
        dwarf = DwarfMetadata(
            has_dwarf=True, structs={"A": StructLayout(name="A", byte_size=8)}
        )
        ev = _evidence(_snap(types=[_rec("A"), _rec("B")], dwarf=dwarf))
        assert _answer(ev, "debug_type_of", "type://A") == PRESENT
        assert _answer(ev, "debug_type_of", "type://B") == ABSENT


# ---------------------------------------------------------------------------
# Exhaustive small-domain enumeration against an independent truth table
# ---------------------------------------------------------------------------

#: (producer, edge present, subject in the producer's covered scope) -> answer.
#: Stated by hand from I4, not derived from the implementation.
TRUTH = {
    ("ran", True, True): PRESENT,
    ("ran", True, False): PRESENT,
    ("ran", False, True): ABSENT,
    ("ran", False, False): ABSENT,  # a whole-project run covers every unit
    ("not_run", True, True): PRESENT,
    ("not_run", True, False): PRESENT,
    ("not_run", False, True): UNKNOWN,
    ("not_run", False, False): UNKNOWN,
    ("partial", True, True): PRESENT,
    ("partial", True, False): PRESENT,
    ("partial", False, True): ABSENT,
    ("partial", False, False): UNKNOWN,
    ("failed", True, True): PRESENT,
    ("failed", True, False): PRESENT,
    ("failed", False, True): UNKNOWN,
    ("failed", False, False): UNKNOWN,
}

PRODUCERS = ("ran", "not_run", "partial", "failed")


def test_truth_table_is_exhaustive_and_not_constant():
    assert set(TRUTH) == set(itertools.product(PRODUCERS, (True, False), (True, False)))
    assert set(TRUTH.values()) == {PRESENT, ABSENT, UNKNOWN}


def _l5_case(producer, edge):
    kw = {
        "ran": dict(ran=["call_graph"]),
        "not_run": {},
        "partial": dict(narrowed=["call_graph"], scope=["in.cpp"]),
        "failed": dict(degraded=["call_graph"]),
    }[producer]
    return _l5_graph(edges=[("d:a", "d:b")] if edge else [], **kw)


def _export_case(producer, edge):
    """Two tables owed, ``elf`` is the queried unit when "in scope"."""
    symbols = ("run",) if edge else ("other",)
    macho = MachoMetadata(exports=[MachoExport(name="zzz")], filetype="MH_DYLIB")
    elf = {
        "ran": _elf(*symbols),
        "not_run": None,
        "partial": _elf(*symbols),
        "failed": ElfMetadata(symbols=[]),
    }[producer]
    if producer == "partial":
        macho = MachoMetadata()  # owed (carried) but never read
    if producer == "failed" and edge:
        # a failed parse that still recovered the entry
        elf = ElfMetadata(symbols=[ElfSymbol(name="run")])
    if producer == "not_run" and edge:
        elf = None
        macho = MachoMetadata(exports=[MachoExport(name="run")], filetype="MH_DYLIB")
    return _snap([_fn("run", "run")], elf=elf, macho=macho, platform="elf")


@pytest.mark.parametrize("producer,edge,in_scope", sorted(TRUTH), ids=lambda v: str(v))
@_PENDING
def test_l5_matrix(producer, edge, in_scope):
    ev = _evidence(_snap(), _l5_case(producer, edge))
    scope = frozenset({"in.cpp" if in_scope else "out.cpp"})
    got = _answer(ev, "DECL_CALLS_DECL", "d:a", target="d:b", scope=scope)
    assert got == TRUTH[(producer, edge, in_scope)]


#: The export-table fixture differs from the L5 one in two stated ways: a
#: ``ran`` ELF table is responsible for the ``elf`` unit only, so a query
#: about the other table is not covered by it; and ``not_run`` here means the
#: owed ELF table is missing while the Mach-O one was read.
EXPORT_TRUTH = {
    **TRUTH,
    ("ran", False, False): ABSENT,  # Mach-O was read too, and lacks "run"
    ("not_run", True, False): PRESENT,
    ("not_run", False, False): ABSENT,  # Mach-O read, no "run" there
    ("not_run", True, True): PRESENT,  # observed in Mach-O: present anywhere
    ("failed", False, False): ABSENT,  # Mach-O read
}


@pytest.mark.parametrize(
    "producer,edge,in_scope", sorted(EXPORT_TRUTH), ids=lambda v: str(v)
)
@_PENDING
def test_export_matrix(producer, edge, in_scope):
    ev = _evidence(_export_case(producer, edge))
    scope = frozenset({"elf" if in_scope else "macho"})
    got = _answer(ev, "exports", "decl://run", scope=scope)
    assert got == EXPORT_TRUTH[(producer, edge, in_scope)]


# ---------------------------------------------------------------------------
# Every edge kind is queryable; every answer carries its records
# ---------------------------------------------------------------------------


@_PENDING
def test_every_surface_graph_edge_kind_is_queryable():
    from abicheck.compare.edge_query import QUERYABLE_EDGE_KINDS

    assert set(EDGE_EVIDENCE_CLASS) <= QUERYABLE_EDGE_KINDS
    assert {"DECL_CALLS_DECL", "COMPILE_UNIT_INCLUDES_FILE"} <= QUERYABLE_EDGE_KINDS


@_PENDING
def test_unknown_edge_kind_is_rejected():
    with pytest.raises(KeyError):
        _evidence(_snap()).query("no_such_kind", "x")


@_PENDING
def test_unknown_subject_is_unknown_not_absent():
    ev = _evidence(_snap([_fn("run", "run")], elf=_elf("run")))
    assert _answer(ev, "exports", "decl://nope") == UNKNOWN
    assert ev.query("exports", "decl://nope").reason == "subject_unknown"


@_PENDING
def test_derived_linker_name_answers_from_its_records():
    snap = _snap([_fn("run", "_Z3runv"), _fn("c_fn", "")])
    ev = _evidence(snap)
    assert _answer(ev, "declares_linker_name", "decl://_Z3runv") == PRESENT
    # The C declaration records no linker name; its node id is whatever the
    # I1 identity table assigns (an explicit unresolved node).
    from abicheck.model.graph_entity_identity import snapshot_identities

    c_node = snapshot_identities(snap).functions[1].node_id
    assert _answer(ev, "declares_linker_name", c_node) == ABSENT


@_PENDING
def test_references_through_an_ambiguous_spelling_is_unknown():
    a = RecordType(
        name="Foo", qualified_name="a::Foo", kind="struct", source_header="api.h"
    )
    b = RecordType(
        name="Foo", qualified_name="b::Foo", kind="struct", source_header="api.h"
    )
    snap = _snap([_fn("use", "_Z3use3Foo", "Foo")], [a, b])
    ev = _evidence(snap)
    res = ev.query("references", "decl://_Z3use3Foo", target="type://a::Foo")
    assert res.answer.value == UNKNOWN
    assert res.records[0].reason.startswith("ambiguous_type_spelling")


@_PENDING
def test_result_serializes_its_records():
    ev = _evidence(_snap([_fn("run", "run")], elf=ElfMetadata()))
    d = ev.query("exports", "decl://run").to_dict()
    assert d["answer"] == UNKNOWN
    assert d["records"][0]["run"] == "failed"
    assert d["records"][0]["covered"] == []
