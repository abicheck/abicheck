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

"""Tests for the NumPy C-API compatibility-envelope detectors (G26)."""

from __future__ import annotations

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.diff_numpy_capi import (
    check_numpy_metadata_contract,
    diff_numpy_capi_surfaces,
)
from abicheck.model import AbiSnapshot
from abicheck.numpy_capi import NumPyCapiSurface


def _kinds(changes: list) -> set[ChangeKind]:
    return {c.kind for c in changes}


class TestDiffNumPyCapiSurfaces:
    def test_consumption_added(self) -> None:
        changes = diff_numpy_capi_surfaces(
            None, NumPyCapiSurface(consumes_array_api=True)
        )
        assert _kinds(changes) == {ChangeKind.NUMPY_CAPI_CONSUMPTION_ADDED}

    def test_consumption_added_from_prior_non_consuming_surface(self) -> None:
        # A prior surface object that exists but consumes nothing (e.g. a
        # library that previously imported neither table) — same "added"
        # transition as starting from None.
        changes = diff_numpy_capi_surfaces(
            NumPyCapiSurface(), NumPyCapiSurface(consumes_ufunc_api=True)
        )
        assert _kinds(changes) == {ChangeKind.NUMPY_CAPI_CONSUMPTION_ADDED}

    def test_consumption_removed(self) -> None:
        changes = diff_numpy_capi_surfaces(
            NumPyCapiSurface(consumes_array_api=True), NumPyCapiSurface()
        )
        assert _kinds(changes) == {ChangeKind.NUMPY_CAPI_CONSUMPTION_REMOVED}

    def test_consumption_removed_to_none(self) -> None:
        changes = diff_numpy_capi_surfaces(
            NumPyCapiSurface(consumes_array_api=True), None
        )
        assert _kinds(changes) == {ChangeKind.NUMPY_CAPI_CONSUMPTION_REMOVED}

    def test_no_consumption_either_side_is_silent(self) -> None:
        assert diff_numpy_capi_surfaces(None, None) == []
        assert diff_numpy_capi_surfaces(NumPyCapiSurface(), NumPyCapiSurface()) == []

    def test_target_floor_raised(self) -> None:
        old = NumPyCapiSurface(consumes_array_api=True, capi_target_version="1.22")
        new = NumPyCapiSurface(consumes_array_api=True, capi_target_version="1.23")
        changes = diff_numpy_capi_surfaces(old, new)
        assert _kinds(changes) == {ChangeKind.NUMPY_TARGET_FLOOR_RAISED}
        assert changes[0].old_value == "1.22"
        assert changes[0].new_value == "1.23"

    def test_target_floor_dropped_is_not_flagged(self) -> None:
        # A lower target-version floor is a compatibility improvement (works
        # on an older NumPy than before), never a regression.
        old = NumPyCapiSurface(consumes_array_api=True, capi_target_version="1.25")
        new = NumPyCapiSurface(consumes_array_api=True, capi_target_version="1.23")
        assert diff_numpy_capi_surfaces(old, new) == []

    def test_target_floor_unchanged_is_silent(self) -> None:
        old = NumPyCapiSurface(consumes_array_api=True, capi_target_version="1.23")
        new = NumPyCapiSurface(consumes_array_api=True, capi_target_version="1.23")
        assert diff_numpy_capi_surfaces(old, new) == []

    def test_missing_target_version_on_either_side_skips_floor_check(self) -> None:
        old = NumPyCapiSurface(consumes_array_api=True, capi_target_version=None)
        new = NumPyCapiSurface(consumes_array_api=True, capi_target_version="1.23")
        assert diff_numpy_capi_surfaces(old, new) == []


class TestCheckNumPyMetadataContract:
    def test_no_surface_returns_nothing(self) -> None:
        assert check_numpy_metadata_contract(None, ">=1.20") == []

    def test_no_target_version_returns_nothing(self) -> None:
        surf = NumPyCapiSurface(consumes_array_api=True, capi_target_version=None)
        assert check_numpy_metadata_contract(surf, ">=1.20") == []

    def test_declared_floor_covers_target_is_clean(self) -> None:
        surf = NumPyCapiSurface(consumes_array_api=True, capi_target_version="1.23")
        assert check_numpy_metadata_contract(surf, ">=1.23.5") == []
        assert check_numpy_metadata_contract(surf, ">=1.23") == []
        assert check_numpy_metadata_contract(surf, ">=1.25") == []

    def test_declared_floor_understates_target(self) -> None:
        surf = NumPyCapiSurface(consumes_array_api=True, capi_target_version="1.23")
        changes = check_numpy_metadata_contract(surf, ">=1.20")
        assert _kinds(changes) == {
            ChangeKind.NUMPY_METADATA_UNDERSTATES_REQUIRED_VERSION
        }
        assert changes[0].old_value == ">=1.20"
        assert changes[0].new_value == "1.23"

    def test_no_declaration_at_all_understates(self) -> None:
        surf = NumPyCapiSurface(consumes_array_api=True, capi_target_version="1.23")
        changes = check_numpy_metadata_contract(surf, None)
        assert _kinds(changes) == {
            ChangeKind.NUMPY_METADATA_UNDERSTATES_REQUIRED_VERSION
        }
        assert changes[0].old_value == "(none declared)"

    def test_bare_unconstrained_declaration_understates(self) -> None:
        # "Requires-Dist: numpy" with no version constraint at all declares
        # no real floor — same as undeclared for this purpose.
        surf = NumPyCapiSurface(consumes_array_api=True, capi_target_version="1.23")
        changes = check_numpy_metadata_contract(surf, "")
        assert _kinds(changes) == {
            ChangeKind.NUMPY_METADATA_UNDERSTATES_REQUIRED_VERSION
        }

    def test_abi_major_incompatible_when_target_crosses_2_0_boundary(self) -> None:
        surf = NumPyCapiSurface(consumes_array_api=True, capi_target_version="2.1")
        changes = check_numpy_metadata_contract(surf, ">=1.20")
        assert _kinds(changes) == {
            ChangeKind.NUMPY_METADATA_UNDERSTATES_REQUIRED_VERSION,
            ChangeKind.NUMPY_ABI_MAJOR_INCOMPATIBLE,
        }

    def test_abi_major_incompatible_not_flagged_when_declared_floor_already_2_x(
        self,
    ) -> None:
        # The declared floor already excludes NumPy 1.x — no crash risk, even
        # though the exact patch version still understates the real target.
        surf = NumPyCapiSurface(consumes_array_api=True, capi_target_version="2.1")
        changes = check_numpy_metadata_contract(surf, ">=2.0")
        assert _kinds(changes) == {
            ChangeKind.NUMPY_METADATA_UNDERSTATES_REQUIRED_VERSION
        }
        assert ChangeKind.NUMPY_ABI_MAJOR_INCOMPATIBLE not in _kinds(changes)

    def test_malformed_declared_specifier_degrades_to_understated(self) -> None:
        surf = NumPyCapiSurface(consumes_array_api=True, capi_target_version="1.23")
        changes = check_numpy_metadata_contract(surf, "not a valid specifier!!")
        assert _kinds(changes) == {
            ChangeKind.NUMPY_METADATA_UNDERSTATES_REQUIRED_VERSION
        }


class TestNumPyCapiWiredIntoCompare:
    """checker.compare() runs diff_numpy_capi_surfaces automatically — no
    env_matrix or other opt-in needed, since it only needs the two
    snapshots' own numpy_capi field (G26)."""

    def test_target_floor_raised_surfaces_through_compare(self) -> None:
        old = AbiSnapshot(
            library="mod.so",
            version="1.0",
            numpy_capi=NumPyCapiSurface(
                consumes_array_api=True, capi_target_version="1.22"
            ),
        )
        new = AbiSnapshot(
            library="mod.so",
            version="2.0",
            numpy_capi=NumPyCapiSurface(
                consumes_array_api=True, capi_target_version="1.23"
            ),
        )
        result = compare(old, new)
        assert ChangeKind.NUMPY_TARGET_FLOOR_RAISED in _kinds(result.changes)

    def test_no_numpy_capi_on_either_snapshot_is_silent(self) -> None:
        old = AbiSnapshot(library="mod.so", version="1.0")
        new = AbiSnapshot(library="mod.so", version="2.0")
        result = compare(old, new)
        assert ChangeKind.NUMPY_TARGET_FLOOR_RAISED not in _kinds(result.changes)
        assert ChangeKind.NUMPY_CAPI_CONSUMPTION_ADDED not in _kinds(result.changes)
