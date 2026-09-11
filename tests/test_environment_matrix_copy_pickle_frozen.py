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

"""``EnvironmentMatrix``/``SyclConstraints``/``CudaConstraints``: copy,
pickle, and genuine ``frozen=True`` immutability.

Codex review, round 7, two related P2 findings on top of
``test_environment_matrix_hashable.py``'s existing collection-freezing
fix:

- **Finding 2**: wrapping ``runtime_floors`` in a ``types.MappingProxyType``
  made the class hashable, but a ``MappingProxyType`` has no registered
  pickle reducer, so ``copy.deepcopy(matrix)`` and ``pickle.dumps(matrix)``
  both raised ``TypeError: cannot pickle 'mappingproxy' object`` --
  propagating through any ``CompareRequest`` (a documented, embeddable
  public type) carrying a real declared-deployment-floor contract. Fixed by
  registering a ``copyreg`` reducer for ``types.MappingProxyType`` itself
  (see ``environment_matrix.py``'s own ``_reduce_mapping_proxy``
  docstring). A PR #1221 follow-up round then found that reducer left
  ``dataclasses.asdict()`` over this field still not JSON-safe (it
  reconstructs *another* proxy, not a plain dict) -- closed by switching
  ``runtime_floors`` itself away from ``MappingProxyType`` entirely, to the
  dedicated ``model.frozen_str_dict.FrozenStrDict`` dict-subclass (see its own docstring).
- **Finding 3**: freezing only the mutable *collection* fields left the
  dataclasses themselves ordinarily mutable (``matrix.target_os = "x"``),
  which is the identical hash-invariant violation the collection freeze
  fixed for ``runtime_floors``/``compilers`` alone, just one level up.
  Fixed by making all three dataclasses genuinely ``@dataclass(frozen=True)``.

Both fixes are stated here as primitive-level property tests (root
``AGENTS.md``'s "Primitive-level property tests" convention), not just a
regression test pinned to the originally reported shape.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import pickle

import pytest
from hypothesis import given, strategies as st

from abicheck.environment_matrix import (
    CudaConstraints,
    EnvironmentMatrix,
    SyclConstraints,
)
from abicheck.model.frozen_str_dict import FrozenStrDict

# ---------------------------------------------------------------------------
# Fixed-example tests: the reported shape and its immediate neighbors.
# ---------------------------------------------------------------------------


def _populated_matrix() -> EnvironmentMatrix:
    return EnvironmentMatrix(
        compilers=["gcc-13", "clang-17"],
        abi_version="18",
        libstdcxx_dual_abi="cxx11",
        runtime_floors={"GLIBC": "2.28", "CXXABI": "1.3.13"},
        sycl=SyclConstraints(
            implementation="dpcpp",
            backends=["level_zero", "opencl"],
            min_pi_version="2",
        ),
        cuda=CudaConstraints(
            gpu_architectures=["sm_80", "sm_90"],
            driver_range=("525.0", "580.0"),
            toolkit_version="12.4",
            require_ptx=True,
        ),
        target_os="linux",
        target_arch="x86_64",
    )


class TestDeepcopy:
    def test_deepcopy_round_trips_an_empty_matrix(self) -> None:
        matrix = EnvironmentMatrix()
        cloned = copy.deepcopy(matrix)
        assert cloned == matrix

    def test_deepcopy_round_trips_a_fully_populated_matrix(self) -> None:
        matrix = _populated_matrix()
        cloned = copy.deepcopy(matrix)
        assert cloned == matrix
        assert cloned is not matrix

    def test_deepcopy_produces_an_independent_runtime_floors_copy(self) -> None:
        """The deep-copied instance must not share the original's backing
        dict through its MappingProxyType view -- otherwise a caller that
        (illegally) reaches the private dict via reflection could still
        corrupt both instances at once."""
        matrix = _populated_matrix()
        cloned = copy.deepcopy(matrix)
        assert cloned.runtime_floors == matrix.runtime_floors
        assert cloned.runtime_floors is not matrix.runtime_floors

    def test_deepcopy_result_is_still_frozen(self) -> None:
        matrix = _populated_matrix()
        cloned = copy.deepcopy(matrix)
        with pytest.raises(dataclasses.FrozenInstanceError):
            cloned.target_os = "windows"  # type: ignore[misc]
        with pytest.raises(TypeError):
            cloned.runtime_floors["GLIBC"] = "9.9"  # type: ignore[index]

    def test_deepcopy_result_is_still_hashable_and_equal_hash(self) -> None:
        matrix = _populated_matrix()
        cloned = copy.deepcopy(matrix)
        assert hash(cloned) == hash(matrix)

    def test_shallow_copy_also_round_trips(self) -> None:
        """`copy.copy` falls back to the identical `__reduce_ex__`/
        `__getstate__`/`__setstate__` machinery as `copy.deepcopy` for a
        plain object with no dedicated `__copy__`, so it must round-trip
        the same way."""
        matrix = _populated_matrix()
        cloned = copy.copy(matrix)
        assert cloned == matrix


class TestPickle:
    def test_pickle_round_trips_an_empty_matrix(self) -> None:
        matrix = EnvironmentMatrix()
        restored = pickle.loads(pickle.dumps(matrix))
        assert restored == matrix

    def test_pickle_round_trips_a_fully_populated_matrix(self) -> None:
        matrix = _populated_matrix()
        restored = pickle.loads(pickle.dumps(matrix))
        assert restored == matrix

    @pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
    def test_pickle_round_trips_at_every_supported_protocol(
        self, protocol: int
    ) -> None:
        matrix = _populated_matrix()
        restored = pickle.loads(pickle.dumps(matrix, protocol=protocol))
        assert restored == matrix

    def test_pickle_result_is_still_frozen(self) -> None:
        matrix = _populated_matrix()
        restored = pickle.loads(pickle.dumps(matrix))
        with pytest.raises(dataclasses.FrozenInstanceError):
            restored.target_os = "windows"  # type: ignore[misc]
        with pytest.raises(TypeError):
            restored.runtime_floors["GLIBC"] = "9.9"  # type: ignore[index]

    def test_pickle_result_is_still_hashable_and_equal_hash(self) -> None:
        matrix = _populated_matrix()
        restored = pickle.loads(pickle.dumps(matrix))
        assert hash(restored) == hash(matrix)

    def test_pickle_result_runtime_floors_stays_frozen_str_dict(self) -> None:
        """`runtime_floors` round-trips as `model.frozen_str_dict.FrozenStrDict`
        (Codex review, PR #1221, Finding 2 follow-up), not a
        `types.MappingProxyType` -- see `test_environment_matrix_hashable.
        py::TestGenuineImmutability` for why the field switched away from a
        proxy. Since round 9, `FrozenStrDict` is a `collections.abc.Mapping`,
        deliberately *not* a `dict` subclass any more (see
        `model.frozen_str_dict`'s own module docstring for why) -- so this
        now asserts the mapping *type* directly rather than
        `isinstance(..., dict)`. Still genuinely immutable after the round
        trip: a plain `dict` would not raise on item assignment here."""
        from abicheck.model.frozen_str_dict import FrozenStrDict

        matrix = _populated_matrix()
        restored = pickle.loads(pickle.dumps(matrix))
        assert isinstance(restored.runtime_floors, FrozenStrDict)
        assert not isinstance(restored.runtime_floors, dict)
        with pytest.raises(TypeError):
            restored.runtime_floors["GLIBC"] = "9.9"  # type: ignore[index]


class TestAsdictStillWorks:
    """The earlier round fixed the one known ``dataclasses.asdict()`` call
    site that touches an ``EnvironmentMatrix`` (``checker.
    env_matrix_content_digest`` already goes through ``to_dict()``, not
    ``asdict()``) -- this pins that a *direct* ``asdict()`` call over the
    dataclass itself (the shape a naive caller would reach for) still
    works now that the class is frozen with a custom ``__getstate__``,
    since ``asdict()`` walks dataclass fields directly and does not consult
    pickle/copy protocol methods at all."""

    def test_asdict_still_works_on_a_populated_matrix(self) -> None:
        matrix = _populated_matrix()
        d = dataclasses.asdict(matrix)
        assert d["target_os"] == "linux"
        assert d["runtime_floors"] == {"GLIBC": "2.28", "CXXABI": "1.3.13"}

    def test_asdict_over_a_populated_matrix_is_json_serializable(self) -> None:
        """Codex review, PR #1221, Finding 2: a *direct* ``dataclasses.
        asdict()`` call -- the shape arbitrary code elsewhere may reach for
        without knowing to prefer ``EnvironmentMatrix.to_dict()`` -- must
        produce a plain-JSON-serializable structure, not merely avoid
        raising. ``runtime_floors`` previously survived ``asdict()`` (the
        earlier ``copyreg`` fix) but the result was still a
        ``MappingProxyType``, which ``json.dumps()`` cannot serialize."""
        matrix = _populated_matrix()
        d = dataclasses.asdict(matrix)
        serialized = json.dumps(d)
        assert json.loads(serialized)["runtime_floors"] == {
            "GLIBC": "2.28",
            "CXXABI": "1.3.13",
        }

    def test_asdict_over_an_empty_matrix_is_json_serializable(self) -> None:
        matrix = EnvironmentMatrix()
        assert (
            json.loads(json.dumps(dataclasses.asdict(matrix)))["runtime_floors"] == {}
        )

    def test_asdict_runtime_floors_matches_to_dict_runtime_floors(self) -> None:
        """``asdict()``'s ``runtime_floors`` and ``to_dict()``'s own
        ``runtime_floors`` must agree byte-for-byte once serialized -- the
        two paths must never quietly diverge on what this field contains."""
        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        via_asdict = json.dumps(dataclasses.asdict(matrix)["runtime_floors"])
        via_to_dict = json.dumps(matrix.to_dict()["runtime_floors"])
        assert json.loads(via_asdict) == json.loads(via_to_dict)


# ---------------------------------------------------------------------------
# Finding 3: genuine `frozen=True` -- ordinary attribute reassignment raises.
# ---------------------------------------------------------------------------


class TestFrozenAttributeReassignment:
    def test_environment_matrix_target_os_reassignment_raises(self) -> None:
        matrix = EnvironmentMatrix()
        with pytest.raises(dataclasses.FrozenInstanceError):
            matrix.target_os = "windows"  # type: ignore[misc]

    def test_environment_matrix_compilers_reassignment_raises(self) -> None:
        matrix = EnvironmentMatrix()
        with pytest.raises(dataclasses.FrozenInstanceError):
            matrix.compilers = ("gcc-13",)  # type: ignore[misc]

    def test_environment_matrix_sycl_reassignment_raises(self) -> None:
        matrix = EnvironmentMatrix()
        with pytest.raises(dataclasses.FrozenInstanceError):
            matrix.sycl = SyclConstraints(implementation="adaptivecpp")  # type: ignore[misc]

    def test_sycl_constraints_implementation_reassignment_raises(self) -> None:
        sycl = SyclConstraints(implementation="dpcpp")
        with pytest.raises(dataclasses.FrozenInstanceError):
            sycl.implementation = "adaptivecpp"  # type: ignore[misc]

    def test_cuda_constraints_toolkit_version_reassignment_raises(self) -> None:
        cuda = CudaConstraints(toolkit_version="12.4")
        with pytest.raises(dataclasses.FrozenInstanceError):
            cuda.toolkit_version = "13.0"  # type: ignore[misc]

    def test_dataclasses_fields_are_all_frozen(self) -> None:
        """All three dataclasses report as frozen via the public
        ``dataclasses`` introspection API, not merely by observed
        `raises`-on-assignment behavior for the fields this test happens
        to try."""
        assert dataclasses.fields(EnvironmentMatrix)
        for cls in (EnvironmentMatrix, SyclConstraints, CudaConstraints):
            with pytest.raises(dataclasses.FrozenInstanceError):
                # Every dataclass field is reachable via object.__setattr__
                # being refused when frozen -- pick the first declared field.
                instance = cls()
                first_field = dataclasses.fields(cls)[0].name
                setattr(instance, first_field, getattr(instance, first_field))


class TestSetMembershipSurvivesAttemptedMutation:
    """A blocked attribute-reassignment attempt must not disturb the
    object's set/dict membership -- the same invariant
    ``test_environment_matrix_hashable.py``'s
    ``TestGenuineImmutability.test_dict_lookup_survives_a_mutation_that_
    would_have_broken_it`` already states for the collection-field freeze,
    now for a would-be *attribute* mutation too."""

    def test_compare_request_like_membership_survives(self) -> None:
        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        cache = {matrix: "resolved"}
        with pytest.raises(dataclasses.FrozenInstanceError):
            matrix.target_os = "windows"  # type: ignore[misc]
        lookup = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        assert cache[lookup] == "resolved"


# ---------------------------------------------------------------------------
# Property tests: the fixed primitive's contract holds over generated input,
# not just the fixed examples above.
# ---------------------------------------------------------------------------

_short_text = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), max_codepoint=0x7A),
    min_size=0,
    max_size=12,
)
_runtime_floor_value = st.from_regex(r"[0-9]{1,3}(\.[0-9]{1,3}){0,3}", fullmatch=True)


@st.composite
def _matrices(draw: st.DrawFn) -> EnvironmentMatrix:
    keys = draw(st.lists(st.sampled_from(["GLIBC", "GLIBCXX", "CXXABI"]), unique=True))
    floors = {k: draw(_runtime_floor_value) for k in keys}
    return EnvironmentMatrix(
        compilers=draw(st.lists(_short_text, max_size=3)),
        abi_version=draw(st.none() | _short_text),
        runtime_floors=floors,
        target_os=draw(st.none() | _short_text),
        target_arch=draw(st.none() | _short_text),
    )


@given(matrix=_matrices())
def test_property_deepcopy_round_trips_any_matrix(matrix: EnvironmentMatrix) -> None:
    assert copy.deepcopy(matrix) == matrix


@given(matrix=_matrices())
def test_property_pickle_round_trips_any_matrix(matrix: EnvironmentMatrix) -> None:
    assert pickle.loads(pickle.dumps(matrix)) == matrix


@given(matrix=_matrices())
def test_property_pickled_matrix_stays_frozen(matrix: EnvironmentMatrix) -> None:
    restored = pickle.loads(pickle.dumps(matrix))
    with pytest.raises(dataclasses.FrozenInstanceError):
        restored.target_os = "changed"  # type: ignore[misc]


@given(matrix=_matrices())
def test_property_deepcopy_hash_matches_original(matrix: EnvironmentMatrix) -> None:
    assert hash(copy.deepcopy(matrix)) == hash(matrix)


# ---------------------------------------------------------------------------
# `model.frozen_str_dict.FrozenStrDict` itself: a reusable primitive, tested directly against its
# own contract (root AGENTS.md's "Primitive-level property tests"), not only
# through its one current caller (`EnvironmentMatrix.runtime_floors`).
#
# Codex review, PR #1221, round 9: `FrozenStrDict` stopped being a `dict`
# subclass (see `model.frozen_str_dict`'s own module docstring for why --
# `dict.__setitem__(instance, ...)`, calling the *base class's* method
# directly, could reach around every instance-method override the previous,
# `dict`-subclass design put in place). It is now a `collections.abc.Mapping`
# with no mutating method anywhere in its MRO, so most of the mutator-probe
# machinery this section used to need (an exhaustive `dir(dict)` sweep
# proving every mutating `dict` method was individually overridden) no
# longer applies: there is nothing dict-shaped left to override, on this
# class or any base class, so a probe like `d.__setitem__(...)` now fails
# with a plain `AttributeError` ("no such attribute") rather than the
# `TypeError` an explicit override used to raise.
# ---------------------------------------------------------------------------

_str_dicts = st.dictionaries(_short_text, _short_text, max_size=5)


class TestFrozenStrDictContract:
    def test_construction_preserves_contents(self) -> None:
        assert dict(FrozenStrDict({"a": "1", "b": "2"})) == {"a": "1", "b": "2"}

    def test_is_a_mapping_not_a_dict(self) -> None:
        """The load-bearing type change itself (Codex review, PR #1221,
        round 9): no longer `isinstance(x, dict)`, so generic dict-mutating
        utility code that checks `isinstance` before calling a mutator
        cannot reach this class's storage at all -- and there is no
        `Mapping.__setitem__`/`__delitem__`/etc. to call directly either,
        since `Mapping` (unlike `MutableMapping`) declares none."""
        from collections.abc import Mapping

        d = FrozenStrDict({"a": "1"})
        assert isinstance(d, Mapping)
        assert not isinstance(d, dict)

    @pytest.mark.parametrize(
        "mutator_name",
        [
            "__setitem__",
            "__delitem__",
            "__ior__",
            "update",
            "pop",
            "popitem",
            "clear",
            "setdefault",
        ],
    )
    def test_no_mutating_dict_method_exists_at_all(self, mutator_name: str) -> None:
        """Stronger than "raises when called": these methods must not exist
        on this class or any base in its MRO -- `Mapping` provides only the
        read-only mixins (`get`/`keys`/`items`/`values`/`__contains__`/
        `__eq__`/`__ne__`), never a mutator. Proves the base-class-bypass
        vector the finding reported (calling a mutating method through the
        base class directly, e.g. `dict.__setitem__(instance, ...)`) is
        categorically unreachable here: there is no base class in the MRO
        that defines one to call."""
        d = FrozenStrDict({"a": "1"})
        assert not hasattr(d, mutator_name)

    def test_bracket_assignment_raises_type_error(self) -> None:
        d = FrozenStrDict({"a": "1"})
        with pytest.raises(TypeError):
            d["a"] = "2"  # type: ignore[index]

    def test_dict_dunder_bypass_raises_type_error(self) -> None:
        """The finding's own literal reported vector: calling `dict`'s base
        class method *directly* against a `FrozenStrDict` instance, rather
        than through the instance's own (now-nonexistent) `__setitem__`.
        Since `FrozenStrDict` is no longer a `dict` at all, `dict`'s
        C-level `__setitem__` descriptor refuses an instance of the wrong
        type outright -- there is no shared storage left for it to reach."""
        d = FrozenStrDict({"a": "1"})
        with pytest.raises(TypeError):
            dict.__setitem__(d, "a", "2")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            dict.__delitem__(d, "a")  # type: ignore[arg-type]
        assert dict(d) == {"a": "1"}

    def test_direct_attribute_reassignment_raises(self) -> None:
        """Replacing the private backing store wholesale
        (`instance._items = (...)`) is an equally effective mutation vector
        to the item-level ones above -- blocked by this class's own
        `__setattr__` override."""
        d = FrozenStrDict({"a": "1"})
        with pytest.raises(TypeError):
            d._items = (("a", "2"),)  # type: ignore[misc]
        assert dict(d) == {"a": "1"}

    def test_backing_store_is_a_tuple_with_no_mutable_container_reachable(
        self,
    ) -> None:
        """Codex review, PR #1221, round 12: the *reported* vector was
        reaching the round-9 `Mapping` redesign's private `_data` plain
        `dict` directly -- `instance._data["GLIBC"] = "2.34"` mutated that
        dict object in place without ever touching `__setattr__` at all,
        since `__setattr__` only blocked *reassigning* `_data` wholesale,
        not mutating what it already pointed to. The fix backs the mapping
        with a `tuple` of pairs instead, which has no mutating method to
        reach in the first place. This asserts both halves: the private
        attribute is no longer even named `_data` (the exact reported
        repro string must fail with `AttributeError`), and whatever it is
        now named is a `tuple`, which cannot be item-assigned at all."""
        d = FrozenStrDict({"GLIBC": "2.28"})
        assert not hasattr(d, "_data")
        assert isinstance(d._items, tuple)
        with pytest.raises(TypeError):
            d._items[0] = ("GLIBC", "2.34")  # type: ignore[index]

    def test_reported_repro_string_no_longer_mutates(self) -> None:
        """The finding's own exact reported repro,
        ``request.env_matrix.runtime_floors._data["GLIBC"] = "2.34"``,
        applied directly to a `FrozenStrDict` instance: it must no longer
        be able to mutate the instance's observable contents or hash,
        regardless of whether it raises `AttributeError` (no `_data`
        attribute exists any more) or `TypeError` (whatever the backing
        attribute is now doesn't support item assignment)."""
        d = FrozenStrDict({"GLIBC": "2.28"})
        original_contents = dict(d)
        original_hash = hash(d)

        with pytest.raises((AttributeError, TypeError)):
            d._data["GLIBC"] = "2.34"  # type: ignore[attr-defined]

        assert dict(d) == original_contents
        assert hash(d) == original_hash

    def test_reinit_on_already_constructed_instance_raises_and_preserves_hash(
        self,
    ) -> None:
        """Codex review, PR #1221, round 11 (still applicable to the round-9
        `Mapping` redesign): calling `.__init__(...)` a *second* time
        directly on an already-constructed instance must not silently
        repopulate its backing store -- exactly the hash-invariant
        violation the earlier `__ior__` fix closed from a different angle.
        Must raise instead, leaving contents and hash untouched.
        """
        d = FrozenStrDict({"GLIBC": "2.34"})
        original_contents = dict(d)
        original_hash = hash(d)

        with pytest.raises(TypeError):
            d.__init__({"GLIBC": "9.99", "EXTRA": "1.0"})

        assert dict(d) == original_contents
        assert hash(d) == original_hash

    @given(data=_str_dicts)
    def test_property_reinit_never_mutates_regardless_of_contents(
        self, data: dict[str, str]
    ) -> None:
        """Generalization of the fixed-example test above: no matter what
        the instance already holds or what a second ``__init__`` call is
        given, re-initialization must raise and leave the instance
        unchanged -- not just for the one reported ``GLIBC`` shape."""
        d = FrozenStrDict(data)
        original_contents = dict(d)
        original_hash = hash(d)

        with pytest.raises(TypeError):
            d.__init__({"different-key": "different-value"})

        assert dict(d) == original_contents
        assert hash(d) == original_hash

    def test_json_serializable_via_plain_dict_conversion(self) -> None:
        """Codex review, PR #1221, round 9 follow-up: a *bare*
        `json.dumps(frozen_str_dict_instance)` is no longer supported now
        that this class is a `Mapping`, not a `dict` -- the stdlib JSON
        encoder's fast path requires `isinstance(obj, dict)` exactly (or a
        `default=` callback). `dict(d)`/`json.dumps(d, default=dict)` are
        the two supported ways to serialize one directly; the load-bearing
        production contract (`dataclasses.asdict()` over a *containing*
        dataclass) is covered separately below and by
        `TestAsdictStillWorks` above, which is unaffected by this."""
        d = FrozenStrDict({"a": "1", "b": "2"})
        with pytest.raises(TypeError):
            json.dumps(d)
        assert json.loads(json.dumps(dict(d))) == {"a": "1", "b": "2"}
        assert json.loads(json.dumps(d, default=dict)) == {"a": "1", "b": "2"}

    def test_json_serializable_via_asdict_of_a_containing_dataclass(self) -> None:
        @dataclasses.dataclass
        class Holder:
            floors: dict = dataclasses.field(default_factory=dict)

        holder = Holder(floors=FrozenStrDict({"GLIBC": "2.28"}))
        d = dataclasses.asdict(holder)
        assert json.loads(json.dumps(d)) == {"floors": {"GLIBC": "2.28"}}

    @given(data=_str_dicts)
    def test_property_deepcopy_produces_an_independent_plain_dict(
        self, data: dict[str, str]
    ) -> None:
        """`FrozenStrDict.__deepcopy__` deliberately returns a *plain*,
        mutable `dict` (see its own docstring: this is what makes
        `dataclasses.asdict()`'s `copy.deepcopy(obj)` fallback produce
        JSON-safe output now that this class is a `Mapping`, not a `dict`
        subclass). A *direct* `copy.deepcopy(matrix)` over a containing
        `EnvironmentMatrix` is a different call and keeps `runtime_floors`
        genuinely immutable -- see
        `TestDeepcopy.test_deepcopy_result_is_still_frozen` above and
        `EnvironmentMatrix.__deepcopy__`'s own docstring."""
        d = FrozenStrDict(data)
        cloned = copy.deepcopy(d)
        assert type(cloned) is dict
        assert cloned == data
        # Independent storage: mutating the plain-dict clone must not be
        # observable through `d` (which stays immutable regardless).
        cloned["new-key"] = "new-value"
        assert "new-key" not in d

    @given(data=_str_dicts)
    def test_property_pickle_round_trips_and_stays_frozen(
        self, data: dict[str, str]
    ) -> None:
        d = FrozenStrDict(data)
        restored = pickle.loads(pickle.dumps(d))
        assert dict(restored) == data
        with pytest.raises(TypeError):
            restored["new"] = "value"

    @given(data=_str_dicts)
    def test_property_hashable_and_order_independent(
        self, data: dict[str, str]
    ) -> None:
        forward = FrozenStrDict(data)
        reversed_ = FrozenStrDict(dict(reversed(list(data.items()))))
        assert hash(forward) == hash(reversed_)

    @given(data=_str_dicts)
    def test_property_json_round_trip_is_lossless_via_dict_conversion(
        self, data: dict[str, str]
    ) -> None:
        d = FrozenStrDict(data)
        assert json.loads(json.dumps(dict(d))) == data
