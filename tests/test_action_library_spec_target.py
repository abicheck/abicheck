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


"""One shared ``library-spec`` drives both the baseline and the compare gate.

``actions/baseline`` dumps every component of a spec; the root Action's
``library-spec`` + ``library-spec-target`` resolves ONE component of that same
file into ``new-library``/``new-header``/``new-include``. The invariant is that
the per-target view never disagrees with the baseline's view of that target:
for every declared component, the ``--target`` result equals that component's
entry in the full resolution. The oracle is the full-set resolution the
baseline Action already ships, checked over every component rather than one.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from _workflow_exec import bash_executable, require_bash
from click.testing import CliRunner

from abicheck.frontends.action.cli import action_cli
from abicheck.frontends.action.library_selection import (
    SelectionError,
    libraries_payload,
    resolve_library_set,
)

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "action" / "resolve-library-spec.sh"
NAMES = ("libalpha", "libbeta", "libgamma")


def _elf(path: Path, machine: int = 62) -> None:
    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    header[4], header[5], header[6] = 2, 1, 1
    header[16:18] = (3).to_bytes(2, "little")  # ET_DYN
    header[18:20] = machine.to_bytes(2, "little")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(header))


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, list[dict]]:
    root = tmp_path / "inst"
    (root / "include" / "sub").mkdir(parents=True)
    for header in ("umbrella.h", "sub/extra.hpp", "private.h"):
        (root / "include" / header).write_text("#pragma once\n", encoding="utf-8")
    spec = []
    for index, name in enumerate(NAMES):
        _elf(root / "lib" / f"{name}.so.1")
        entry: dict = {"name": name, "artifact": f"lib/{name}.so*"}
        # Vary the shape per component: shared umbrella headers, an exclusion,
        # and one header-less component.
        if index == 0:
            entry |= {
                "header": ["include/umbrella.h", "include/sub/*.hpp"],
                "include": "include",
            }
        elif index == 1:
            entry |= {
                "header": ["include/*.h"],
                "header_exclude": ["include/private.h"],
                "include": ["include", "include/sub"],
            }
        spec.append(entry)
    return root, spec


def test_every_target_matches_its_entry_in_the_full_set(tree) -> None:
    root, spec = tree
    full = {
        e["name"]: e for e in libraries_payload(resolve_library_set(spec, root=root))
    }
    for name in NAMES:
        single = libraries_payload(resolve_library_set(spec, root=root, only=name))
        assert single == [full[name]], name


def test_only_the_selected_artifact_has_to_exist(tree) -> None:
    root, spec = tree
    (root / "lib" / "libgamma.so.1").unlink()
    with pytest.raises(SelectionError):
        resolve_library_set(spec, root=root)
    assert resolve_library_set(spec, root=root, only="libalpha")[0].name == "libalpha"


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda s: None, "not declared"),
        (lambda s: s.append(dict(s[0])), "declared twice"),
        (lambda s: s.append({"artifact": "x"}), "no usable 'name'"),
    ],
)
def test_whole_spec_is_still_validated(tree, mutate, needle) -> None:
    root, spec = tree
    mutate(spec)
    target = "libdelta" if needle == "not declared" else "libbeta"
    with pytest.raises(SelectionError, match=needle):
        resolve_library_set(spec, root=root, only=target)


def test_cli_target_writes_the_new_side_outputs(tree, tmp_path: Path) -> None:
    root, spec = tree
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    full = {
        e["name"]: e for e in libraries_payload(resolve_library_set(spec, root=root))
    }
    for name in NAMES:
        out = tmp_path / f"{name}.out"
        result = CliRunner().invoke(
            action_cli,
            [
                "resolve-libraries",
                str(spec_file),
                "--root",
                str(root),
                "--target",
                name,
                "--github-output",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        lines = dict(line.split("=", 1) for line in out.read_text().splitlines())
        assert lines["artifact"] == full[name]["artifact"]
        assert lines["header"] == full[name].get("header", "")
        assert lines["include"] == full[name].get("include", "")
        assert lines["library-count"] == "1"


def test_cli_refuses_placeholders_without_bindings(tree, tmp_path: Path) -> None:
    root, spec = tree
    spec[0]["artifact"] = "lib/${ARCH}/libalpha.so*"
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    result = CliRunner().invoke(
        action_cli,
        [
            "resolve-libraries",
            str(spec_file),
            "--root",
            str(root),
            "--target",
            "libalpha",
        ],
    )
    assert result.exit_code != 0


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"INPUT_NEW_LIBRARY": "x.so"},
        {"INPUT_HEADER": "a.h"},
        {"INPUT_NEW_HEADER": "a.h"},
        {"INPUT_INCLUDE": "inc"},
        {"INPUT_NEW_INCLUDE": "inc"},
        {"INPUT_LIBRARY_SPEC_TARGET": ""},
    ],
)
def test_action_script_refuses_a_second_declaration(
    tree, tmp_path: Path, extra
) -> None:
    root, spec = tree
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    out = tmp_path / "gh_out"
    out.write_text("")
    env = {
        **os.environ,
        "GITHUB_OUTPUT": str(out),
        "INPUT_LIBRARY_SPEC": str(spec_file),
        "INPUT_LIBRARY_SPEC_TARGET": "libbeta",
        "RUNNER_OS": "Linux",
        **extra,
    }
    require_bash()
    proc = subprocess.run(
        [bash_executable(), str(SCRIPT)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    if not extra:
        assert proc.returncode == 0, proc.stderr
        assert "header=" in out.read_text()
    else:
        assert proc.returncode == 64, proc.stdout + proc.stderr
        assert out.read_text() == ""
