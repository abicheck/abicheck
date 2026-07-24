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

"""Tests for scripts/dump_cli_surface.py and scripts/diff_cli_surface.py --
the CLI-interface-change detector the `cli-interface-check` CI workflow
runs on every PR (a repo-specific ask, not part of any ADR)."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def dump_mod():  # type: ignore[no-untyped-def]
    return _load("dump_cli_surface")


@pytest.fixture(scope="module")
def diff_mod():  # type: ignore[no-untyped-def]
    return _load("diff_cli_surface")


def test_dump_surface_covers_root_commands(dump_mod) -> None:  # type: ignore[no-untyped-def]
    """The dumped surface exposes exactly the public root commands (ADR-043,
    plus ``aggregate`` — the multi-target CI fan-in gate —,
    ``build-output`` — the G30 P1.1 ``build-output.json`` validator group —,
    ``project-targets`` — the G30 P1.5
    ``targets:``/``bundles:``/``profiles:``/``baseline:`` validator group —,
    and ``run-plan`` — the G30 P1.4 run-plan generator group — all added
    afterward).

    `pr-comment` is deliberately NOT here: it is Action/library-only tooling
    (`python -m abicheck.cli_pr_comment`), never a public `abicheck` subcommand.
    """
    surface = dump_mod.dump_surface()
    assert set(surface) == {
        "aggregate",
        "build-output",
        "compare",
        "compat",
        "deps",
        "dump",
        "project-targets",
        "run-plan",
        "scan",
    }


def test_dump_surface_deps_has_subcommands(dump_mod) -> None:  # type: ignore[no-untyped-def]
    surface = dump_mod.dump_surface()
    assert set(surface["deps"]["subcommands"]) == {"tree", "compare"}


def test_dump_surface_option_shape(dump_mod) -> None:  # type: ignore[no-untyped-def]
    surface = dump_mod.dump_surface()
    dump_params = {p["name"]: p for p in surface["dump"]["params"]}
    assert "--dry-run" in dump_params["dry_run"]["opts"]
    assert dump_params["dry_run"]["is_flag"] is True


def test_diff_identical_surface_is_empty(dump_mod, diff_mod) -> None:  # type: ignore[no-untyped-def]
    surface = dump_mod.dump_surface()
    assert diff_mod.diff_surfaces(surface, copy.deepcopy(surface)) == []


def test_diff_detects_removed_and_added_command(diff_mod) -> None:  # type: ignore[no-untyped-def]
    base = {
        "dump": {"path": "dump", "kind": "command", "hidden": False, "params": []},
        "old-cmd": {
            "path": "old-cmd",
            "kind": "command",
            "hidden": False,
            "params": [],
        },
    }
    head = {
        "dump": {"path": "dump", "kind": "command", "hidden": False, "params": []},
        "new-cmd": {
            "path": "new-cmd",
            "kind": "command",
            "hidden": False,
            "params": [],
        },
    }
    lines = diff_mod.diff_surfaces(base, head)
    assert any("removed command `old-cmd`" in ln for ln in lines)
    assert any("added command `new-cmd`" in ln for ln in lines)


def test_diff_detects_option_added_removed_and_changed(diff_mod) -> None:  # type: ignore[no-untyped-def]
    base = {
        "dump": {
            "path": "dump",
            "kind": "command",
            "hidden": False,
            "params": [
                {
                    "name": "output",
                    "kind": "option",
                    "opts": ["-o", "--output"],
                    "required": False,
                    "default": None,
                },
                {
                    "name": "gone",
                    "kind": "option",
                    "opts": ["--gone"],
                    "required": False,
                    "default": None,
                },
                {
                    "name": "depth",
                    "kind": "option",
                    "opts": ["--depth"],
                    "required": False,
                    "default": "auto",
                },
            ],
        },
    }
    head = {
        "dump": {
            "path": "dump",
            "kind": "command",
            "hidden": False,
            "params": [
                {
                    "name": "output",
                    "kind": "option",
                    "opts": ["-o", "--output"],
                    "required": False,
                    "default": None,
                },
                {
                    "name": "new",
                    "kind": "option",
                    "opts": ["--new"],
                    "required": False,
                    "default": None,
                },
                {
                    "name": "depth",
                    "kind": "option",
                    "opts": ["--depth"],
                    "required": False,
                    "default": "binary",
                },
            ],
        },
    }
    lines = diff_mod.diff_surfaces(base, head)
    joined = "\n".join(lines)
    assert "removed option `--gone`" in joined
    assert "added option `--new`" in joined
    assert "changed `--depth`" in joined
    assert "'auto'" in joined and "'binary'" in joined


def test_diff_report_exit_code_semantics(diff_mod) -> None:  # type: ignore[no-untyped-def]
    """run() exits 1 when the surface differs, 0 when identical (never a verdict code)."""
    same = {"dump": {"path": "dump", "kind": "command", "hidden": False, "params": []}}
    other = {"scan": {"path": "scan", "kind": "command", "hidden": False, "params": []}}
    assert diff_mod.diff_surfaces(same, same) == []
    assert diff_mod.diff_surfaces(same, other) != []


def test_render_report_markdown_header(diff_mod) -> None:  # type: ignore[no-untyped-def]
    lines = ["- removed command `foo`"]
    text = diff_mod.render_report(lines, markdown=True)
    assert "## CLI interface change detected" in text
    assert "- removed command `foo`" in text
    assert (
        diff_mod.render_report([], markdown=True)
        == "No user-facing CLI surface changes detected."
    )
