# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""`Fact._make` shares instances only between genuinely equal facts."""

from __future__ import annotations

import itertools

from abicheck.model.availability import FactStatus
from abicheck.model.fact import Fact
from abicheck.storage.fact_codec import decode_fact

_VALUES = [None, False, True, 0, 1, (), (0,), [], "", "x"]
_DIAGS = [(), ("d",)]
_PRODUCERS = [None, "clang", "castxml"]


def _all():
    for status, value, diags, producer in itertools.product(
        list(FactStatus), _VALUES, _DIAGS, _PRODUCERS
    ):
        yield (
            Fact._make(status, value, diags, producer),
            (status, value, diags, producer),
        )


def test_shared_iff_equal_by_independent_oracle():
    """Oracle: two facts may be the same object only when status, value
    *type*, value, diagnostics and producer all agree -- computed here from
    the inputs, not from the implementation's key."""
    made = list(_all()) + list(_all())
    for (a, ka), (b, kb) in itertools.combinations(made, 2):
        same_input = ka == kb and type(ka[1]) is type(kb[1])
        if a is b:
            assert same_input, (ka, kb)
        if not same_input:
            assert a is not b
    # Must-merge: the common shapes really are shared.
    assert Fact.present(False) is Fact.present(False)
    assert Fact.not_collected() is Fact.not_collected()
    assert Fact.present(()) is Fact.present(())
    # Must-not-merge: hash-equal but distinct values, and diagnostics.
    assert Fact.present(False) is not Fact.present(0)
    assert Fact.present(True) is not Fact.present(1)
    assert Fact.not_collected("why") is not Fact.not_collected("why")


def test_every_made_fact_round_trips_fields():
    for fact, (status, value, diags, producer) in _all():
        assert fact.status is status
        assert fact.value == value and type(fact.value) is type(value)
        assert fact.diagnostics == diags and fact.producer == producer


def test_decoder_shares_and_preserves():
    raw = {"status": "present", "value": False}
    a, b = decode_fact(raw, 999, 0), decode_fact(dict(raw), 999, 0)
    assert a is b and a == Fact.present(False)
    c = decode_fact({"status": "present", "value": 0}, 999, 0)
    assert c is not a and c.value == 0 and type(c.value) is int
