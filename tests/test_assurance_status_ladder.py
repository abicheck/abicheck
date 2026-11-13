"""``AnalysisAssurance.status`` ladder: every sub-status value has a rung.

The ladder was an if-chain naming the shortfall spellings it knew, so any
other spelling read as ``complete``. Two checks:

* each sub-status table covers exactly its ``Literal`` vocabulary, so adding
  a value without deciding its rung fails here;
* over the whole input space, the table-driven ladder equals the if-chain it
  replaced (copied below as the oracle, not imported).
"""

from __future__ import annotations

import itertools
from typing import get_args

import pytest

from abicheck.policy import assurance_status_vocabulary as vocab
from abicheck.policy.analysis_assurance import _status_ladder as ladder


@pytest.mark.parametrize(
    ("table", "literal"),
    [
        (ladder.CONTEXT_SHORTFALL, vocab.ContextStatus),
        (ladder.HEADER_SHORTFALL, vocab.HeaderContextStatus),
        (ladder.SCHEMA_STALENESS_SHORTFALL, vocab.SchemaStalenessStatus),
        (ladder.GRAPH_SHORTFALL, vocab.GraphCompleteness),
        (ladder.FACT_SET_RUNG, vocab.FactSetComparability),
    ],
)
def test_every_vocabulary_value_has_a_rung(table, literal) -> None:  # type: ignore[no-untyped-def]
    assert set(table) == set(get_args(literal))


def _old_if_chain(
    fs, depth_unsat, l0, hdr, dwarf, l3, stale, graph, other, nothing
) -> str:  # type: ignore[no-untyped-def]
    if fs == "inconsistent":
        return "failed"
    if depth_unsat:
        return "failed"
    if (
        l0 == "asymmetric"
        or hdr in ("drift_detected", "asymmetric")
        or dwarf == "asymmetric"
        or l3 == "asymmetric"
        or stale == "degraded"
        or graph in ("degraded", "narrowed", "unknown")
        or other
        or fs == "unknown"
    ):
        return "partial"
    if nothing:
        return "not_requested"
    return "complete"


def test_table_ladder_equals_the_if_chain_over_the_whole_space() -> None:
    ctx = get_args(vocab.ContextStatus)
    space = itertools.product(
        get_args(vocab.FactSetComparability),
        (False, True),
        ctx,
        get_args(vocab.HeaderContextStatus),
        ctx,
        ctx,
        get_args(vocab.SchemaStalenessStatus),
        get_args(vocab.GraphCompleteness),
        (False, True),
        (False, True),
    )
    seen = set()
    for fs, du, l0, hdr, dw, l3, st, gr, other, nothing in space:
        got = ladder.overall_status(
            fact_set_comparability=fs,
            depth_unsatisfied=du,
            l0_context_status=l0,
            header_context_status=hdr,
            dwarf_context_status=dw,
            l3_context_status=l3,
            schema_staleness_status=st,
            graph_completeness=gr,
            other_shortfall=other,
            nothing_requested=nothing,
        )
        assert got == _old_if_chain(fs, du, l0, hdr, dw, l3, st, gr, other, nothing)
        seen.add(got)
    # Vacuity guard: the space reaches every rung.
    assert seen == {"failed", "partial", "not_requested", "complete"}
