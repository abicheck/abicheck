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

"""``SymbolSignatureStatus`` has four inhabitants, so four objects suffice.

The type is frozen, slotted, and carries two booleans. Its value space is
therefore *exactly* four members, yet the producer allocated one object per
symbol -- 48 bytes against 87,728 symbols is 4.02 MiB for one real oneDAL
library, and the release fan-out retains a mapping per matched member.

Two things have to hold for sharing those four objects to be a memory
change rather than a behaviour change, and both are asserted here rather
than argued:

* **the value contract is untouched** -- a shared status equals, hashes
  like, and reads like a directly-constructed one, for *every* point in
  the domain; and
* **the allocation bound actually binds** -- the number of distinct status
  objects a real ``symbol_signature_statuses`` run produces does not grow
  with the number of symbols.

The domain is enumerated exhaustively rather than sampled, which AGENTS.md
allows precisely when the domain is small enough to exhaust: two booleans
is four cases, so "several independently-chosen sibling cases" would be a
strictly weaker statement than the complete one.

The bound is asserted on **object identity counted through the real
producer**, not on the factory in isolation. A factory that interns
correctly while the producer keeps calling the constructor directly would
pass every factory-only test and save nothing -- that gap is the whole
failure this file exists to foreclose, and
``test_the_real_producer_allocates_no_new_status`` is what closes it.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.bundle_models import (
    SymbolSignatureStatus,
    symbol_signature_status,
)
from abicheck.bundle_signature_evidence import symbol_signature_statuses
from abicheck.model import AbiSnapshot, Function, Variable, Visibility

#: The complete domain. Named so a failure says which point disagreed.
DOMAIN = list(itertools.product([False, True], repeat=2))


def test_the_domain_is_the_whole_value_space() -> None:
    """Vacuity guard for every enumeration below.

    Each test in this file sweeps ``DOMAIN``. If ``DOMAIN`` were ever
    reduced -- to one entry, or to none -- those sweeps would keep passing
    while asserting almost nothing, which is the "matrix test with no
    oracle" failure AGENTS.md names. Pinning the count here means the
    sweeps cannot quietly stop covering the type.
    """
    assert len(DOMAIN) == 4
    assert len(set(DOMAIN)) == 4


class TestTheValueContractIsUnchanged:
    """Sharing must be invisible to every reader of a status."""

    @pytest.mark.parametrize(("exported", "sufficient"), DOMAIN)
    def test_shared_equals_directly_constructed(
        self, exported: bool, sufficient: bool
    ) -> None:
        """The oracle is the constructor itself, which sharing must match.

        Deliberately not a restatement of the field values: comparing
        against ``SymbolSignatureStatus(...)`` is what makes this a test of
        the *substitution*, so a factory that returned a correct-looking
        object with a swapped field still fails.
        """
        direct = SymbolSignatureStatus(
            exported=exported, evidence_sufficient=sufficient
        )
        shared = symbol_signature_status(
            exported=exported, evidence_sufficient=sufficient
        )
        assert shared == direct
        assert hash(shared) == hash(direct)
        assert shared.exported is direct.exported
        assert shared.evidence_sufficient is direct.evidence_sufficient

    @pytest.mark.parametrize(("exported", "sufficient"), DOMAIN)
    def test_repeated_calls_share_one_object(
        self, exported: bool, sufficient: bool
    ) -> None:
        """Same inputs, same object -- the saving, stated directly."""
        first = symbol_signature_status(
            exported=exported, evidence_sufficient=sufficient
        )
        second = symbol_signature_status(
            exported=exported, evidence_sufficient=sufficient
        )
        assert first is second

    def test_distinct_inputs_stay_distinct(self) -> None:
        """Interning must not collapse two genuinely different answers.

        The mirror image of the sharing property, and the one that would
        actually corrupt a result: a cache keyed on too little (say, on
        ``exported`` alone) still passes every sharing assertion above
        while silently answering the second question wrongly.
        """
        objects = {
            (e, s): symbol_signature_status(exported=e, evidence_sufficient=s)
            for e, s in DOMAIN
        }
        assert len({id(o) for o in objects.values()}) == len(DOMAIN)
        for (e, s), obj in objects.items():
            assert obj.exported is e
            assert obj.evidence_sufficient is s

    @pytest.mark.parametrize(("exported", "sufficient"), DOMAIN)
    def test_an_int_input_needs_no_coercion_to_resolve(
        self, exported: bool, sufficient: bool
    ) -> None:
        """``0``/``1`` resolve whether or not the factory coerces.

        Recorded as its own case precisely because it proves *nothing*
        about the coercion: ``1 == True`` and ``hash(1) == hash(True)``, so
        an ``int`` key finds the shared value by ordinary dict lookup even
        with ``bool(...)`` removed. The first version of this file tested
        only this input and called it a coercion test -- a mutation that
        deleted the coercion passed all 24 tests. Kept as a documented
        boundary, with the real requirement in the sibling test below.
        """
        shared = symbol_signature_status(
            exported=int(exported), evidence_sufficient=int(sufficient)
        )
        assert shared is symbol_signature_status(
            exported=exported, evidence_sufficient=sufficient
        )

    @pytest.mark.parametrize(
        "truthy",
        ["yes", 2, 0.5, (0,), object()],
        ids=["str", "int_not_one", "float", "tuple", "object"],
    )
    @pytest.mark.parametrize(
        "falsy", ["", 0.0, (), None], ids=["str", "float", "tuple", "none"]
    )
    def test_a_non_bool_that_is_not_hash_equal_is_still_normalized(
        self, truthy: object, falsy: object
    ) -> None:
        """The coercion's actual job, on inputs that can detect it.

        These values are truthy/falsy but **not** hash-equal to ``True``/
        ``False``, so they miss the shared table entirely unless the
        factory coerces: without ``bool(...)`` this raises ``KeyError``.
        That is the mutation the ``int`` case above cannot catch.

        It also states why coercing is right rather than merely
        convenient. The fields are annotated ``bool``, and the bare
        constructor stored such a value verbatim -- ``SymbolSignature
        Status(exported="yes")`` compares **unequal** to its own boolean
        twin, so two symbols with the same answer would read as different.
        Normalizing is what makes a shared value genuinely
        interchangeable with a directly-constructed one.

        Swept across several unrelated types rather than one, since the
        requirement is about truthiness, not about ``str``.
        """
        shared = symbol_signature_status(exported=truthy, evidence_sufficient=falsy)
        assert shared is symbol_signature_status(
            exported=True, evidence_sufficient=False
        )
        assert shared.exported is True
        assert shared.evidence_sufficient is False

    def test_an_unhashable_input_is_normalized_rather_than_raising(self) -> None:
        """A truthy/falsy *unhashable* value is the sharpest case.

        A ``list`` cannot be a dict key at all, so an uncoerced lookup
        raises ``TypeError: unhashable type`` rather than ``KeyError`` --
        a different failure mode from the sweep above, and the one a
        predicate returning an accumulated list would actually produce.
        """
        shared = symbol_signature_status(exported=[1], evidence_sufficient=[])
        assert shared is symbol_signature_status(
            exported=True, evidence_sufficient=False
        )

    @pytest.mark.parametrize(("exported", "sufficient"), DOMAIN)
    def test_a_shared_status_is_still_immutable(
        self, exported: bool, sufficient: bool
    ) -> None:
        """Frozenness is what makes sharing safe, so it is asserted.

        A shared object reached by many callers is only sound while none
        can write to it. If ``SymbolSignatureStatus`` ever lost
        ``frozen=True``, sharing would turn one member's answer into
        every member's answer -- a correctness bug, not a memory one.
        """
        status = symbol_signature_status(
            exported=exported, evidence_sufficient=sufficient
        )
        with pytest.raises(Exception):  # noqa: B017,PT011 - see below
            status.exported = not exported  # type: ignore[misc]
        # Deliberately broad: a frozen dataclass raises
        # `FrozenInstanceError`, but a frozen **slotted** one can raise a
        # bare `TypeError` instead, because `slots=True` rebuilds the class
        # while the generated `__setattr__` closed over the original. The
        # claim is "the write does not land", so it is asserted directly
        # rather than through whichever exception type CPython picks.
        assert status.exported is exported


def _snapshot_with(symbol_count: int) -> AbiSnapshot:
    """A snapshot whose symbols span several distinguishable states.

    The states are varied on purpose: a snapshot whose symbols all resolve
    to one status would satisfy an allocation bound of *one* and so could
    not detect a cache that over-shares.
    """
    functions: list[Function] = []
    variables: list[Variable] = []
    visibilities = [Visibility.PUBLIC, Visibility.HIDDEN, Visibility.ELF_ONLY]
    for index in range(symbol_count):
        visibility = visibilities[index % len(visibilities)]
        if index % 4 == 3:
            variables.append(
                Variable(
                    name=f"var_{index}",
                    mangled=f"var_{index}",
                    # Every 8th gets an unresolved spelling, so
                    # `evidence_sufficient` varies independently of
                    # `exported` rather than tracking it.
                    type="__unresolved_type" if index % 8 == 3 else "int",
                    visibility=visibility,
                )
            )
        else:
            functions.append(
                Function(
                    name=f"fn_{index}",
                    mangled=f"fn_{index}",
                    return_type="__unresolved_type" if index % 8 == 0 else "int",
                    params=[],
                    visibility=visibility,
                    is_variadic=None if index % 8 == 1 else False,
                    contract_attributes=[],
                )
            )
    return AbiSnapshot(
        library="libx.so", version="1.0", functions=functions, variables=variables
    )


class TestTheAllocationBoundBinds:
    """The saving is a *bound*, so it is measured as one."""

    @pytest.mark.parametrize("symbol_count", [16, 256, 2048])
    def test_the_real_producer_allocates_no_new_status(self, symbol_count: int) -> None:
        """Distinct status objects stay bounded as symbols grow.

        This is the invariant the memory claim rests on, asserted through
        the real ``symbol_signature_statuses`` rather than the factory:
        the producer is where a regression would land, since reverting its
        one call site to the constructor restores per-symbol allocation
        while leaving every factory-only test green.

        Growth is swept rather than fixed because the property is about
        the *relationship* between symbol count and object count. A single
        size cannot distinguish "bounded" from "happens to be small here".
        """
        statuses = symbol_signature_statuses(_snapshot_with(symbol_count))
        assert len(statuses) == symbol_count, "fixture did not produce one per symbol"
        distinct = {id(status) for status in statuses.values()}
        assert len(distinct) <= len(DOMAIN)

    def test_the_producer_returns_the_shared_objects_not_copies(self) -> None:
        """Every produced status *is* the module's own shared instance.

        A bound of four is also satisfied by a producer that built its own
        four objects per call -- which would still allocate per member and
        save nothing across the fan-out. Identity against the shared table
        is what rules that out.
        """
        statuses = symbol_signature_statuses(_snapshot_with(256))
        shared = {
            id(symbol_signature_status(exported=e, evidence_sufficient=s))
            for e, s in DOMAIN
        }
        assert {id(status) for status in statuses.values()} <= shared

    def test_two_runs_share_across_calls(self) -> None:
        """Separate producer runs share, which is the fan-out's saving.

        The release fan-out builds one mapping per matched member, so the
        cost this change removes is per *member*, not per symbol within
        one member. A per-call cache would leave that untouched.
        """
        first = symbol_signature_statuses(_snapshot_with(64))
        second = symbol_signature_statuses(_snapshot_with(64))
        assert {id(s) for s in first.values()} == {id(s) for s in second.values()}

    def test_the_fixture_actually_spans_several_statuses(self) -> None:
        """Vacuity guard on the bound itself.

        ``<= 4`` is trivially true of a fixture that produces one status
        for every symbol, and such a fixture would let a badly
        over-sharing cache (one that ignored its key entirely) pass every
        assertion above. Requiring real variety is what gives the bound
        something to bind.
        """
        statuses = symbol_signature_statuses(_snapshot_with(256))
        assert len({id(status) for status in statuses.values()}) > 1
