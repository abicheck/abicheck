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

"""The six hot model types carry no per-instance ``__dict__``.

This is a *memory* contract, and it needs its own assertions because nothing
else in the suite can fail when it is broken. Delete ``slots=True`` from any
of these types and every equality, round-trip and detector test still passes
-- the objects behave identically, they just cost three to four times more
resident bytes each. That is the same shape as the defect
``perf.shared_projection_read_as_an_owned_copy`` records: a cost that returns
invisibly because correctness assertions cannot see it.

So the invariant is stated structurally (no ``__dict__``), paired with the
behavioural properties slotting is most likely to break silently -- the
``__post_init__`` fact bridging, ``copy``/``deepcopy`` semantics the
``--depth`` projection depends on, and encode-path equivalence.
"""

from __future__ import annotations

import copy
import dataclasses
import json

import pytest

from abicheck.model.declarations import Fact, Function, Param, Variable
from abicheck.model.entities import EnumType, RecordType

#: Every type whose instance count scales with the size of a parsed library.
#: A type added here without ``slots=True`` fails, which is the point: the
#: next wide declaration dataclass should not quietly ship with a ``__dict__``.
SLOTTED_TYPES = (Function, Param, Variable, RecordType, EnumType, Fact)


def _make(cls):
    """A minimally-valid instance of each slotted type."""
    if cls is Function:
        return Function(name="f", mangled="_Z1fv", return_type="void")
    if cls is Param:
        return Param(name="a", type="int")
    if cls is Variable:
        return Variable(name="v", mangled="_Z1v", type="int")
    if cls is RecordType:
        return RecordType(name="S", kind="struct")
    if cls is EnumType:
        return EnumType(name="E")
    if cls is Fact:
        return Fact.unsupported()
    raise AssertionError(f"no constructor for {cls!r}")


class TestNoPerInstanceDict:
    """The structural half: these instances have no ``__dict__`` at all."""

    @pytest.mark.parametrize("cls", SLOTTED_TYPES, ids=lambda c: c.__name__)
    def test_instances_carry_no_dict(self, cls) -> None:
        obj = _make(cls)
        assert not hasattr(obj, "__dict__"), (
            f"{cls.__name__} regained a per-instance __dict__. That is a pure "
            f"memory regression: every behavioural test still passes, and each "
            f"instance costs several hundred bytes more."
        )

    @pytest.mark.parametrize("cls", SLOTTED_TYPES, ids=lambda c: c.__name__)
    def test_class_declares_slots(self, cls) -> None:
        """`__slots__` must be on the class itself, not merely inherited.

        A subclass of a slotted base that omits its own `__slots__` gets a
        `__dict__` back; asserting only `not hasattr(obj, "__dict__")` on the
        base would not catch that for a future subclass.
        """
        assert "__slots__" in cls.__dict__, f"{cls.__name__} declares no __slots__"

    @pytest.mark.parametrize("cls", SLOTTED_TYPES, ids=lambda c: c.__name__)
    def test_undeclared_attributes_are_rejected(self, cls) -> None:
        """The observable consequence, asserted rather than implied.

        This is what makes the contract enforceable at runtime: a caller that
        starts stashing state on one of these objects fails loudly here
        instead of silently restoring the `__dict__`.

        The *exception type* is deliberately not pinned. A plain slotted
        dataclass raises `AttributeError`, but a `frozen=True, slots=True`
        one (`Fact`) raises `TypeError` from its generated `__setattr__`:
        `slots=True` builds a new class object while the frozen
        `__setattr__` closure still refers to the original in its `super()`
        call, so the undeclared-attribute path fails inside CPython's own
        machinery rather than at the slot check. That is a CPython
        implementation detail, it differs by version, and pinning it here
        would make this test a tripwire for the interpreter rather than for
        this repository. What matters -- and what is asserted -- is that the
        write is refused rather than silently restoring a `__dict__`.
        """
        obj = _make(cls)
        with pytest.raises((AttributeError, TypeError)):
            obj._some_attribute_no_field_declares = 1  # type: ignore[attr-defined]
        assert not hasattr(obj, "_some_attribute_no_field_declares")
        assert not hasattr(obj, "__dict__")

    def test_a_frozen_slotted_type_still_refuses_a_declared_field(self) -> None:
        """`Fact` is the one frozen member, and its *documented* guarantee --
        assignment to a real field raises `FrozenInstanceError` -- is
        unaffected by slotting. Asserted separately from the undeclared-name
        path above, because only that path changed shape."""
        fact = Fact.unsupported()
        with pytest.raises(dataclasses.FrozenInstanceError):
            fact.status = "mutated"  # type: ignore[misc]

    def test_a_frozen_slotted_type_still_supports_replace(self) -> None:
        """`dataclasses.replace` goes through `__init__`, not `__setattr__`,
        so it is unaffected -- but it is the supported way to derive a
        modified `Fact`, so it gets an assertion rather than an assumption."""
        fact = Fact.unsupported()
        assert dataclasses.replace(fact, status=fact.status) == fact

    @pytest.mark.parametrize("cls", SLOTTED_TYPES, ids=lambda c: c.__name__)
    def test_every_declared_field_is_still_settable(self, cls) -> None:
        """Non-vacuity guard for the test above.

        A class with `__slots__ = ()` and no fields would pass every
        assertion here while being useless, so each declared field must still
        round-trip through a real read and write.
        """
        obj = _make(cls)
        fields = dataclasses.fields(cls)
        assert fields, f"{cls.__name__} has no fields; the sweep would be vacuous"
        for f in fields:
            value = getattr(obj, f.name)
            if f.name in getattr(cls, "__slots__", ()):  # frozen types reject writes
                try:
                    setattr(obj, f.name, value)
                except dataclasses.FrozenInstanceError:
                    pass
            assert getattr(obj, f.name) is value or getattr(obj, f.name) == value


class TestSlottingDidNotChangeBehaviour:
    """The behavioural half: what slotting is most likely to break quietly."""

    def test_post_init_fact_bridging_still_runs(self) -> None:
        """`__post_init__` writes declared fields; slots must not block it."""
        p = Param(name="a", type="int")
        assert p.kind_fact is not None
        assert p.is_va_list_fact is not None
        assert p.is_restrict_fact is not None

    @pytest.mark.parametrize("cls", SLOTTED_TYPES, ids=lambda c: c.__name__)
    def test_copy_and_deepcopy_still_work(self, cls) -> None:
        """`copy.copy` uses the slots reducer rather than a `__dict__` copy.

        The `--depth` projection depends on `copy.copy` carrying a whole
        instance without re-running `__init__` (see
        `tests/test_depth_projection_ownership.py`), so both copy paths must
        keep working on every slotted type.
        """
        obj = _make(cls)
        shallow = copy.copy(obj)
        deep = copy.deepcopy(obj)
        for clone in (shallow, deep):
            assert clone is not obj
            assert type(clone) is cls
            for f in dataclasses.fields(cls):
                assert getattr(clone, f.name) == getattr(obj, f.name)

    def test_a_deep_copy_owns_its_mutable_members(self) -> None:
        """Slots must not silently turn a deep copy into a shared one."""
        fn = Function(
            name="f",
            mangled="_Z1fv",
            return_type="void",
            params=[Param(name="a", type="int")],
        )
        clone = copy.deepcopy(fn)
        assert clone.params is not fn.params
        assert clone.params[0] is not fn.params[0]

    def test_dataclasses_fields_are_unchanged(self) -> None:
        """Field count is part of the encode contract, not an implementation
        detail: the codecs iterate declared fields."""
        # 49: ADR-075 D2 added `ownership_fact` (persisted as a side table,
        # stripped from the per-entity dict by storage/extraction_scope_codec).
        assert len(dataclasses.fields(Function)) == 49
        assert len(dataclasses.fields(Param)) == 10
        assert len(dataclasses.fields(Fact)) == 4


class TestEncodePathEquivalence:
    """A snapshot built from slotted types still encodes."""

    def test_a_snapshot_of_slotted_types_round_trips(self) -> None:
        from abicheck.model.snapshot import AbiSnapshot
        from abicheck.storage.snapshot_codec import snapshot_to_dict

        snap = AbiSnapshot(library="libx.so", version="1.0")
        snap.functions = [
            Function(
                name=f"f{i}",
                mangled=f"_Z1f{i}v",
                return_type="void",
                params=[Param(name="a", type="int"), Param(name="b", type="char*")],
            )
            for i in range(25)
        ]
        snap.types = [RecordType(name=f"S{i}", kind="struct") for i in range(10)]
        snap.enums = [EnumType(name="E")]

        encoded = snapshot_to_dict(snap)
        # The encoder reads declared fields, not `__dict__` -- which is why
        # removing the instance dict leaves the stored bytes untouched.
        assert len(encoded["functions"]) == 25
        assert len(encoded["functions"][0]["params"]) == 2
        assert len(encoded["types"]) == 10
        # Serialisable end to end, not merely dict-shaped.
        json.dumps(encoded, sort_keys=True, default=str)
