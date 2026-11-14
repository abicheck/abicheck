"""The L4/L5 source evidence variant of the edit corpus (``Config.sources``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.model.change_catalog.kinds import ChangeKind
from abiforge import coverage as cov
from abiforge.model.config import Toolchain, default_config
from abiforge.model.expect import PASSING_CLASSES
from abiforge.model.seeds import SEED_BY_NAME
from abiforge.operators.source_evidence import SOURCE_EVIDENCE_OPERATORS
from abiforge.run.cells import CellContext, run_cell
from abiforge.run.tiers import SOURCE_EVIDENCE_PR, SOURCES_CONFIG
from abiforge.run.toolchains import newest

_GCC = Toolchain("gcc", 0, "gcc", "g++")
_CLANG = Toolchain("clang", 0, "clang", "clang++")


@pytest.mark.parametrize("evidence", ["binary", "dwarf"])
def test_sources_is_refused_below_headers(evidence: str) -> None:
    with pytest.raises(ValueError, match="sources"):
        default_config("c++", _GCC).with_(evidence=evidence, sources=True)


def test_sources_is_part_of_the_build_identity() -> None:
    plain = default_config("c++", _GCC)
    with_src = plain.with_(sources=True)
    assert plain.build_id != with_src.build_id
    assert with_src.evidence_label == "headers+src"
    both = with_src.with_(build_data=True, header_backend="clang")
    assert both.evidence_label == "headers.clang+build+src"


@pytest.mark.parametrize("op", SOURCE_EVIDENCE_OPERATORS, ids=lambda o: o.name)
def test_every_source_operator_needs_sources(op) -> None:
    seed = SEED_BY_NAME["cxx_src"]
    assert op.applies_to(seed)
    # Only the gated seed takes the operator.
    assert [s for s in ("cxx_alt", "cxx_more") if op.applies_to(SEED_BY_NAME[s])] == []
    for tc in (_GCC, _CLANG):
        assert op.requires(default_config("c++", tc)) is not None
        assert op.requires(default_config("c++", tc).with_(**SOURCES_CONFIG)) is None


def test_the_in_scope_source_bucket_is_exactly_what_the_operators_declare() -> None:
    # Oracle: the scope table's in-scope source bucket, by ChangeKind value.
    bucket = cov.SCOPE_BUCKET_BY_ID["l4_l5_source_evidence"]
    assert bucket.in_scope
    declared = {
        ChangeKind[name].value
        for op in SOURCE_EVIDENCE_OPERATORS
        for name in op.expected
    }
    assert set(bucket.names) <= declared


def test_every_source_kind_sits_in_a_named_reasoned_bucket() -> None:
    # Oracle: the catalog's own taxonomy. Every kind the source module declares
    # is placed by a named bucket (in scope, or out with its own reason); there
    # is no catch-all module bucket for it.
    source = [n for n, _, mod in cov.checker.kind_catalog() if mod == "source"]
    assert source
    named = {b.id for b in cov.SCOPE_BUCKETS if b.named}
    held = cov.scope_buckets()
    placed = {k for b in named for k in held[b]}
    assert set(source) <= placed
    assert "source_evidence" not in cov.SCOPE_BUCKET_BY_ID


def test_the_pr_tier_guards_every_source_operator_on_both_families() -> None:
    names = [op for op, _, _, _ in SOURCE_EVIDENCE_PR]
    assert sorted(names) == sorted(op.name for op in SOURCE_EVIDENCE_OPERATORS)
    assert {fam for _, _, fam, _ in SOURCE_EVIDENCE_PR} == {"gcc", "clang"}


_OTHER = {"gcc": "clang", "clang": "gcc"}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("op_name", "family"),
    [(op, _OTHER[fam]) for op, _, fam, _ in SOURCE_EVIDENCE_PR],
    ids=[f"{op}-{_OTHER[fam]}" for op, _, fam, _ in SOURCE_EVIDENCE_PR],
)
def test_source_operator_on_the_other_family(
    op_name: str, family: str, tmp_path: Path
) -> None:
    # The PR tier runs each operator on one family; this runs it on the
    # other, so every source-evidence kind is verified on gcc and clang.
    from abiforge.operators import OP_BY_NAME

    tc = newest(family)
    if tc is None:
        pytest.skip(f"{family} is not installed")
    config = default_config("c++", tc).with_(**SOURCES_CONFIG)
    cell = run_cell(
        OP_BY_NAME[op_name], SEED_BY_NAME["cxx_src"], config, CellContext.at(tmp_path)
    )
    if cell.status in ("unavailable", "infeasible"):
        pytest.skip(cell.reason)
    assert cell.cls in PASSING_CLASSES, cell.describe()
