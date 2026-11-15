"""The L3 build-data evidence variant of the edit corpus (``Config.build_data``)."""

from __future__ import annotations

import pytest

from abicheck.model.change_catalog.kinds import ChangeKind
from abiforge import coverage as cov
from abiforge.model.config import Toolchain, default_config
from abiforge.operators.build_data import BUILD_DATA_OPERATORS
from abiforge.run.tiers import BUILD_DATA_PR

_GCC = Toolchain("gcc", 0, "gcc", "g++")
_CLANG = Toolchain("clang", 0, "clang", "clang++")


@pytest.mark.parametrize("evidence", ["binary", "dwarf"])
def test_build_data_is_refused_below_headers(evidence: str) -> None:
    with pytest.raises(ValueError, match="build_data"):
        default_config("c", _GCC).with_(evidence=evidence, build_data=True)


def test_build_data_is_part_of_the_build_identity() -> None:
    # Two configs that differ only in build data are two library pairs: the
    # baseline cache must never hand a build-data cell a pack-less old side.
    plain = default_config("c", _GCC)
    with_l3 = plain.with_(build_data=True)
    assert plain.build_id != with_l3.build_id
    assert with_l3.evidence_label == "headers+build"
    clang_hb = with_l3.with_(header_backend="clang")
    assert clang_hb.evidence_label == "headers.clang+build"


@pytest.mark.parametrize("op", BUILD_DATA_OPERATORS, ids=lambda o: o.name)
def test_every_build_data_operator_needs_build_data(op) -> None:
    for tc in (_GCC, _CLANG):
        for lang in ("c", "c++"):
            assert op.requires(default_config(lang, tc)) is not None
    reachable = [
        tc.family
        for tc in (_GCC, _CLANG)
        for lang in ("c", "c++")
        if op.requires(default_config(lang, tc).with_(build_data=True)) is None
    ]
    assert reachable, f"{op.name} applies under no build_data config"


def test_the_l3_build_mode_bucket_is_exactly_what_the_operators_declare() -> None:
    # Oracle: the scope table's in-scope L3 bucket, by ChangeKind value, read
    # independently of the operators' own declarations.
    bucket = cov.SCOPE_BUCKET_BY_ID["l3_build_mode"]
    assert bucket.in_scope
    declared = {
        ChangeKind[name].value for op in BUILD_DATA_OPERATORS for name in op.expected
    }
    assert set(bucket.names) <= declared


def test_the_pr_tier_guards_every_build_data_operator_once() -> None:
    names = [op for op, _, _, _ in BUILD_DATA_PR]
    assert sorted(names) == sorted(op.name for op in BUILD_DATA_OPERATORS)
    assert all(ch == {"build_data": True} for _, _, _, ch in BUILD_DATA_PR)


def _op(name: str):
    return next(op for op in BUILD_DATA_OPERATORS if op.name == name)


@pytest.mark.parametrize(
    ("pack", "applies"),
    [(None, True), (1, False), (2, False), (4, False), (8, True), (16, True)],
)
def test_pack_struct_8_never_relaxes_a_tighter_baseline_packing(pack, applies) -> None:
    """``-fpack-struct=8`` appended after a tighter baseline packing (the later
    flag wins) relaxes it and changes the seed's layout, which contradicts the
    operator's layout-unchanged ground truth: such configs are refused."""
    from abiforge.model.config import Flags

    config = default_config("c", _GCC).with_(build_data=True, flags=Flags(pack=pack))
    gap = _op("l3_struct_packing_8").requires(config)
    assert (gap is None) == applies


@pytest.mark.parametrize(
    ("name", "flags"),
    [
        ("l3_lto_enabled", {"-flto"}),
        ("l3_whole_program_vtables", {"-flto=thin", "-fwhole-program-vtables"}),
    ],
)
def test_lto_operators_probe_the_link(name, flags) -> None:
    """An LTO flag fails only at link time when the linker plugin is missing,
    so the operator must carry it as a link probe (an unavailable cell)."""
    assert set(_op(name).probe_flags) == flags
