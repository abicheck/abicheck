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

"""`action/install-abicheck.sh`: install once per job, never serve stale source.

The composite Action may be called many times in one job. The script skips
`pip install` when the same interpreter already holds an install of the same
source content. Its contract, checked here against an independent model over
generated call sequences (not one hand-picked repro):

* the first call in a job always installs;
* a repeat call with byte-identical source never reinstalls;
* any change to the source (an edited file, an added file, a different tree)
  reinstalls, including switching *back* to a tree installed earlier -- the
  marker remembers only the most recent install, never a set;
* a lost install (``import abicheck`` fails) reinstalls whatever the marker says;
* a failed ``pip install`` is reported and leaves no marker behind.

``pip`` and ``python`` are stubs on PATH, so the test needs no network and
measures the decision, not pip.
"""

from __future__ import annotations

import os
import random
import subprocess
from pathlib import Path

import pytest

from tests._workflow_exec import bash_executable, require_bash

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "action" / "install-abicheck.sh"

_FAKE_PIP = """#!/usr/bin/env bash
echo "$2" >> "$STATE/pip.log"
[ -f "$STATE/pip_fails" ] && exit 3
touch "$STATE/installed"
"""
_FAKE_PYTHON = """#!/usr/bin/env bash
case "${@: -1}" in
  'import abicheck') [ -f "$STATE/installed" ] ;;
  *) echo "3.13.0 (fake)" ;;
esac
"""


class _Job:
    """One CI job: a stub interpreter, a RUNNER_TEMP, and the pip call log."""

    def __init__(self, tmp: Path) -> None:
        self.state = tmp / "state"
        self.bin = tmp / "bin"
        self.state.mkdir()
        self.bin.mkdir()
        for name, body in (("pip", _FAKE_PIP), ("python", _FAKE_PYTHON)):
            p = self.bin / name
            p.write_text(body)
            p.chmod(0o755)
        self.env = {
            # Native separator: Git Bash on Windows converts a Windows PATH.
            "PATH": os.pathsep.join([str(self.bin), os.environ.get("PATH", "")]),
            "STATE": str(self.state),
            "RUNNER_TEMP": str(tmp / "runner_temp"),
        }

    def call(self, src: Path) -> tuple[int, bool]:
        require_bash()
        log = self.state / "pip.log"
        before = log.read_text().count("\n") if log.exists() else 0
        proc = subprocess.run(
            [bash_executable(), str(SCRIPT), str(src)],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        after = log.read_text().count("\n") if log.exists() else 0
        return proc.returncode, after > before


def _tree(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _content(src: Path) -> tuple:
    """The oracle's notion of 'same source': every file's path and bytes."""
    return tuple(
        sorted(
            (str(p.relative_to(src)), p.read_bytes())
            for p in [src / "pyproject.toml", *(src / "abicheck").rglob("*")]
            if p.is_file() and "__pycache__" not in p.parts
        )
    )


@pytest.mark.parametrize("seed", range(12))
def test_generated_sequences_match_the_model(tmp_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    job = _Job(tmp_path)
    trees = [
        _tree(
            tmp_path / f"src{i}",
            {
                "pyproject.toml": f"[project]\nname='abicheck'\n# {i}\n",
                "abicheck/__init__.py": f"V = {i}\n",
            },
        )
        for i in range(3)
    ]
    last_installed: tuple | None = None
    for step in range(25):
        op = rng.choice(
            ["call", "call", "call", "edit", "add", "nested-edit", "lose", "pycache"]
        )
        src = rng.choice(trees)
        if op == "edit":
            (src / "abicheck" / "__init__.py").write_text(f"V = {rng.random()}\n")
        elif op == "add":
            # Nested files are source too (subpackages, package data).
            sub = rng.choice(["", "compare/", "model/deep/"])
            _tree(src, {f"abicheck/{sub}m{step}.py": "x = 1\n"})
        elif op == "nested-edit":
            _tree(src, {"abicheck/model/deep/k.json": str(rng.random())})
        elif op == "lose":
            (job.state / "installed").unlink(missing_ok=True)
        elif op == "pycache":
            # Bytecode next to the source is not source: it must not force a reinstall.
            _tree(src, {"abicheck/__pycache__/x.cpython-313.pyc": str(rng.random())})
        rc, installed = job.call(src)
        lost = not (job.state / "installed").exists() if not installed else False
        expected = last_installed != _content(src) or (op == "lose")
        assert rc == 0
        assert installed == expected, (seed, step, op, src.name)
        if installed:
            last_installed = _content(src)
        assert not lost


def test_failed_install_is_reported_and_leaves_no_marker(tmp_path: Path) -> None:
    job = _Job(tmp_path)
    src = _tree(tmp_path / "src", {"pyproject.toml": "x", "abicheck/__init__.py": ""})
    (job.state / "pip_fails").touch()
    assert job.call(src) == (3, True)
    (job.state / "pip_fails").unlink()
    # The failed attempt must not be remembered as an install of this source.
    assert job.call(src) == (0, True)
    assert job.call(src) == (0, False)


def test_unfingerprintable_source_falls_back_to_a_plain_install(tmp_path: Path) -> None:
    job = _Job(tmp_path)
    missing = tmp_path / "does-not-exist"
    assert job.call(missing)[1] is True
    assert job.call(missing)[1] is True


def test_action_yml_installs_through_the_script() -> None:
    text = (REPO / "action.yml").read_text()
    assert (
        'run: bash "${{ github.action_path }}/action/install-abicheck.sh" "${{ github.action_path }}"'
        in text
    )
    assert 'run: pip install "${{ github.action_path }}"' not in text
