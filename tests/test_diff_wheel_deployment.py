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

"""Wheel deployment-claim vs. binary-evidence checks (G27): the macOS
deployment-target floor check, the Mach-O counterpart of G10's manylinux
glibc-floor check. All tests use synthetic ``MachoMetadata``/``AbiSnapshot``
— no real binaries required.
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.diff_wheel_deployment import check_macos_deployment_target_floor
from abicheck.environment_matrix import EnvironmentMatrix
from abicheck.macho_metadata import MachoMetadata
from abicheck.model import AbiSnapshot


def _macho(**kwargs) -> MachoMetadata:
    kwargs.setdefault("install_name", "@rpath/libtest.dylib")
    return MachoMetadata(**kwargs)


def _snap(macho: MachoMetadata, **kwargs) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.dylib", version="1.0", macho=macho, platform="macho", **kwargs
    )


def _kinds(changes) -> set[ChangeKind]:
    return {c.kind for c in changes}


class TestMacosDeploymentTargetFloorUnit:
    def test_no_declared_floor_no_finding(self) -> None:
        macho = _macho(min_os_version="12.3")
        assert check_macos_deployment_target_floor(macho, None) == []
        assert check_macos_deployment_target_floor(macho, {}) == []

    def test_no_macho_no_finding(self) -> None:
        assert (
            check_macos_deployment_target_floor(
                None, {"MACOS_DEPLOYMENT_TARGET": "10.14"}
            )
            == []
        )

    def test_within_floor_no_finding(self) -> None:
        macho = _macho(min_os_version="10.9")
        assert (
            check_macos_deployment_target_floor(
                macho, {"MACOS_DEPLOYMENT_TARGET": "10.14"}
            )
            == []
        )

    def test_at_floor_exactly_no_finding(self) -> None:
        macho = _macho(min_os_version="10.14")
        assert (
            check_macos_deployment_target_floor(
                macho, {"MACOS_DEPLOYMENT_TARGET": "10.14"}
            )
            == []
        )

    def test_padded_equal_versions_not_flagged(self) -> None:
        # Codex review #583: a bare "11" floor and a "11.0" load-command
        # minimum name the same version — raw tuple comparison would treat
        # (11, 0) as exceeding (11,) and falsely flag this.
        macho = _macho(min_os_version="11.0")
        assert (
            check_macos_deployment_target_floor(
                macho, {"MACOS_DEPLOYMENT_TARGET": "11"}
            )
            == []
        )

    def test_above_floor_flagged(self) -> None:
        macho = _macho(min_os_version="12.3", install_name="@rpath/libopenblas.dylib")
        changes = check_macos_deployment_target_floor(
            macho, {"MACOS_DEPLOYMENT_TARGET": "10.14"}
        )
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.MACOS_DEPLOYMENT_TARGET_RAISED
        assert changes[0].old_value == "10.14"
        assert changes[0].new_value == "12.3"
        assert "libopenblas" in changes[0].description

    def test_malformed_declared_floor_no_finding_not_crash(self) -> None:
        macho = _macho(min_os_version="12.3")
        changes = check_macos_deployment_target_floor(
            macho, {"MACOS_DEPLOYMENT_TARGET": "not-a-version"}
        )
        assert changes == []

    def test_missing_min_os_version_no_finding(self) -> None:
        macho = _macho(min_os_version="")
        assert (
            check_macos_deployment_target_floor(
                macho, {"MACOS_DEPLOYMENT_TARGET": "10.14"}
            )
            == []
        )

    def test_lowercase_key_still_matches(self) -> None:
        macho = _macho(min_os_version="12.3")
        changes = check_macos_deployment_target_floor(
            macho, {"macos_deployment_target": "10.14"}
        )
        assert len(changes) == 1

    def test_unrelated_floor_key_ignored(self) -> None:
        macho = _macho(min_os_version="12.3")
        assert check_macos_deployment_target_floor(macho, {"GLIBC": "2.28"}) == []


class TestMacosDeploymentTargetFloorCliEndToEnd:
    """The check reaches exit code / JSON through the real ``compare`` CLI
    via ``--env-matrix``'s existing ``runtime_floors`` mechanism (no
    dedicated flag — same declared-constraint contract G10/G27's GLIBC
    checks already use)."""

    def test_raised_floor_surfaces_as_risk(self) -> None:
        old = _snap(_macho(min_os_version="12.3"))
        new = _snap(_macho(min_os_version="12.3"))
        result = compare(
            old,
            new,
            env_matrix=EnvironmentMatrix(
                runtime_floors={"MACOS_DEPLOYMENT_TARGET": "10.14"}
            ),
        )
        assert ChangeKind.MACOS_DEPLOYMENT_TARGET_RAISED in _kinds(result.changes)
        assert result.verdict is Verdict.COMPATIBLE_WITH_RISK

    def test_within_floor_clean(self) -> None:
        old = _snap(_macho(min_os_version="10.9"))
        new = _snap(_macho(min_os_version="10.9"))
        result = compare(
            old,
            new,
            env_matrix=EnvironmentMatrix(
                runtime_floors={"MACOS_DEPLOYMENT_TARGET": "10.14"}
            ),
        )
        assert ChangeKind.MACOS_DEPLOYMENT_TARGET_RAISED not in _kinds(result.changes)

    def test_no_env_matrix_no_finding(self) -> None:
        old = _snap(_macho(min_os_version="12.3"))
        new = _snap(_macho(min_os_version="12.3"))
        result = compare(old, new)
        assert ChangeKind.MACOS_DEPLOYMENT_TARGET_RAISED not in _kinds(result.changes)
