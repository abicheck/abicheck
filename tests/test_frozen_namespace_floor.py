"""The frozen-namespace floor, now one helper the policy resolvers share.

Seven branches restated "a frozen-namespace finding may not be relaxed below
its floor"; any one of them drifting would let one resolver relax what the
others keep. Exhaustive over verdict pairs and the violation flag; the
oracle is the rule written out here with the literal order.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest

from abicheck.change_registry_types import Verdict
from abicheck.policy.reclassify import _frozen_floor

ORDER = ["NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK", "API_BREAK", "BREAKING"]


@pytest.mark.parametrize("tag", [None, "", "v1", 3])
def test_floor_applies_only_to_a_real_tag_and_only_upwards(tag: object) -> None:
    change = SimpleNamespace(frozen_namespace_violation=tag)
    frozen = isinstance(tag, str) and bool(tag)
    for req, floor in itertools.product(Verdict, repeat=2):
        expected = floor if frozen and ORDER.index(req) < ORDER.index(floor) else req
        assert _frozen_floor(change, req, floor) is expected
