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

"""A *configured* compiler's failure must fail the test, never skip it.

Bug class `guard.compiler_failure_masquerades_as_skip`
(`tests/regressions/manifest_guards.py`). The escape this closes is the one
the silent-skip guard (`tests/conftest.py`'s `ABICHECK_MIN_EXECUTED`) cannot
see: a lane where the compiler *is* present, some fixtures build and satisfy
the minimum-executed floor, and the fixtures that stopped building vanish as
skips — a green run that proved less than it claims.

These tests state the contract as invariants over the whole failure domain
(every nonzero exit status, empty/huge/undecodable stderr, present and absent
optional-feature label), not against the one flag that happened to break —
AGENTS.md, "A bug fix's regression test targets the bug *class*".
"""
from __future__ import annotations

import ast
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_cross_platform_integration import (
    _compile_dll,
    _require_compile_success,
)

_SRC = "int fn(void) { return 0; }"
Failed = pytest.fail.Exception


def _completed(returncode: int, stderr: bytes = b"boom") -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args=["cc"], returncode=returncode, stdout=b"", stderr=stderr)


# The oracle is deliberately *not* the implementation's own branch: "a nonzero
# exit is a failure unless a named optional feature was being probed" is
# restated here from the contract, so a mutation of the helper's condition
# cannot pass by agreeing with itself.
def _expected(returncode: int, optional_feature: str | None) -> str:
    if returncode == 0:
        return "return"
    return "skip" if optional_feature is not None else "fail"


@pytest.mark.parametrize(
    "returncode",
    # The whole small domain, not the one observed status: every POSIX exit
    # code a compiler can plausibly produce, plus the negative values
    # subprocess reports for a signal-killed compiler (OOM-killed cc1 is a
    # real, recurring CI event and must not read as "feature unavailable").
    [0, 1, 2, 4, 33, 64, 126, 127, 128, 139, 255, -9, -11],
)
@pytest.mark.parametrize("optional_feature", [None, "-fsanitize=address"])
def test_outcome_over_the_whole_exit_status_domain(returncode: int, optional_feature: str | None) -> None:
    expected = _expected(returncode, optional_feature)
    result = _completed(returncode)

    if expected == "return":
        assert (
            _require_compile_success(
                "cc", ["cc", "-shared"], _SRC, result, optional_feature=optional_feature
            )
            is None
        )
        return

    raises = pytest.raises(pytest.skip.Exception if expected == "skip" else Failed)
    with raises:
        _require_compile_success(
            "cc", ["cc", "-shared"], _SRC, result, optional_feature=optional_feature
        )


def test_oracle_is_not_a_constant() -> None:
    """Vacuity guard: an oracle reduced to one answer makes the sweep vacuous."""
    answers = {
        _expected(rc, feature)
        for rc in (0, 1)
        for feature in (None, "x")
    }
    assert answers == {"return", "skip", "fail"}


@pytest.mark.parametrize(
    "stderr",
    [b"", b"x" * 100_000, b"\xff\xfe not utf-8", "жёсткая ошибка".encode()],
    # Explicit ids: pytest derives one from the value otherwise, and it exports
    # the full node id in `PYTEST_CURRENT_TEST`. A 100 KB parameter therefore
    # produced a 100 KB environment variable, which Windows rejects outright
    # ("the environment variable is longer than 32767 characters") -- an error
    # at setup, on that platform only. Any parametrized value large enough to
    # matter as a test input is large enough to need an id of its own.
    ids=["empty", "100kb", "not_utf8", "non_ascii"],
)
def test_failure_diagnostics_survive_any_stderr(stderr: bytes) -> None:
    """The failure message carries command, source and stderr for *every*
    stderr shape — including one that is not valid UTF-8, which a naive
    `.decode()` would turn into a `UnicodeDecodeError` masking the real
    compiler error."""
    with pytest.raises(Failed) as excinfo:
        _require_compile_success(
            "clang", ["clang", "-shared", "-o", "x.so"], _SRC, _completed(1, stderr), optional_feature=None
        )

    message = str(excinfo.value)
    assert "clang" in message
    assert "clang -shared -o x.so" in message
    assert _SRC in message
    assert "exited 1" in message


def test_skip_reason_names_the_optional_feature() -> None:
    with pytest.raises(pytest.skip.Exception) as excinfo:
        _require_compile_success(
            "gcc", ["gcc"], _SRC, _completed(1), optional_feature="-Wl,--no-undefined"
        )
    assert "-Wl,--no-undefined" in str(excinfo.value)


def test_no_compile_helper_skips_on_a_bare_returncode() -> None:
    """Structural half: neither fixture-building helper may reach `pytest.skip`
    on its own, which is how the original defect was spelled. The helpers must
    route every nonzero status through the one place that decides."""
    module = Path(__file__).with_name("test_cross_platform_integration.py")
    tree = ast.parse(module.read_text(encoding="utf-8"))
    helpers = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in {"_compile_dylib", "_compile_dll"}
    ]
    assert {h.name for h in helpers} == {"_compile_dylib", "_compile_dll"}

    for helper in helpers:
        calls = [
            ast.unparse(node.func)
            for node in ast.walk(helper)
            if isinstance(node, ast.Call)
        ]
        assert "pytest.skip" not in calls, f"{helper.name} still skips on a compiler error"
        assert "_require_compile_success" in calls, f"{helper.name} does not enforce the contract"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("gcc") is None, reason="Requires gcc")
def test_real_compiler_error_fails_the_test(tmp_path: Path) -> None:
    """Executable half: drive the *real* helper against a real compiler that
    really rejects the build, rather than asserting the helper's text."""
    with pytest.raises(Failed):
        _compile_dll("int fn(void) { return ", "broken.dll", tmp_path)

    # ... and the same helper still builds a good fixture, so the failure
    # above is the compiler's verdict and not a broken helper.
    out = _compile_dll(_SRC, "good.dll", tmp_path)
    assert out.exists()


def test_every_parametrized_id_in_this_module_stays_short() -> None:
    """Guard for the platform-specific setup error the 100 KB stderr case hit.

    pytest exports the whole node id in `PYTEST_CURRENT_TEST`, and Windows caps
    an environment variable at 32767 characters, so a large parameter used as
    its own implicit id turns into a collection-time `ValueError` on one
    platform and passes everywhere else. The bound below is far under that cap;
    the point is that a value big enough to be an interesting input must be
    given an id, not that 200 is special.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "--collect-only", "-q", "--no-header"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, result.stdout + result.stderr

    ids = [line for line in result.stdout.splitlines() if "::" in line]
    assert ids, "collection produced no test ids — the guard would be vacuous"
    too_long = [(line[:60], len(line)) for line in ids if len(line) > 200]
    assert not too_long
