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

"""``action/run.sh`` scan mode must reject ``build-info`` + ``compile-db``.

``scan --compile-db`` is gone from the CLI -- ``--build-info`` already takes a
build directory, a ``compile_commands.json``, or a pack -- so the Action's two
inputs collapse onto one flag through a
``${INPUT_BUILD_INFO:-${INPUT_COMPILE_DB:-}}`` fallback, which silently
discards ``compile-db`` when both are set. Scan previously forwarded *both*
operands and the scan pipeline preferred ``compile-db``, so a workflow setting
both would quietly start analyzing a different build context (Codex review).

Scoped deliberately to scan. Compare and dump have always used this same
fallback -- ``compare`` never had a ``--compile-db`` flag to forward -- so
their "build-info wins" precedence is pre-existing behavior with its own test
(``test_action_run_sh_compare_build_source.py``'s
``test_build_info_takes_precedence_over_compile_db``), not a regression to
correct. A first version of this guard was global and broke exactly that.

The fragment is extracted verbatim from run.sh and executed, the same
"parse the real file, don't hand-copy it" discipline as
``test_action_run_sh_helpers.py``: asserting the guard's *text* would not
prove it actually exits.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_START = "  # Rejected rather than resolved by the fallback below when both are set,"
_END = '  add_single_flag "--build-info"'


def _guard() -> str:
    """The scan-mode guard, verbatim and dedented enough to run standalone."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_START)
    return text[start : text.index(_END, start)]


def _run(build_info: str, compile_db: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", _guard() + "\necho REACHED_END"],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "INPUT_BUILD_INFO": build_info,
            "INPUT_COMPILE_DB": compile_db,
        },
    )


def test_both_set_is_a_hard_error() -> None:
    res = _run("build/", "build/compile_commands.json")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "REACHED_END" not in res.stdout
    assert "::error::" in res.stdout
    # Both values are named, so the workflow author can see which two inputs
    # collided without reading the Action source.
    assert "build/" in res.stdout
    assert "build/compile_commands.json" in res.stdout


@pytest.mark.parametrize(
    ("build_info", "compile_db"),
    [
        ("", ""),               # neither set: the ordinary case
        ("build/", ""),         # the preferred spelling alone
        ("", "compile_commands.json"),  # the compatibility spelling alone
    ],
)
def test_one_or_neither_passes_through(build_info: str, compile_db: str) -> None:
    res = _run(build_info, compile_db)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "REACHED_END" in res.stdout


def test_the_guard_covers_only_the_scan_forwarding_site() -> None:
    """Three sites share the fallback; only scan's is behind the guard.

    Pinning the scope directly, because the natural mistake here is a global
    guard -- which is what the first version of this fix was, and it broke
    compare mode's own pre-existing precedence test.
    """
    lines = RUN_SH.read_text(encoding="utf-8").splitlines()
    guard_at = next(i for i, line in enumerate(lines) if line.startswith(_START))
    # Only real forwarding sites -- the guard's own comment quotes the
    # fallback expression too, and a text scan would count that as another.
    fallbacks = [
        i
        for i, line in enumerate(lines)
        if "${INPUT_BUILD_INFO:-${INPUT_COMPILE_DB:-}}" in line
        and ("add_single_flag" in line or "add_sided_flag" in line)
    ]
    assert len(fallbacks) == 3, fallbacks
    assert sum(1 for i in fallbacks if i > guard_at) == 1, (guard_at, fallbacks)


def test_a_real_scan_mode_run_hits_the_guard(tmp_path: Path) -> None:
    """The extracted fragment proves the guard exits; this proves the guard is
    on scan mode's actual path, with a stub abicheck that would otherwise be
    invoked."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stub = fake_bin / "abicheck"
    stub.write_text("#!/usr/bin/env bash\necho STUB_RAN\nexit 0\n")
    stub.chmod(0o755)
    lib = tmp_path / "libfoo.so"
    lib.write_bytes(b"\x7fELF" + b"\x00" * 60)

    env = {
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "gh_summary"),
        "INPUT_MODE": "scan",
        "INPUT_NEW_LIBRARY": str(lib),
        "INPUT_BUILD_INFO": "/build",
        "INPUT_COMPILE_DB": "/compile_commands.json",
    }
    res = subprocess.run(
        ["bash", str(RUN_SH)], capture_output=True, text=True, env=env, cwd=tmp_path
    )
    assert res.returncode == 1, res.stdout + res.stderr
    assert "are both set for mode: scan" in res.stdout
    assert "STUB_RAN" not in res.stdout
