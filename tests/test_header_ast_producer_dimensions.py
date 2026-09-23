"""A waived cross-producer (castxml vs. clang) profile mismatch must not
report the declaration or source axis as trusted.

Measured on oneDAL: the same pair gave 1,594 findings comparable-style and
21,120 cross-producer, almost all declaration-level (func_removed/added,
visibility public 2,149 -> 4), yet ``comparability_assurance`` read
``declaration: trusted`` because ``compiler_family``/``compiler_version``
map to layout/runtime only.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.comparability import (
    ComparabilityMismatch,
    check_contracts_comparable,
    compute_extraction_contract,
    dimension_assurance,
)
from abicheck.comparability_profile import header_ast_producer_dimensions
from abicheck.model import AbiSnapshot

PRODUCERS = ("castxml", "clang", "")
FAMILIES = ("gcc", "clang")


def _snap(producer: str, family: str) -> AbiSnapshot:
    contract = compute_extraction_contract(
        l2_frontend_ran=True, compiler_family=family, compiler_version="1"
    )
    toolchain = {"producer": producer} if producer else {}
    return AbiSnapshot(
        library="libfoo.so", version="1", contract=contract, ast_toolchain=toolchain
    )


@pytest.mark.parametrize(
    ("old_p", "new_p", "old_f", "new_f"),
    list(itertools.product(PRODUCERS, PRODUCERS, FAMILIES, FAMILIES)),
)
def test_producer_difference_always_unverifies_declaration_and_source(
    old_p, new_p, old_f, new_f
):
    result = check_contracts_comparable(
        _snap(old_p, old_f), _snap(new_p, new_f), diagnostic=True
    )
    if old_f == new_f:
        # Identical fingerprints: no mismatch, whatever the producer.
        assert result is None
        return
    assert isinstance(result, ComparabilityMismatch)
    assert result.dimensions == _expected_dimensions(old_p, new_p)
    assert dimension_assurance(result) == _expected_assurance(old_p, new_p)


def _producers_differ(old_p: str, new_p: str) -> bool:
    return bool(old_p and new_p and old_p != new_p)


def _expected_dimensions(old_p: str, new_p: str) -> frozenset[str]:
    """Oracle stated independently of the implementation's constants: a
    compiler-family difference touches layout and runtime; a producer
    difference adds declaration and source."""
    base = {"layout", "runtime"}
    if _producers_differ(old_p, new_p):
        base |= {"declaration", "source"}
    return frozenset(base)


def _expected_assurance(old_p: str, new_p: str) -> dict[str, str]:
    unverified = _expected_dimensions(old_p, new_p)
    return {
        axis: ("unverified" if axis in unverified else "trusted")
        for axis in ("declaration", "layout", "runtime", "source", "symbol")
    }


def test_helper_is_symmetric_and_empty_without_evidence():
    for a, b in itertools.product(PRODUCERS, repeat=2):
        sa, sb = _snap(a, "gcc"), _snap(b, "gcc")
        assert header_ast_producer_dimensions(sa, sb) == header_ast_producer_dimensions(
            sb, sa
        )
        if not (a and b) or a == b:
            assert header_ast_producer_dimensions(sa, sb) == frozenset()
