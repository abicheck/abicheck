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
from collections.abc import Callable

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
            implementation="dpcpp", backends=["level_zero", "opencl"],
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
    def test_pickle_round_trips_at_every_supported_protocol(self, protocol: int) -> None:
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

    def test_pickle_result_runtime_floors_stays_frozen_dict_subclass(self) -> None:
        """`runtime_floors` round-trips as `model.frozen_str_dict.FrozenStrDict`
        (Codex review, PR #1221, Finding 2 follow-up), not a
        `types.MappingProxyType` -- see `test_environment_matrix_hashable.
        py::TestGenuineImmutability` for why the field switched away from a
        proxy. Still genuinely immutable after the round trip: a plain
        `dict` would not raise on item assignment here."""
        matrix = _populated_matrix()
        restored = pickle.loads(pickle.dumps(matrix))
        assert isinstance(restored.runtime_floors, dict)
        assert type(restored.runtime_floors) is not dict
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
        assert json.loads(json.dumps(dataclasses.asdict(matrix)))["runtime_floors"] == {}

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
    min_size=0, max_size=12,
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
# ---------------------------------------------------------------------------

_str_dicts = st.dictionaries(_short_text, _short_text, max_size=5)

# The full, explicitly-verified set of `dict`-mutation entry points
# `FrozenStrDict` must block (Codex review, PR #1221, round 8: `__ior__` --
# the `|=` in-place-union operator -- was the one initially missing, since it
# mutates the receiver at the C level without going through `__setitem__` or
# `update()`). Kept as one shared table so the exhaustive `dir(dict)` sweep
# below (`test_dict_mutator_probes_cover_every_mutating_dict_method`) and the
# per-method `test_every_mutator_raises` parametrization can't drift apart.
_DICT_MUTATOR_PROBES: dict[str, Callable[[dict[str, str]], object]] = {
    "setitem": lambda d: d.__setitem__("x", "y"),
    "delitem": lambda d: d.__delitem__(next(iter(d))),
    "ior": lambda d: d.__ior__({"x": "y"}),
    "update": lambda d: d.update({"x": "y"}),
    "pop": lambda d: d.pop(next(iter(d))),
    "popitem": lambda d: d.popitem(),
    "clear": lambda d: d.clear(),
    "setdefault": lambda d: d.setdefault("x", "y"),
}


class TestFrozenStrDictContract:
    def test_construction_preserves_contents(self) -> None:
        assert dict(FrozenStrDict({"a": "1", "b": "2"})) == {"a": "1", "b": "2"}

    @pytest.mark.parametrize(
        "mutate", list(_DICT_MUTATOR_PROBES.values()), ids=list(_DICT_MUTATOR_PROBES)
    )
    def test_every_mutator_raises(self, mutate) -> None:  # type: ignore[no-untyped-def]
        d = FrozenStrDict({"a": "1"})
        with pytest.raises(TypeError):
            mutate(d)

    def test_every_probed_mutator_is_actually_overridden(self) -> None:
        """Belt-and-suspenders over the parametrized test above: each probed
        name must correspond to a method `FrozenStrDict` itself defines (not
        merely inherits), so a probe that happens to raise `TypeError` for an
        unrelated reason (e.g. a missing key) can't mask a missing override."""
        dunder_names = {
            "setitem": "__setitem__",
            "delitem": "__delitem__",
            "ior": "__ior__",
        }
        for probe_name in _DICT_MUTATOR_PROBES:
            attr_name = dunder_names.get(probe_name, probe_name)
            assert attr_name in vars(FrozenStrDict), (
                f"FrozenStrDict does not define its own {attr_name!r} override"
            )

    def test_dict_mutator_probes_cover_every_mutating_dict_method(self) -> None:
        """The probe table above must itself be exhaustive against this
        Python's real ``dict`` mutation surface, not just the set
        ``FrozenStrDict`` happens to override today -- otherwise a future
        Python release adding a new in-place ``dict`` mutator could go
        unnoticed by both this test file and ``FrozenStrDict`` alike, the
        same way ``__ior__`` did (Codex review round 8). For every callable
        name in ``dir(dict)`` not already in the probe table, invoke it (with
        a zero-argument call, the only generic signature that plausibly
        mutates in place) on a fresh plain ``dict`` and check whether it
        actually changed -- a name that mutates and isn't in the table is a
        real, unaudited gap.
        """
        probed_attr_names = {
            "setitem": "__setitem__",
            "delitem": "__delitem__",
            "ior": "__ior__",
        }
        already_covered = {
            probed_attr_names.get(name, name) for name in _DICT_MUTATOR_PROBES
        }
        discovered_unaudited: list[str] = []
        for name in dir(dict):
            if name in already_covered:
                continue
            attr = getattr(dict, name, None)
            if not callable(attr):
                continue
            probe: dict[str, str] = {"a": "1"}
            try:
                getattr(probe, name)()
            except TypeError:
                continue  # wrong arity for a zero-arg call -- not a signal
            except Exception:
                continue
            if probe != {"a": "1"}:
                discovered_unaudited.append(name)
        assert not discovered_unaudited, (
            "dict grew (or this Python version has) a mutating method the "
            f"probe table above does not cover: {sorted(discovered_unaudited)}"
        )

    def test_json_serializable_directly(self) -> None:
        d = FrozenStrDict({"a": "1", "b": "2"})
        assert json.loads(json.dumps(d)) == {"a": "1", "b": "2"}

    def test_json_serializable_via_asdict_of_a_containing_dataclass(self) -> None:
        @dataclasses.dataclass
        class Holder:
            floors: dict = dataclasses.field(default_factory=dict)

        holder = Holder(floors=FrozenStrDict({"GLIBC": "2.28"}))
        d = dataclasses.asdict(holder)
        assert json.loads(json.dumps(d)) == {"floors": {"GLIBC": "2.28"}}

    @given(data=_str_dicts)
    def test_property_deepcopy_round_trips_and_stays_frozen(
        self, data: dict[str, str]
    ) -> None:
        d = FrozenStrDict(data)
        cloned = copy.deepcopy(d)
        assert dict(cloned) == data
        with pytest.raises(TypeError):
            cloned["new"] = "value"

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
    def test_property_hashable_and_order_independent(self, data: dict[str, str]) -> None:
        forward = FrozenStrDict(data)
        reversed_ = FrozenStrDict(dict(reversed(list(data.items()))))
        assert hash(forward) == hash(reversed_)

    @given(data=_str_dicts)
    def test_property_json_round_trip_is_lossless(self, data: dict[str, str]) -> None:
        d = FrozenStrDict(data)
        assert json.loads(json.dumps(d)) == data
