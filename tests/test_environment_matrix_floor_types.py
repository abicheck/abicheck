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

"""``runtime_floors``' non-numeric-typed keys (``WHEEL_ARCH``/``MUSLLINUX``/
``WHEEL_CONTEXT``) must still reject a wrong-*shape* value.

Split out of ``tests/test_environment_drift.py`` (Codex review, PR #1221,
round-6 follow-up finding 1): that module sits at the AI-readiness 2000-line
hard cap, with no headroom for new tests -- see its own
``architecture/debt.yaml`` entry.

``EnvironmentMatrix._parse_runtime_floors`` exempts ``WHEEL_ARCH``/
``MUSLLINUX``/``WHEEL_CONTEXT`` from the dotted-numeric-version check every
other ``runtime_floors`` key gets, since they carry a non-version token (an
architecture name or a presence flag) rather than a floor. That exemption
must not become a license to accept *any* type: a YAML list, mapping, or
(for ``WHEEL_ARCH`` specifically, which is not a presence-flag key) a bare
bool would otherwise fall through to the unconditional ``str(value)`` and
become a nonsense literal string (``"['x86_64']"``), which the downstream
architecture-mismatch detector treats as an unrecognized claim and reports
nothing for -- a malformed ``strict=True`` config silently disabling a hard
check instead of raising the error it promises.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.environment_matrix import EnvironmentMatrix


class TestNonNumericRuntimeFloorKeysRejectWrongShapeValues:
    @pytest.mark.parametrize(
        "bad_value",
        [["x86_64"], {"arch": "x86_64"}, True, False],
        ids=["list", "mapping", "bool_true", "bool_false"],
    )
    def test_wheel_arch_non_string_value_rejected(self, bad_value: object) -> None:
        with pytest.raises(ValueError, match="must be a quoted string"):
            EnvironmentMatrix.from_dict({"runtime_floors": {"WHEEL_ARCH": bad_value}})

    def test_wheel_arch_non_string_value_rejected_strict(self) -> None:
        # The finding's own reported scenario: the mode .abicheck.yml's
        # `deployment:` block actually loads with.
        with pytest.raises(ValueError, match="must be a quoted string"):
            EnvironmentMatrix.from_dict(
                {"runtime_floors": {"WHEEL_ARCH": ["x86_64"]}}, strict=True
            )

    @pytest.mark.parametrize(
        "bad_value", [["level_zero"], {"arch": "x86_64"}], ids=["list", "mapping"]
    )
    @pytest.mark.parametrize("key", ["MUSLLINUX", "WHEEL_CONTEXT"])
    def test_presence_flag_keys_reject_list_and_mapping_values(
        self, key: str, bad_value: object
    ) -> None:
        # bool/int/float/None are legitimate presence-flag spellings for
        # these two keys (covered in test_environment_drift.py's own
        # TestEnvironmentMatrixRuntimeFloors) and must keep working; a
        # list/mapping is not a presence flag and must still raise rather
        # than silently stringify into a truthy nonsense value.
        with pytest.raises(ValueError, match="must be a quoted string"):
            EnvironmentMatrix.from_dict({"runtime_floors": {key: bad_value}})


class TestWheelArchListEndToEndConfigError:
    def test_deployment_wheel_arch_list_is_hard_config_error(
        self, tmp_path: Path
    ) -> None:
        """`WHEEL_ARCH: [x86_64]` (a YAML list, not a quoted string) in
        `.abicheck.yml`'s `deployment:` block must be a loud exit-64 error --
        not silently stringified into `"['x86_64']"`, which disables the
        wheel-architecture check instead of raising it."""
        from click.testing import CliRunner

        from abicheck.cli import main

        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(
            "deployment:\n  runtime_floors:\n    WHEEL_ARCH:\n      - x86_64\n",
            encoding="utf-8",
        )
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        result = CliRunner().invoke(
            main, ["compare", str(old_dir), str(new_dir), "--config", str(cfg)]
        )
        assert result.exit_code == 64, result.output
        assert "must be a quoted string" in result.output.lower()
