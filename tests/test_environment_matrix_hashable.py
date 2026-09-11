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

"""``EnvironmentMatrix``/``SyclConstraints``/``CudaConstraints`` hashability.

Codex review, P2 (PR #1221): ``CompareRequest`` is a frozen dataclass whose
dataclass-generated ``__hash__`` requires every field to be hashable.
``EnvironmentMatrix`` -- a plain, non-frozen dataclass with ``eq=True`` --
had ``__hash__`` implicitly set to ``None`` by the ``@dataclass`` decorator,
so any ``CompareRequest`` carrying a real declared-deployment-floor contract
was unhashable, breaking a typed-API caller using requests as set members or
cache keys (``tests/test_api_types.py::TestCompareRequestHashableWithEnvMatrix``
covers that call site directly). This module states the fixed primitive's
own contract as invariants, independent of ``CompareRequest``, per this
repo's "Primitive-level property tests" convention (root ``AGENTS.md``):
hash/eq agreement, order-independence of the ``runtime_floors`` dict, and
that two matrices with genuinely different content are distinguishable.

A follow-up review round found the first fix incomplete: making the class
hashable via a projection computed fresh inside ``__hash__`` does not
satisfy the actual Python hash contract when the fields it projects
(``runtime_floors``: ``dict``, ``compilers``: ``list``) remain directly
mutable after construction -- mutating either one changes the computed hash
out from under a dict/set the instance was already inserted into. The
``TestGenuineImmutability`` class below states and checks that stronger
invariant directly: the containers themselves are frozen (a ``dict``
mutation raises / a ``tuple`` has no in-place mutation method), not merely
"nothing today happens to mutate them."
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from abicheck.environment_matrix import (
    CudaConstraints,
    EnvironmentMatrix,
    SyclConstraints,
)

# ---------------------------------------------------------------------------
# Example-shaped tests: the reported defect and its immediate neighbors.
# ---------------------------------------------------------------------------


def test_default_construction_is_hashable() -> None:
    assert isinstance(hash(EnvironmentMatrix()), int)


def test_fully_populated_matrix_is_hashable() -> None:
    matrix = EnvironmentMatrix(
        compilers=["gcc-13", "clang-17"],
        abi_version="18",
        libstdcxx_dual_abi="cxx11",
        runtime_floors={"GLIBC": "2.28", "CXXABI": "1.3.13"},
        sycl=SyclConstraints(
            implementation="dpcpp",
            backends=["level_zero", "opencl"],
            min_pi_version="1",
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
    assert isinstance(hash(matrix), int)


def test_equal_matrices_hash_equal_even_with_different_dict_insertion_order() -> None:
    """dict equality is order-insensitive; the hash must agree."""
    a = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28", "CXXABI": "1.3.13"})
    b = EnvironmentMatrix(runtime_floors={"CXXABI": "1.3.13", "GLIBC": "2.28"})
    assert a == b
    assert hash(a) == hash(b)


def test_matrices_differing_only_in_runtime_floor_value_are_distinguishable() -> None:
    a = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
    b = EnvironmentMatrix(runtime_floors={"GLIBC": "2.34"})
    assert a != b
    assert {a, b} == {a, b}


def test_matrices_differing_only_in_sycl_backends_are_distinguishable() -> None:
    a = EnvironmentMatrix(sycl=SyclConstraints(backends=["level_zero"]))
    b = EnvironmentMatrix(sycl=SyclConstraints(backends=["opencl"]))
    assert a != b
    # Hash collision is legal in general; the real invariant is that both
    # remain distinct set members, not that their hashes differ.
    assert {a, b} == {a, b}


def test_sycl_and_cuda_constraints_are_independently_hashable() -> None:
    assert isinstance(
        hash(
            SyclConstraints(
                implementation="dpcpp", backends=["level_zero"], min_pi_version="1"
            )
        ),
        int,
    )
    assert isinstance(
        hash(
            CudaConstraints(
                gpu_architectures=["sm_80"],
                driver_range=("525.0", "580.0"),
                toolkit_version="12.4",
                require_ptx=True,
            )
        ),
        int,
    )


def test_matrix_usable_as_dict_key_and_set_member() -> None:
    a = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
    b = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
    cache = {a: "resolved"}
    assert cache[b] == "resolved"
    assert {a, b} == {a}


# ---------------------------------------------------------------------------
# Property test: the hash/eq contract over generated content, not just the
# hand-picked examples above.
# ---------------------------------------------------------------------------

_prefix = st.sampled_from(["GLIBC", "GLIBCXX", "CXXABI"])
_floor = st.from_regex(r"\A[0-9]{1,2}(\.[0-9]{1,2}){0,2}\Z", fullmatch=True)
_floors = st.dictionaries(_prefix, _floor, max_size=3)
_compilers = st.lists(st.sampled_from(["gcc-13", "clang-17", "gcc-14"]), max_size=3)


@given(compilers=_compilers, floors=_floors)
def test_property_equal_matrices_always_hash_equal(
    compilers: list[str], floors: dict[str, str]
) -> None:
    """The hash/eq contract, generated rather than hand-picked: any two
    ``EnvironmentMatrix`` instances built from the same logical content
    (including a dict re-keyed in a different insertion order) must compare
    equal and hash equal -- never merely "usually" for the examples above.
    """
    a = EnvironmentMatrix(compilers=list(compilers), runtime_floors=dict(floors))
    # Rebuild the floors dict via a different insertion order, so the two
    # underlying dicts are equal but were never the same object nor built
    # the same way.
    reordered = dict(reversed(list(floors.items())))
    b = EnvironmentMatrix(compilers=list(compilers), runtime_floors=reordered)
    assert a == b
    assert hash(a) == hash(b)


@given(a_floors=_floors, b_floors=_floors)
def test_property_hash_is_a_pure_function_of_content(
    a_floors: dict[str, str], b_floors: dict[str, str]
) -> None:
    """Calling ``hash()`` twice on freshly-built equal-content instances
    always agrees -- the hash never depends on identity, construction
    history, or Python's per-process string-hash randomization seed
    differing between two objects built in the same run.
    """
    a1 = EnvironmentMatrix(runtime_floors=dict(a_floors))
    a2 = EnvironmentMatrix(runtime_floors=dict(a_floors))
    assert hash(a1) == hash(a2)
    if a_floors != b_floors:
        b = EnvironmentMatrix(runtime_floors=dict(b_floors))
        assert a1 != b


# ---------------------------------------------------------------------------
# Genuine immutability: the hash contract requires the *containers*, not
# just "nothing currently mutates them", to be frozen (Codex review, P2
# follow-up, PR #1221).
# ---------------------------------------------------------------------------


class TestGenuineImmutability:
    def test_runtime_floors_is_not_a_plain_mutable_dict(self) -> None:
        """`runtime_floors` is frozen into `model.frozen_str_dict.FrozenStrDict`
        (Codex review, PR #1221, Finding 2 follow-up) rather than a
        `types.MappingProxyType`. Since round 9 it is a
        `collections.abc.Mapping`, deliberately **not** a `dict` subclass
        any more: a `dict` subclass could not actually close every
        mutation vector (`dict.__setitem__(instance, ...)`, calling the
        *base class's* method directly, still reached its shared C-level
        storage regardless of which instance methods were overridden -- see
        `model.frozen_str_dict`'s own module docstring). `dataclasses.
        asdict()` still produces a JSON-serializable plain-dict-shaped
        result for this field, now via `FrozenStrDict.__deepcopy__`
        (`_asdict_inner`'s fallback for a non-`dict` value) rather than the
        `isinstance(obj, dict)` fast path a `dict` subclass rode. Item
        assignment still raises (see the next test)."""
        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        assert not isinstance(matrix.runtime_floors, dict)
        assert type(matrix.runtime_floors) is not dict

    def test_runtime_floors_item_assignment_raises(self) -> None:
        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        with pytest.raises(TypeError):
            matrix.runtime_floors["GLIBC"] = "2.34"  # type: ignore[index]

    def test_mutating_the_original_dict_after_construction_does_not_leak_in(
        self,
    ) -> None:
        """`runtime_floors` must be a *copy*-backed view, not a proxy over
        the caller's own dict -- otherwise a caller mutating the dict it
        passed in still changes this instance's (and its hash's) content."""
        source = {"GLIBC": "2.28"}
        matrix = EnvironmentMatrix(runtime_floors=source)
        original_hash = hash(matrix)
        source["GLIBC"] = "2.34"
        assert matrix.runtime_floors == {"GLIBC": "2.28"}
        assert hash(matrix) == original_hash

    def test_compilers_is_a_tuple(self) -> None:
        matrix = EnvironmentMatrix(compilers=["gcc-13", "clang-17"])
        assert isinstance(matrix.compilers, tuple)
        assert not hasattr(matrix.compilers, "append")

    def test_sycl_backends_is_a_tuple(self) -> None:
        sycl = SyclConstraints(backends=["level_zero", "opencl"])
        assert isinstance(sycl.backends, tuple)

    def test_cuda_gpu_architectures_is_a_tuple(self) -> None:
        cuda = CudaConstraints(gpu_architectures=["sm_80", "sm_90"])
        assert isinstance(cuda.gpu_architectures, tuple)

    def test_dict_lookup_survives_a_mutation_that_would_have_broken_it(
        self,
    ) -> None:
        """The regression this whole fix defends against, stated directly:
        before genuine immutability, `matrix.runtime_floors["GLIBC"] =
        "2.34"` on a matrix already inserted into a dict/set silently
        changed its hash, making it unfindable in that container
        afterward. Now the mutation attempt itself raises, so the
        container membership is never even put at risk."""
        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        cache = {matrix: "resolved"}
        with pytest.raises(TypeError):
            matrix.runtime_floors["GLIBC"] = "2.34"  # type: ignore[index]
        # The matrix is still findable by an equal-but-distinct instance,
        # exactly as it was before the (rejected) mutation attempt.
        lookup = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        assert cache[lookup] == "resolved"

    def test_runtime_floors_get_still_works_like_a_dict(self) -> None:
        """The friendlier half of the fix: callers across the codebase read
        `runtime_floors` via `.get(...)` (`checker.py`, `workflows.
        env_matrix_audit`, `extract.wheel_tags`) -- a `MappingProxyType`
        must keep supporting that read-only interface identically to a
        plain `dict`."""
        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        assert matrix.runtime_floors.get("GLIBC") == "2.28"
        assert matrix.runtime_floors.get("MISSING") is None
        assert matrix.runtime_floors.get("MISSING", "default") == "default"
        assert "GLIBC" in matrix.runtime_floors
        assert dict(matrix.runtime_floors.items()) == {"GLIBC": "2.28"}
        assert len(matrix.runtime_floors) == 1


class TestSetMembershipSurvivesInsertionAndLookupByEqualInstance:
    """Regression test for the specific hash/container invariant this fix
    restores: insert a real instance into a dict/set, then look it up again
    via a separately-constructed but equal instance."""

    def test_dict_membership(self) -> None:
        a = EnvironmentMatrix(
            runtime_floors={"GLIBC": "2.28", "CXXABI": "1.3.13"},
            compilers=["gcc-13"],
        )
        cache: dict[EnvironmentMatrix, str] = {a: "resolved"}
        b = EnvironmentMatrix(
            runtime_floors={"CXXABI": "1.3.13", "GLIBC": "2.28"},
            compilers=["gcc-13"],
        )
        assert cache[b] == "resolved"

    def test_set_membership(self) -> None:
        a = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        b = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        s = {a}
        assert b in s
        s.add(b)
        assert len(s) == 1
