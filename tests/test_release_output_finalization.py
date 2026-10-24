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

"""Branches of the release output finalization and suppression validation.

``_validate_suppression_early`` and ``_finalize_release_output`` were moved
verbatim out of the retired ``cli_compare_release_matrix`` module. These tests pin the branches
the moved code owns through the entry points a user reaches, so the patch
coverage of the move does not depend on the full unit lane.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.frontends.cli.release_compare_request import _validate_suppression_early
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _snap(library: str) -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version="1.0",
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
        from_headers=True,
    )


def _write(dir_: Path, name: str, snap: AbiSnapshot) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / name).write_text(snapshot_to_json(snap), encoding="utf-8")


def _expired_suppression(tmp_path: Path) -> Path:
    past = date.today() - timedelta(days=30)
    path = tmp_path / "suppressions.yaml"
    path.write_text(
        "version: 1\n"
        "suppressions:\n"
        '  - symbol: "_Z3foov"\n'
        '    reason: "expired"\n'
        f'    expires: "{past.isoformat()}"\n',
        encoding="utf-8",
    )
    return path


class TestValidateSuppressionEarly:
    def test_without_a_strict_flag_the_file_is_not_read(self, tmp_path: Path) -> None:
        # A path that does not exist would raise if it were opened: the early
        # check is a no-op unless strict or justification validation was asked.
        missing = tmp_path / "does-not-exist.yaml"
        _validate_suppression_early(missing, "warn", None, False, False)
        _validate_suppression_early(None, "warn", None, True, True)

    def test_strict_mode_rejects_an_expired_rule_before_any_comparison(
        self, tmp_path: Path
    ) -> None:
        sup = _expired_suppression(tmp_path)
        with pytest.raises(Exception, match="expired"):
            _validate_suppression_early(sup, "warn", None, True, False)


class TestReleaseOutputFinalization:
    def test_output_dir_writes_the_summary_sidecar(self, tmp_path: Path) -> None:
        from abicheck.frontends.cli.release_summary import _finalize_release_output

        out = tmp_path / "out"
        _finalize_release_output(
            "markdown",
            "NO_CHANGE",
            tmp_path / "old",
            tmp_path / "new",
            [],
            [],
            [],
            {},
            {},
            [],
            [],
            None,
            None,
            out,
            False,
        )
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        assert summary["exit"]["code"] == 0
        assert summary["exit"]["reasons"] == ["clean"]
        assert summary["run_outcome"]["scope"] == "complete"

    def test_incomplete_scope_prints_its_notice_on_stderr(self, tmp_path: Path) -> None:
        from test_release_scope_completeness import _write_unreadable

        old, new = tmp_path / "old", tmp_path / "new"
        _write(old, "liba.json", _snap("liba.so"))
        _write_unreadable(old, "libb.json", _snap("libb.so"))
        _write(new, "liba.json", _snap("liba.so"))
        _write(new, "libb.json", _snap("libb.so"))
        result = CliRunner().invoke(
            main, ["compare", str(old), str(new), "-o", "markdown=-"]
        )
        assert "scope incompletely checked" in result.stderr
