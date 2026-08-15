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

"""``$_PY_SAFE_DIR`` (``action/run.sh``): the checkout-shadowing prevention
mechanism every inline Python invocation that imports a real ``abicheck``
module runs through (Codex review, PR #774).

``python -c``/``-m`` insert the current working directory into
``sys.path``, and Python's automatic ``site`` processing (which runs during
interpreter *startup*, before a ``-c`` script body ever executes) auto-
imports a discoverable ``sitecustomize.py``/``usercustomize.py`` from
anywhere on the resulting ``sys.path`` -- including a ``PYTHONPATH`` entry.
So on a ``pull_request``-triggered workflow -- where the checkout is the PR
author's own, untrusted code -- a PR that added a same-named module (e.g.
its own ``abicheck/_compiler_options.py``) or a top-level
``sitecustomize.py`` could make one of run.sh's inline scripts import and
execute that code instead of the real, pip-installed package.

Four earlier revisions of a fix each tried to *filter* ``sys.path`` from
*inside* the ``-c`` script body after Python had already started resolving
it (strip the resolved CWD; strip a resolved ``PYTHONPATH=.`` entry; strip
any descendant path, not just an exact match; pair every call site with
``-S`` plus a manual ``site.main()`` re-run to also outrun the
sitecustomize auto-import window) -- each fixed a real gap the previous one
left open, but the cumulative ``-S`` + manual re-processing broke real
``abicheck`` importability on a ``windows-latest`` CI runner for reasons
that could not be fully root-caused remotely. This revision removes the
whole "clean up sys.path after Python already started" premise: every such
invocation now runs from a freshly created, empty temporary directory
(``$_PY_SAFE_DIR``) with ``PYTHONPATH`` cleared for that one invocation --
so the untrusted checkout is never on ``sys.path`` at any point, and
neither ``-S`` nor any Python-side filtering is needed.

These tests exercise the actual runtime property through a real bash
subprocess reproducing run.sh's own mechanism verbatim (the same
"parse the real file, don't hand-copy it" discipline as this module's
siblings), started with its CWD set to a directory containing a fake,
"malicious" ``abicheck`` package or ``sitecustomize.py`` -- proving the
mechanism blocks both, and (via a companion "without the fix" control test)
that the attack genuinely reproduces without it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

_PY_SAFE_DIR_START = '_PY_SAFE_DIR="$(mktemp'

_MALICIOUS_MARKER = "MALICIOUS CODE EXECUTED"


def _py_safe_dir_source() -> str:
    """Extract the real ``_PY_SAFE_DIR=...`` assignment verbatim from
    run.sh -- one line, up to and including its trailing newline."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_PY_SAFE_DIR_START)
    end = text.index("\n", start) + 1
    return text[start:end]


def _write_fake_abicheck_package(root: Path) -> None:
    pkg = root / "abicheck"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "_compiler_options.py").write_text(
        f'def split_gcc_options(text):\n    raise RuntimeError("{_MALICIOUS_MARKER}")\n'
    )


def _write_fake_sitecustomize(root: Path) -> None:
    (root / "sitecustomize.py").write_text(
        f'import sys\nprint("{_MALICIOUS_MARKER}", file=sys.stderr)\n'
    )


def _bash_executable() -> str:
    """Resolve a real bash, bypassing Windows' WSL-launcher stub.

    See ``test_action_run_sh_helpers._bash_executable`` for the full
    rationale.
    """
    if os.name != "nt":
        return "bash"
    for candidate in (
        os.environ.get("GIT_BASH_PATH"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return "bash"


def _run_import_via_real_mechanism(
    cwd: Path, extra_env: dict[str, str] | None = None, *, use_the_fix: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run the real ``$_PY_SAFE_DIR``-based invocation (extracted verbatim
    from run.sh) importing ``abicheck._compiler_options`` -- or, with
    ``use_the_fix=False``, the identical import with no such mechanism at
    all, as the control case proving the attack genuinely reproduces
    without it."""
    py_snippet = (
        "from abicheck._compiler_options import split_gcc_options\n"
        'print(split_gcc_options("-DFOO=1"))\n'
    )
    if use_the_fix:
        script = (
            '_PY_BIN="$(command -v python3 || command -v python || true)"\n'
            + _py_safe_dir_source()
            + '(cd "$_PY_SAFE_DIR" && PYTHONPATH= "$_PY_BIN" -c \''
            + py_snippet
            + "')\n"
        )
    else:
        script = (
            '_PY_BIN="$(command -v python3 || command -v python || true)"\n'
            + '"$_PY_BIN" -c \''
            + py_snippet
            + "'\n"
        )
    env = {**os.environ, **(extra_env or {})}
    return subprocess.run(
        [_bash_executable(), "-c", script],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=30,
    )


def _run_sitecustomize_probe_via_real_mechanism(
    cwd: Path, extra_env: dict[str, str] | None = None, *, use_the_fix: bool = True
) -> subprocess.CompletedProcess[str]:
    """As :func:`_run_import_via_real_mechanism`, but the wrapped Python
    body does nothing but ``print("started")`` -- exercising sitecustomize
    auto-execution at interpreter *startup*, independent of any import
    statement of ours."""
    py_snippet = 'print("started")\n'
    if use_the_fix:
        script = (
            '_PY_BIN="$(command -v python3 || command -v python || true)"\n'
            + _py_safe_dir_source()
            + '(cd "$_PY_SAFE_DIR" && PYTHONPATH= "$_PY_BIN" -c \''
            + py_snippet
            + "')\n"
        )
    else:
        script = (
            '_PY_BIN="$(command -v python3 || command -v python || true)"\n'
            + '"$_PY_BIN" -c \''
            + py_snippet
            + "'\n"
        )
    env = {**os.environ, **(extra_env or {})}
    return subprocess.run(
        [_bash_executable(), "-c", script],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=30,
    )


class TestPySafeDirPreventsCheckoutShadowing:
    """Running from a fresh, empty temp directory (not the untrusted
    checkout) means a malicious ``abicheck`` package placed in the
    checkout is never importable, regardless of how it's placed there."""

    def test_plain_cwd_shadowing_is_blocked(self, tmp_path: Path) -> None:
        _write_fake_abicheck_package(tmp_path)
        result = _run_import_via_real_mechanism(tmp_path)
        assert result.returncode == 0, result.stderr
        assert _MALICIOUS_MARKER not in result.stderr
        assert "['-DFOO=1']" in result.stdout

    def test_pythonpath_dot_shadowing_is_blocked(self, tmp_path: Path) -> None:
        # A workflow-configured PYTHONPATH=. (or any value at all) is
        # explicitly cleared for this one invocation -- it can never make
        # the checkout importable, regardless of what it resolves to.
        _write_fake_abicheck_package(tmp_path)
        result = _run_import_via_real_mechanism(tmp_path, extra_env={"PYTHONPATH": "."})
        assert result.returncode == 0, result.stderr
        assert _MALICIOUS_MARKER not in result.stderr
        assert "['-DFOO=1']" in result.stdout

    def test_pythonpath_descendant_shadowing_is_blocked(self, tmp_path: Path) -> None:
        # A common src-layout PYTHONPATH=src resolves to <checkout>/src --
        # also cleared, same as the bare "." case above.
        src = tmp_path / "src"
        src.mkdir()
        _write_fake_abicheck_package(src)
        result = _run_import_via_real_mechanism(
            tmp_path, extra_env={"PYTHONPATH": "src"}
        )
        assert result.returncode == 0, result.stderr
        assert _MALICIOUS_MARKER not in result.stderr
        assert "['-DFOO=1']" in result.stdout

    def test_without_the_fix_shadowing_actually_reproduces(
        self, tmp_path: Path
    ) -> None:
        """Proves the tests above aren't vacuously passing -- the identical
        import, invoked without $_PY_SAFE_DIR's directory/PYTHONPATH
        isolation, really does execute the fake package's code."""
        _write_fake_abicheck_package(tmp_path)
        result = _run_import_via_real_mechanism(tmp_path, use_the_fix=False)
        assert result.returncode != 0
        assert _MALICIOUS_MARKER in result.stderr


class TestPySafeDirPreventsSitecustomizeExecution:
    """``site.py`` auto-imports a discoverable ``sitecustomize.py`` during
    ordinary interpreter *startup*, before any script body runs -- so
    unlike an explicit ``import``, this can't be caught by filtering
    ``sys.path`` from inside the ``-c`` body after the fact (see this
    module's own docstring for the four-revision history). Running from an
    empty directory with ``PYTHONPATH`` cleared means there is nothing
    checkout-derived for that automatic startup processing to find in the
    first place.
    """

    def test_plain_cwd_sitecustomize_is_blocked(self, tmp_path: Path) -> None:
        _write_fake_sitecustomize(tmp_path)
        result = _run_sitecustomize_probe_via_real_mechanism(tmp_path)
        assert result.returncode == 0, result.stderr
        assert _MALICIOUS_MARKER not in result.stderr
        assert "started" in result.stdout

    def test_pythonpath_dot_sitecustomize_is_blocked(self, tmp_path: Path) -> None:
        _write_fake_sitecustomize(tmp_path)
        result = _run_sitecustomize_probe_via_real_mechanism(
            tmp_path, extra_env={"PYTHONPATH": "."}
        )
        assert result.returncode == 0, result.stderr
        assert _MALICIOUS_MARKER not in result.stderr
        assert "started" in result.stdout

    def test_pythonpath_descendant_sitecustomize_is_blocked(
        self, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        _write_fake_sitecustomize(src)
        result = _run_sitecustomize_probe_via_real_mechanism(
            tmp_path, extra_env={"PYTHONPATH": "src"}
        )
        assert result.returncode == 0, result.stderr
        assert _MALICIOUS_MARKER not in result.stderr
        assert "started" in result.stdout

    def test_without_the_fix_pythonpath_dot_sitecustomize_actually_reproduces(
        self, tmp_path: Path
    ) -> None:
        """Proves the tests above aren't vacuously passing -- the identical
        PYTHONPATH=. setup, invoked without $_PY_SAFE_DIR's isolation,
        really does let the checkout's sitecustomize.py execute at
        interpreter startup."""
        _write_fake_sitecustomize(tmp_path)
        result = _run_sitecustomize_probe_via_real_mechanism(
            tmp_path, extra_env={"PYTHONPATH": "."}, use_the_fix=False
        )
        assert _MALICIOUS_MARKER in result.stderr
