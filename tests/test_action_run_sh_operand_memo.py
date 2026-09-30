# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Nikolay Petrov
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

"""`run.sh`'s per-run memo over `_is_release_style_operand`.

Each uncached answer starts a Python process importing abicheck (~125ms),
and one Action run asked the same two operands six times. The memo must be
invisible except for cost: every answer equals the uncached one, the probe
runs once per distinct *anchored* operand, and a relative spelling asked
from two directories is two operands. Oracle: the real uncached function,
wrapped to count its own calls, over the real sourced helper region.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from _action_run_sh_harness import REQUIRES_POSIX_SHELL
from _workflow_exec import bash_executable, require_bash
from test_action_run_sh_helpers import _cli_introspection_prelude, _helpers_region

pytestmark = REQUIRES_POSIX_SHELL


def _run(tmp_path: Path, body: str) -> list[str]:
    """Source run.sh's helpers, count uncached probes, run *body*; each
    `ask <dir> <path>` line prints `<answer> <probes-so-far>`."""
    require_bash()
    counter = tmp_path / "probes"
    script = (
        _helpers_region()
        + _cli_introspection_prelude()
        + f"""
eval "$(declare -f _is_release_style_operand_uncached | sed '1s/.*/_real_uncached ()/')"
_is_release_style_operand_uncached() {{ echo x >> {str(counter)!r}; _real_uncached "$@"; }}
ask() {{
  (cd "$1" 2>/dev/null) || return 0
  cd "$1"
  if _is_release_style_operand "$2"; then a=1; else a=0; fi
  echo "$a $(wc -l < {str(counter)!r} 2>/dev/null || echo 0)"
}}
: > {str(counter)!r}
{body}
"""
    )
    path = tmp_path / "memo.sh"
    path.write_text(script, encoding="utf-8", newline="\n")
    out = subprocess.run(
        [bash_executable(), str(path)], capture_output=True, text=True, check=True
    )
    return [" ".join(line.split()) for line in out.stdout.splitlines()]


def _operands(tmp_path: Path) -> dict[str, tuple[Path, bool]]:
    libdir = tmp_path / "libdir"
    libdir.mkdir()
    plain = tmp_path / "libfoo.so.1"
    plain.write_text("", encoding="utf-8")
    snap = tmp_path / "snapshot.json"
    snap.write_text("{}", encoding="utf-8")
    deb = tmp_path / "pkg.deb"
    deb.write_bytes(b"!<arch>\n")
    return {
        "libdir": (libdir, True),
        "plain": (plain, False),
        "snap": (snap, False),
        "deb": (deb, True),
    }


def test_repeated_asks_probe_once_and_answer_like_the_uncached_probe(
    tmp_path: Path,
) -> None:
    ops = _operands(tmp_path)
    order = [
        "plain",
        "libdir",
        "plain",
        "deb",
        "libdir",
        "plain",
        "snap",
        "deb",
        "snap",
    ]
    lines = _run(
        tmp_path, "\n".join(f"ask {str(tmp_path)!r} {str(ops[n][0])!r}" for n in order)
    )
    answers = [int(line.split()[0]) for line in lines]
    assert answers == [int(ops[n][1]) for n in order]
    probes = [int(line.split()[1]) for line in lines]
    seen: list[str] = []
    expected = []
    for name in order:
        if name not in seen:
            seen.append(name)
        expected.append(len(seen))
    assert probes == expected


def test_a_relative_operand_is_keyed_by_the_directory_it_was_asked_from(
    tmp_path: Path,
) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "x").mkdir(parents=True)  # a/x is a directory: a release operand
    b.mkdir()
    (b / "x").write_text("", encoding="utf-8")  # b/x is a plain file: not one
    lines = _run(
        tmp_path,
        f"ask {str(a)!r} x\nask {str(b)!r} x\nask {str(a)!r} x\nask {str(b)!r} x",
    )
    assert lines == ["1 1", "0 2", "1 2", "0 2"]
