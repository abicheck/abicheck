"""Hypothesis properties of I4's coverage-aware edge query
(evidence-entity-model Phase 4).

Stated as invariants over generated coverage records and generated L5
graphs, against an independent oracle (:func:`_oracle`) that re-derives the
answer from the record *fields* -- never through ``compare.edge_query.decide``
or ``CoverageRecord.covered_units``:

- the answer is independent of the order of the records and of the graph's
  nodes/edges;
- more evidence never flips ``present`` to anything else;
- removing evidence can only turn ``proven_absent`` into ``unknown``, never
  into ``present`` -- and never turns ``unknown`` into ``proven_absent``.
"""

from __future__ import annotations

import random

from hypothesis import given, settings, strategies as st

from abicheck.model import AbiSnapshot
from abicheck.model.edge_coverage import ALL_UNITS, CoverageRecord, ProducerRun
from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.source_graph import SourceGraphSummary

UNITS = ("u1", "u2", "u3")
RUNS = ("ran", "partial", "not_run", "failed")


def _decide(observed, records, scope):
    from abicheck.compare.edge_query import decide

    return decide(observed, records, scope)[0].value


def _oracle(observed, records, scope):
    """I4, restated from the record fields alone."""
    if observed:
        return "present"
    if not records:
        return "unknown"
    if scope is None:
        wanted = set()
        for r in records:
            wanted |= set(r.units)
    else:
        wanted = set(scope)
    if not wanted:
        return "unknown"
    vouched: set[str] = set()
    for r in records:
        if r.run.value == "ran":
            vouched |= set(r.units)
        elif r.run.value == "partial":
            vouched |= set(r.covered)
    if ALL_UNITS in vouched:
        return "proven_absent"
    return "proven_absent" if wanted <= vouched else "unknown"


@st.composite
def _record(draw):
    run = draw(st.sampled_from(RUNS))
    units = frozenset(draw(st.sets(st.sampled_from(UNITS), min_size=1)))
    covered = (
        frozenset(draw(st.sets(st.sampled_from(sorted(units)))))
        if run == "partial"
        else frozenset()
    )
    return CoverageRecord("k", "p", ProducerRun(run), units, covered)


_records = st.lists(_record(), max_size=4)
_scope = st.one_of(st.none(), st.frozensets(st.sampled_from(UNITS), max_size=3))


@settings(max_examples=400, deadline=None)
@given(observed=st.booleans(), records=_records, scope=_scope)
def test_answer_matches_the_oracle(observed, records, scope):
    assert _decide(observed, records, scope) == _oracle(observed, records, scope)


@settings(max_examples=300, deadline=None)
@given(observed=st.booleans(), records=_records, scope=_scope, seed=st.integers())
def test_answer_is_independent_of_record_order(observed, records, scope, seed):
    shuffled = list(records)
    random.Random(seed).shuffle(shuffled)
    assert _decide(observed, records, scope) == _decide(observed, shuffled, scope)


@settings(max_examples=300, deadline=None)
@given(records=_records, extra=_records, scope=_scope)
def test_more_evidence_never_flips_present(records, extra, scope):
    assert _decide(True, records, scope) == "present"
    assert _decide(True, [*records, *extra], scope) == "present"


def _weaken(rec: CoverageRecord, how: int) -> CoverageRecord:
    """One strictly weaker record with the same responsibility."""
    if how == 0 or rec.run is not ProducerRun.PARTIAL:
        run = ProducerRun.FAILED if how % 2 else ProducerRun.NOT_RUN
        return CoverageRecord(rec.edge_kind, rec.producer, run, rec.units)
    return CoverageRecord(
        rec.edge_kind, rec.producer, ProducerRun.PARTIAL, rec.units,
        frozenset(sorted(rec.covered)[1:]),
    )  # fmt: skip


@settings(max_examples=400, deadline=None)
@given(
    records=_records.filter(bool),
    scope=_scope,
    idx=st.integers(0, 3),
    how=st.integers(0, 3),
)
def test_removing_evidence_only_turns_proven_absent_into_unknown(
    records, scope, idx, how
):
    before = _decide(False, records, scope)
    i = idx % len(records)
    weakened = [*records[:i], _weaken(records[i], how), *records[i + 1 :]]
    after = _decide(False, weakened, scope)
    assert (before, after) in {
        ("proven_absent", "proven_absent"),
        ("proven_absent", "unknown"),
        ("unknown", "unknown"),
    }


@settings(max_examples=200, deadline=None)
@given(observed=st.booleans(), records=_records.filter(bool), scope=_scope)
def test_proven_absent_always_rests_on_a_covering_record(observed, records, scope):
    if _decide(observed, records, scope) == "proven_absent":
        assert any(r.run.value in ("ran", "partial") for r in records)


# ---------------------------------------------------------------------------
# End to end over generated L5 graphs
# ---------------------------------------------------------------------------

NODES = ("n0", "n1", "n2", "n3")


@st.composite
def _graph_spec(draw):
    edges = draw(
        st.sets(st.tuples(st.sampled_from(NODES), st.sampled_from(NODES)), max_size=6)
    )
    state = draw(st.sampled_from(("ran", "narrowed", "degraded", "none", "header")))
    scope = draw(st.frozensets(st.sampled_from(UNITS), max_size=2))
    return sorted(edges), state, scope


def _build(edges, state, scope, *, order_seed=0, extra_edges=()):
    graph = SourceGraphSummary()
    nodes = list(NODES)
    random.Random(order_seed).shuffle(nodes)
    for n in nodes:
        graph.add_node(GraphNode(id=n, kind="source_decl"))
    all_edges = [*edges, *extra_edges]
    random.Random(order_seed + 1).shuffle(all_edges)
    for src, dst in all_edges:
        graph.add_edge(GraphEdge(src=src, dst=dst, kind="DECL_CALLS_DECL"))
    if state == "ran":
        graph.extractor_passes["call_graph"] = True
    elif state == "narrowed":
        graph.narrowed_passes["call_graph"] = True
        graph.narrowed_scope["call_graph"] = scope
    elif state == "degraded":
        graph.degraded_passes["call_graph"] = True
    elif state == "header":
        graph.extractor_passes["header_call_graph"] = True
    return graph


def _query(graph, src, dst, scope):
    from abicheck.compare.edge_query import EdgeEvidence

    ev = EdgeEvidence(AbiSnapshot(library="l", version="1"), source_graph=graph)
    return ev.query("DECL_CALLS_DECL", src, target=dst, scope=scope).answer.value


def _l5_oracle(edges, state, pass_scope, src, dst, query_scope):
    if (src, dst) in edges or (dst, src) in edges:
        return "present"
    if query_scope is not None and not query_scope:
        return "unknown"  # a query over no unit proves nothing
    if state == "ran":
        return "proven_absent"
    if state == "narrowed" and query_scope and query_scope <= pass_scope:
        return "proven_absent"
    return "unknown"  # degraded, unrecorded, header-only (body-blind for calls)


@settings(max_examples=200, deadline=None)
@given(
    spec=_graph_spec(),
    src=st.sampled_from(NODES),
    dst=st.sampled_from(NODES),
    qscope=_scope,
    seed=st.integers(0, 1000),
)
def test_l5_query_matches_oracle_and_ignores_order(spec, src, dst, qscope, seed):
    edges, state, scope = spec
    want = _l5_oracle(set(edges), state, scope, src, dst, qscope)
    assert _query(_build(edges, state, scope), src, dst, qscope) == want
    assert (
        _query(_build(edges, state, scope, order_seed=seed), src, dst, qscope) == want
    )


@settings(max_examples=150, deadline=None)
@given(spec=_graph_spec(), src=st.sampled_from(NODES), dst=st.sampled_from(NODES),
       more=st.sets(st.tuples(st.sampled_from(NODES), st.sampled_from(NODES)), max_size=3))  # fmt: skip
def test_l5_more_edges_never_flip_present(spec, src, dst, more):
    edges, state, scope = spec
    if _query(_build(edges, state, scope), src, dst, None) == "present":
        assert (
            _query(
                _build(edges, state, scope, extra_edges=sorted(more)), src, dst, None
            )
            == "present"
        )


def test_generators_reach_every_answer():
    """Vacuity guard: the record generator can produce each answer."""
    ran = CoverageRecord("k", "p", ProducerRun.RAN, frozenset({"u1"}))
    failed = CoverageRecord("k", "p", ProducerRun.FAILED, frozenset({"u1"}))
    assert {
        _decide(True, [], None),
        _decide(False, [ran], None),
        _decide(False, [failed], None),
    } == {
        "present",
        "proven_absent",
        "unknown",
    }
