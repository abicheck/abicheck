# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""ADR-063 2B: ``compare.record_lookup.RecordLookup`` and its consumers.

``RecordLookup`` replaced three ``{t.name: t}`` dicts (build-context
reconciliation, stdlib-embedding attribution) whose answer for a shared leaf
name was "whichever record was listed last". Its contract, stated as
invariants over every listing order of a small exhaustive domain rather than
one fixture:

* **order independence** -- the answer never depends on list order;
* **no guess under ambiguity** -- a bare name shared by two records answers
  ``None`` unless the finding's own ``entity_id`` names one of them;
* **identity wins** -- a matching ``entity_id`` is answered exactly;
* **unambiguous names still resolve** -- the lookup is not a stricter no-op.

The type-spelling pass (``diff_type_spellings``) is checked the same way:
its field-slot findings for two same-leaf records are identical in every
listing order on either side, and a real change in one of them is reported
against that one only.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.compare.record_lookup import RecordLookup
from abicheck.diff_type_spellings import iter_type_slot_changes
from abicheck.model import AbiSnapshot, RecordType, TypeField
from abicheck.model.identity import Namespace, entity_id_for_type


def _rec(ns: str | None, name: str, field_type: str = "int") -> RecordType:
    return RecordType(
        name=name,
        qualified_name=f"{ns}::{name}" if ns else None,
        kind="struct",
        size_bits=32,
        fields=[TypeField(name="f", type=field_type, offset_bits=0)],
        entity_id=entity_id_for_type((Namespace(ns),) if ns else (), name),
    )


DOMAIN = [_rec("ns1", "S"), _rec("ns2", "S"), _rec(None, "T"), _rec("ns3", "U")]


@pytest.mark.parametrize("order", list(itertools.permutations(range(len(DOMAIN)))))
def test_answers_are_order_independent_and_never_guess(order):
    lookup = RecordLookup([DOMAIN[i] for i in order])
    # Ambiguous bare name, no identity: no answer, in every order.
    assert lookup.resolve("S") is None
    # Identity picks the exact record despite the shared name.
    for rec in DOMAIN[:2]:
        assert lookup.resolve("S", rec.entity_id) is rec
    # Unambiguous bare names still resolve.
    assert lookup.resolve("T") is DOMAIN[2]
    assert lookup.resolve("U") is DOMAIN[3]
    # A foreign identity falls back to the (ambiguous -> None) name tier.
    assert lookup.resolve("S", entity_id_for_type((Namespace("zz"),), "S")) is None
    assert lookup.resolve("missing") is None


def _snap(records):
    return AbiSnapshot(library="l.so", version="1", types=list(records))


def _slots(old, new):
    return sorted(
        (c.symbol, c.slot, c.old_type, c.new_type)
        for c in iter_type_slot_changes(_snap(old), _snap(new))
        if c.slot.startswith("field")
    )


@pytest.mark.parametrize("old_order", list(itertools.permutations(range(2))))
@pytest.mark.parametrize("new_order", list(itertools.permutations(range(2))))
def test_type_spelling_pass_pairs_same_leaf_records_by_identity(old_order, new_order):
    old = [_rec("ns1", "S", "int"), _rec("ns2", "S", "long")]
    new_same = [_rec("ns1", "S", "int"), _rec("ns2", "S", "long")]
    assert _slots([old[i] for i in old_order], [new_same[i] for i in new_order]) == []
    new_changed = [_rec("ns1", "S", "int"), _rec("ns2", "S", "long long")]
    got = _slots([old[i] for i in old_order], [new_changed[i] for i in new_order])
    assert len(got) == 1 and got[0][2:] == ("long", "long long"), got


# -- typedef consumers (diff_integer_model) ------------------------------------


def _td_snap(bare: dict[str, str], qualified: dict[str, str]) -> AbiSnapshot:
    return AbiSnapshot(
        library="l.so", version="1", typedefs=bare, typedefs_qualified=qualified
    )


@pytest.mark.parametrize(
    ("old_bare", "new_bare"),
    [
        ({"my_int32_t": "int"}, {"my_int32_t": "long long"}),
        ({"my_int32_t": "long long"}, {"my_int32_t": "int"}),
    ],
    ids=["collapse-on-new", "collapse-on-old"],
)
def test_integer_typedef_scan_uses_the_qualified_maps(old_bare, new_bare):
    """Each side's bare map collapsed a different one of two same-leaf
    typedefs onto ``my_int32_t``; nothing actually changed, so no flip."""
    from abicheck.diff_integer_model import _scan_typedef_integer_flips

    qualified = {"my_int32_t": "int", "A::my_int32_t": "long long"}
    flips, up, down = _scan_typedef_integer_flips(
        _td_snap(old_bare, qualified), _td_snap(new_bare, dict(qualified)), False
    )
    assert (flips, up, down) == ([], 0, 0)


def test_integer_typedef_scan_still_reports_a_real_flip():
    from abicheck.diff_integer_model import _scan_typedef_integer_flips

    old = _td_snap({"my_int32_t": "int"}, {"my_int32_t": "int", "A::my_int32_t": "int"})
    new = _td_snap(
        {"my_int32_t": "int"}, {"my_int32_t": "int", "A::my_int32_t": "long long"}
    )
    flips, up, down = _scan_typedef_integer_flips(old, new, False)
    assert (len(flips), up, down) == (1, 1, 0) and flips[0].startswith("A::my_int32_t")
