"""A declaration in a supplied public header gets a real contract fact, even
when its visibility already bridges to "public".

Bug class: ``public_header_contract_fact`` skipped writing the scope-aware
fact whenever ``in_public_contract`` read confirmed-true. For a record with no
stored fact that "true" is the legacy ``Visibility.PUBLIC`` bridge, which
export narrowing deliberately ignores (``has_observed_contract_evidence``), so
an unexported header function a backend recorded PUBLIC (the native gcc one:
a ``static inline``) fell out of the compared surface. Oracle, over visibility
x stored fact x origin: a real fact is written iff the origin is a public
header and no *producer* fact already confirms membership; the result is then
always observed contract evidence for a public-header declaration.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.model import Function, ScopeOrigin, Visibility
from abicheck.model.fact import Fact
from abicheck.model.surface_facts import (
    has_observed_contract_evidence,
    public_header_contract_fact,
)

_STORED = {
    "none": None,
    "producer_true": Fact.present(True, "backend judgement"),
    "producer_false": Fact.present(False, "backend judgement"),
}


@pytest.mark.parametrize(
    ("vis", "stored", "origin"),
    list(
        itertools.product(
            (Visibility.PUBLIC, Visibility.HIDDEN, Visibility.ELF_ONLY),
            _STORED,
            (
                ScopeOrigin.PUBLIC_HEADER,
                ScopeOrigin.PRIVATE_HEADER,
                ScopeOrigin.UNKNOWN,
            ),
        )
    ),
)
def test_contract_fact_is_written_unless_a_producer_already_confirms_it(
    vis: Visibility, stored: str, origin: ScopeOrigin
) -> None:
    fn = Function(name="hscale", mangled="hscale", return_type="int", visibility=vis)
    fn.in_public_contract_fact = _STORED[stored]
    got = public_header_contract_fact(fn, origin)
    should_write = origin is ScopeOrigin.PUBLIC_HEADER and stored != "producer_true"
    assert (got is not None) is should_write
    if got is not None:
        assert got.value is True
        fn.in_public_contract_fact = got
    if origin is ScopeOrigin.PUBLIC_HEADER:
        assert has_observed_contract_evidence(fn)
