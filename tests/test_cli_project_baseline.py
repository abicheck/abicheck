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

"""``abicheck project baseline pack``/``unpack`` CLI wiring.

Exercises the Click commands themselves (argument parsing, exit codes,
output formatting); the pack/unpack primitives are tested directly in
``test_product_baseline.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.product_baseline import unpack_product_baseline


def _make_product(root: Path) -> Path:
    product = root / "product"
    (product / "lib").mkdir(parents=True)
    (product / "include").mkdir(parents=True)
    (product / "lib" / "liba.so").write_bytes(b"ELF-A" * 50)
    (product / "include" / "a.h").write_text("struct A { int x; };\n")
    return product


class TestBaselinePackCli:
    def test_pack_writes_archive(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        out = tmp_path / "baseline.tar.zst"
        result = CliRunner().invoke(
            main,
            [
                "project",
                "baseline",
                "pack",
                str(product),
                str(out),
                "--product",
                "demo",
            ],
        )
        assert result.exit_code == 0, result.output
        assert out.is_file()
        assert "packed 1 library" in result.output

    def test_pack_with_header_root(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        out = tmp_path / "baseline.tar.zst"
        result = CliRunner().invoke(
            main,
            [
                "project",
                "baseline",
                "pack",
                str(product),
                str(out),
                "--header-root",
                "include",
            ],
        )
        assert result.exit_code == 0, result.output
        manifest = unpack_product_baseline(out, tmp_path / "unpacked")
        assert manifest.header_roots == ("include",)

    def test_pack_rejects_bad_header_root_as_usage_error(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        out = tmp_path / "baseline.tar.zst"
        result = CliRunner().invoke(
            main,
            [
                "project",
                "baseline",
                "pack",
                str(product),
                str(out),
                "--header-root",
                "does-not-exist",
            ],
        )
        assert result.exit_code == 64
        assert not out.exists()

    def test_pack_rejects_empty_source_dir(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        result = CliRunner().invoke(
            main,
            ["project", "baseline", "pack", str(empty), str(tmp_path / "out.tar.zst")],
        )
        assert result.exit_code == 64


class TestBaselineUnpackCli:
    def test_unpack_text_output(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_result = CliRunner().invoke(
            main, ["project", "baseline", "pack", str(product), str(archive)]
        )
        assert pack_result.exit_code == 0, pack_result.output

        dest = tmp_path / "unpacked"
        result = CliRunner().invoke(
            main, ["project", "baseline", "unpack", str(archive), str(dest)]
        )
        assert result.exit_code == 0, result.output
        assert (dest / "lib" / "liba.so").is_file()
        assert "1 library" in result.output

    def test_unpack_json_output(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_result = CliRunner().invoke(
            main,
            [
                "project",
                "baseline",
                "pack",
                str(product),
                str(archive),
                "--product",
                "demo",
            ],
        )
        assert pack_result.exit_code == 0, pack_result.output

        dest = tmp_path / "unpacked"
        result = CliRunner().invoke(
            main,
            [
                "project",
                "baseline",
                "unpack",
                str(archive),
                str(dest),
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["product"] == "demo"
        assert payload["libraries"][0]["name"] == "liba.so"

    def test_unpack_nonempty_destination_is_usage_error(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_result = CliRunner().invoke(
            main, ["project", "baseline", "pack", str(product), str(archive)]
        )
        assert pack_result.exit_code == 0, pack_result.output

        dest = tmp_path / "unpacked"
        dest.mkdir()
        (dest / "existing").write_text("x\n")
        result = CliRunner().invoke(
            main, ["project", "baseline", "unpack", str(archive), str(dest)]
        )
        assert result.exit_code == 64
        assert "not empty" in result.output

    def test_unpack_missing_archive_is_a_click_usage_error(
        self, tmp_path: Path
    ) -> None:
        result = CliRunner().invoke(
            main,
            [
                "project",
                "baseline",
                "unpack",
                str(tmp_path / "nope.tar.zst"),
                str(tmp_path / "dest"),
            ],
        )
        assert result.exit_code != 0


class TestProjectBaselineHelp:
    """Root-surface documentation contract for the new subgroup, matching
    the shape ``tests/test_cli_root_surface.py`` uses for existing
    ``project`` subcommands (`project`'s own subcommand set isn't pinned
    the way the root command set is -- see AGENTS.md)."""

    def test_project_help_lists_baseline_subgroup(self) -> None:
        result = CliRunner().invoke(main, ["project", "--help"])
        assert result.exit_code == 0
        assert "baseline" in result.output

    def test_baseline_help_lists_pack_and_unpack(self) -> None:
        result = CliRunner().invoke(main, ["project", "baseline", "--help"])
        assert result.exit_code == 0
        assert "pack" in result.output
        assert "unpack" in result.output
