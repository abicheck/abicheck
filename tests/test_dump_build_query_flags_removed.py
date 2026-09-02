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

"""``dump --build-query`` / ``--build-compile-db`` are removed (PR 3C / PR F).

Split out of ``tests/test_dry_run_build_query_contract.py`` rather than added
to it, following that file's own earlier split into
``test_dry_run_build_query_flow2_packs.py``: the contract module is tracked in
``architecture/debt.yaml`` as no-growth, and this is a distinct subject anyway
-- that module tests what the dry-run *reports* about a configured query, this
one tests that two CLI spellings no longer exist and that nothing on the
command line can authorize executing one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main


class TestBuildQueryFlagsRemoved:
    """PR 3C's removal itself: ``dump --build-query`` / ``--build-compile-db``
    are gone, and an explicit ``--config`` is the *only* thing that can
    authorize executing ``build.query``.

    The two assertions are deliberately separate concerns. The first is the
    plan's stated merge criterion for every removal in this phase -- the old
    spelling is a hard usage error (exit 64, ``No such option``), never a
    hidden alias that keeps working silently. The second is the security
    property those flags were entangled with: before this removal the real
    gate read ``build_config is not None or build_query is not None``, so a
    bare ``--build-query`` on the command line was a second, independent way
    to mark an arbitrary command trusted to execute. With the flag gone the
    gate has exactly one term, and there is no CLI-only route to execution at
    all (ADR-032 D5, prerequisites 1 and 2).
    """

    @pytest.mark.parametrize(
        ("flag", "value"),
        [
            ("--build-query", "cmake -S . -B build"),
            ("--build-compile-db", "build/compile_commands.json"),
        ],
    )
    def test_removed_flag_is_a_usage_error(
        self, tmp_path: Path, flag: str, value: str
    ) -> None:
        """The old spelling is rejected outright, not silently accepted."""
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        result = CliRunner().invoke(
            main,
            ["dump", "--sources", str(tmp_path), "-H", str(header), flag, value],
        )
        assert result.exit_code == 64, result.output
        assert "No such option" in result.output
        assert flag in result.output

    def test_no_cli_route_can_authorize_an_auto_discovered_query(
        self, tmp_path: Path
    ) -> None:
        """An auto-discovered config's query stays untrusted no matter what
        else is on the command line.

        Asserted over the whole surviving build-evidence flag surface rather
        than one representative: the removed pair is only genuinely gone if
        *no* remaining flag re-opens the same authorization. ``--config`` is
        excluded because authorizing is precisely its job.
        """
        header = tmp_path / "api.h"
        header.write_text("int foo(int x);\n", encoding="utf-8")
        (tmp_path / ".abicheck.yml").write_text(
            "build:\n  query: touch pwned\n", encoding="utf-8"
        )
        survivors: list[list[str]] = [
            [],
            ["--build-target", "//:lib"],
            ["--compile-db-filter", "*.cpp"],
            ["--allow-build-query"],
            ["--depth", "build"],
            ["--depth", "source"],
        ]
        for extra in survivors:
            result = CliRunner().invoke(
                main,
                [
                    "dump", "--sources", str(tmp_path), "-H", str(header),
                    "--dry-run", *extra,
                ],
            )
            assert result.exit_code == 0, (extra, result.output)
            assert "will NOT run" in result.output, (extra, result.output)
            assert "auto-discovered" in result.output, (extra, result.output)
            # The dry run never executes it, and neither does anything else
            # reachable from this command line.
            assert not (tmp_path / "pwned").exists(), extra
