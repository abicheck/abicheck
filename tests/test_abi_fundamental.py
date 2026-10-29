"""The ABI-equivalence rule for fundamental slots (schema v69, model/abi_fundamental.py).

The oracle below is written from the calling-convention statement, not from the
implementation: it names the register class of each fact in its own words and
decides equality from those, so an error in ``abi_equivalent`` cannot also be
an error in the oracle.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.model.abi_fundamental import (
    AbiFundamental,
    FundamentalEncoding,
    abi_equivalent,
)

_S = FundamentalEncoding.SIGNED
_U = FundamentalEncoding.UNSIGNED
_B = FundamentalEncoding.BOOLEAN
_F = FundamentalEncoding.FLOAT

_SIZES = (1, 2, 4, 8, 16, 32)
_ALIGNS = (1, 2, 4, 8, 16, 32)
_LANES = (0, 1, 2, 4)


def _class_of(fact: AbiFundamental) -> str:
    """The register class, in words: a vector of at most 16 bytes and a float of
    2, 4 or 8 bytes travel in a vector register; an integer of at most 16 bytes
    in a general register; a boolean has its own class; anything else (x87 or
    quad float, a vector over 16 bytes) is opaque."""
    if fact.lanes:
        return "vector" if fact.size <= 16 else "opaque"
    if fact.encoding is _B:
        return "boolean"
    if fact.encoding is _F:
        return "vector" if fact.size in (2, 4, 8) else "opaque"
    return "general" if fact.size <= 16 else "opaque"


def _oracle(a: AbiFundamental, b: AbiFundamental) -> bool:
    """The calling-convention answer to 'do a and b pass, store and return alike'."""
    if a == b:
        return True
    if a.size != b.size or a.align != b.align:
        return False
    cls = _class_of(a)
    if cls != _class_of(b):
        return False
    if cls == "general":
        # Below 8 bytes a caller extends the value by the type's signedness.
        return a.encoding is b.encoding or a.size >= 8
    if cls == "vector":
        # Any two vector-register values of one size agree: the lane split is
        # not part of the register class.
        return True
    # boolean: only the same boolean; opaque: only an identical fact (handled above).
    return cls == "boolean" and a.encoding is b.encoding


def _domain() -> list[AbiFundamental]:
    facts = []
    for encoding, size, align, lanes in itertools.product(
        (_S, _U, _B, _F), _SIZES, _ALIGNS, _LANES
    ):
        if lanes and encoding is _B:
            continue  # a vector of booleans is not a type this rule speaks of
        facts.append(AbiFundamental(encoding, size, align, lanes))
    return facts


_FACTS = _domain()


def test_domain_is_the_expected_size() -> None:
    """Pins the enumeration so an empty or truncated domain cannot pass."""
    assert len(_FACTS) == (4 * 6 * 6 * 4) - (6 * 6 * 3)


def test_abi_equivalent_matches_the_calling_convention_oracle_over_the_domain() -> None:
    """Every ordered pair of the enumerated domain agrees with the oracle."""
    mismatches = [
        (old, new)
        for old, new in itertools.product(_FACTS, repeat=2)
        if abi_equivalent(old, new) != _oracle(old, new)
    ]
    assert mismatches == [], mismatches[:5]


def test_equivalence_is_symmetric_and_reflexive_over_the_domain() -> None:
    sample = _FACTS[::3]
    for a in sample:
        assert abi_equivalent(a, a)
        for b in sample:
            assert abi_equivalent(a, b) == abi_equivalent(b, a), (a, b)


@pytest.mark.parametrize(
    ("old", "new", "equal"),
    [
        # Same bytes, same class, different name: equal.
        (AbiFundamental(_S, 8, 8), AbiFundamental(_S, 8, 8), True),
        (AbiFundamental(_S, 8, 8), AbiFundamental(_U, 8, 8), True),  # long/unsigned long
        (AbiFundamental(_S, 16, 16), AbiFundamental(_U, 16, 16), True),  # __int128
        (AbiFundamental(_S, 1, 1), AbiFundamental(_S, 1, 1), True),  # char/signed char
        (AbiFundamental(_U, 4, 4), AbiFundamental(_U, 4, 4), True),  # enum -> unsigned
        # A float vector of 8 bytes and a double pass in the same register.
        (AbiFundamental(_F, 8, 8, 2), AbiFundamental(_F, 8, 8), True),
        (AbiFundamental(_S, 16, 16, 4), AbiFundamental(_F, 16, 16, 4), True),
        # Sign at 4 or 1 or 2 bytes changes how a caller extends it: a break.
        (AbiFundamental(_S, 4, 4), AbiFundamental(_U, 4, 4), False),  # int/unsigned
        (AbiFundamental(_S, 1, 1), AbiFundamental(_U, 1, 1), False),  # char/unsigned char
        (AbiFundamental(_S, 2, 2), AbiFundamental(_U, 2, 2), False),  # short/unsigned
        # A boolean is never an integer, not even an unsigned char.
        (AbiFundamental(_B, 1, 1), AbiFundamental(_U, 1, 1), False),
        # Integer and float of one size are different register classes.
        (AbiFundamental(_S, 4, 4), AbiFundamental(_F, 4, 4), False),  # int/float
        (AbiFundamental(_S, 8, 8), AbiFundamental(_F, 8, 8), False),  # long/double
        (AbiFundamental(_S, 16, 16), AbiFundamental(_F, 16, 16), False),  # __int128/ldouble
        # Different size or alignment: never equal.
        (AbiFundamental(_S, 4, 4), AbiFundamental(_S, 8, 8), False),
        (AbiFundamental(_S, 4, 4), AbiFundamental(_S, 4, 2), False),
        # A vector over 16 bytes is opaque: only itself.
        (AbiFundamental(_F, 32, 32, 8), AbiFundamental(_S, 32, 32, 8), False),
    ],
)
def test_pinned_pairs(old: AbiFundamental, new: AbiFundamental, equal: bool) -> None:
    assert abi_equivalent(old, new) is equal
    assert _oracle(old, new) is equal


@pytest.mark.parametrize("size, align", [(0, 4), (4, 0), (-1, 4)])
def test_non_positive_size_or_align_is_refused(size: int, align: int) -> None:
    with pytest.raises(ValueError):
        AbiFundamental(_S, size, align)


def test_negative_lanes_is_refused() -> None:
    with pytest.raises(ValueError):
        AbiFundamental(_S, 8, 8, -1)
