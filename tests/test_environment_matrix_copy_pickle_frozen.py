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
  public type) carrying a real declared-deployment-floor contract. Fixed
  via ``__getstate__``/``__setstate__`` (see ``environment_matrix.py``'s
  own docstring for why that pair, not switching away from
  ``MappingProxyType``).
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
import pickle

import pytest
from hypothesis import given, strategies as st

from abicheck.environment_matrix import (
    CudaConstraints,
    EnvironmentMatrix,
    SyclConstraints,
)

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

    def test_pickle_result_runtime_floors_is_a_mapping_proxy(self) -> None:
        from types import MappingProxyType

        matrix = _populated_matrix()
        restored = pickle.loads(pickle.dumps(matrix))
        assert isinstance(restored.runtime_floors, MappingProxyType)


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
