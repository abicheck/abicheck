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
import tempfile
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

_PY_SAFE_DIR_START = 'if ! _PY_SAFE_DIR="$(mktemp -d)"; then'
_PY_SAFE_DIR_END = "\ntrap 'rm -rf \"$_PY_SAFE_DIR\"' EXIT\n"

_MALICIOUS_MARKER = "MALICIOUS CODE EXECUTED"

_PY_BIN_RESOLUTION_START = '_PY_BIN="$(command -v python3 || command -v python || true)"'
_PY_BIN_RESOLUTION_END = "\nesac\n"


def _py_safe_dir_source() -> str:
    """Extract the real ``_PY_SAFE_DIR=...`` assignment verbatim from
    run.sh -- the whole fail-loud if/fi block, not a single line (a
    ``mktemp -d`` failure exits the Action rather than falling back to a
    shared, non-private directory)."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_PY_SAFE_DIR_START)
    end = text.index(_PY_SAFE_DIR_END, start) + len(_PY_SAFE_DIR_END)
    return text[start:end]


def _py_bin_resolution_source() -> str:
    """Extract the real ``$_PY_BIN`` resolution verbatim -- the
    ``command -v`` lookup plus its immediately-following absolute-path
    canonicalization ``case`` block (Codex review, fresh evidence: a
    relative PATH entry can make ``command -v`` return a path relative to
    the CWD, which every ``(cd "$_PY_SAFE_DIR" && ... "$_PY_BIN" ...)``
    invocation below would then resolve against the wrong directory)."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_PY_BIN_RESOLUTION_START)
    end = text.index(_PY_BIN_RESOLUTION_END, start) + len(_PY_BIN_RESOLUTION_END)
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


def _run_bash_script(
    script: str,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``script`` via a real bash, from a temp file rather than an
    inline ``-c`` argument -- see ``test_action_run_sh_helpers._run_harness``
    for the full rationale (Windows argv-reconstruction/console-encoding
    mangling of a complex inline script with many nested quotes; confirmed
    on windows-latest CI for this module's own scripts)."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        return subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env,
            timeout=timeout,
        )
    finally:
        os.unlink(script_path)


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
    return _run_bash_script(script, cwd=cwd, env=env, timeout=30)


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
    return _run_bash_script(script, cwd=cwd, env=env, timeout=30)


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


class TestPySafeDirFailsClosedWhenMktempFails:
    """Codex review, fresh evidence: falling back to a pre-existing shared
    directory (e.g. bare ``${TMPDIR:-/tmp}``) when ``mktemp -d`` fails would
    silently reintroduce the exact risk ``$_PY_SAFE_DIR`` exists to close --
    that directory is neither guaranteed empty nor private on a constrained
    or shared self-hosted runner. run.sh now fails the Action outright
    (``exit 1``) instead.
    """

    def test_mktemp_failure_exits_nonzero_with_a_clear_error(
        self, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        fake_mktemp = fake_bin / "mktemp"
        fake_mktemp.write_text("#!/bin/bash\nexit 1\n")
        fake_mktemp.chmod(0o755)

        script = _py_safe_dir_source() + 'echo "UNREACHABLE: $_PY_SAFE_DIR"\n'
        env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}
        result = _run_bash_script(script, env=env, timeout=30)
        assert result.returncode == 1
        assert "UNREACHABLE" not in result.stdout
        assert "::error::" in result.stdout
        assert "mktemp" in result.stdout

    def test_mktemp_success_is_unaffected(self, tmp_path: Path) -> None:
        """Proves the test above isn't vacuously passing (e.g. because the
        real if/fi block is malformed regardless of mktemp's outcome) --
        the identical block, with a real working mktemp, still resolves
        $_PY_SAFE_DIR normally."""
        script = _py_safe_dir_source() + 'echo "DIR: $_PY_SAFE_DIR"\n'
        result = _run_bash_script(script, env=dict(os.environ), timeout=30)
        assert result.returncode == 0, result.stderr
        assert "DIR: " in result.stdout
        assert "UNREACHABLE" not in result.stdout


class TestPyBinHasAbicheckFallback:
    """Codex review, fresh evidence: a self-hosted runner can expose
    ``pip``/``abicheck`` from one Python environment while ``command -v
    python3`` resolves a *different* one (e.g. a system Python ahead of a
    pyenv shim on PATH) -- without this check, ``add_flag_shlex_split``
    would invoke a ``$_PY_BIN`` that can't import ``abicheck`` and silently
    drop every requested ``--gcc-options`` token (no ``set -e`` in this
    script), rather than falling back to plain whitespace splitting the
    way it already does when no Python interpreter is on PATH at all.
    """

    @staticmethod
    def _py_bin_has_abicheck_source() -> str:
        text = RUN_SH.read_text(encoding="utf-8")
        start = text.index('_PY_BIN_HAS_ABICHECK="false"')
        end = text.index("\nfi\n", start) + len("\nfi\n")
        return text[start:end]

    def test_interpreter_without_abicheck_falls_back_to_whitespace_split(
        self, tmp_path: Path
    ) -> None:
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        # A python3 stand-in that can run -c scripts but genuinely cannot
        # import abicheck -- reproducing "a different interpreter than the
        # one abicheck was pip-installed into" without needing a second
        # real Python installation.
        fake_python3 = fake_bin / "python3"
        fake_python3.write_text(
            '#!/bin/bash\nexec "$(command -v python3.11 || command -v python3)" '
            '-S -c \'raise SystemExit("no abicheck here")\' "$@"\n'
        )
        fake_python3.chmod(0o755)
        script = (
            '_PY_BIN="'
            + str(fake_python3)
            + '"\n'
            + self._py_bin_has_abicheck_source()
            + 'echo "HAS_ABICHECK=$_PY_BIN_HAS_ABICHECK"\n'
        )
        result = _run_bash_script(script, timeout=30)
        assert result.returncode == 0, result.stderr
        assert "HAS_ABICHECK=false" in result.stdout
        assert "::warning::" in result.stdout
        assert "cannot import abicheck" in result.stdout

    def test_real_interpreter_has_abicheck(self) -> None:
        """Proves the test above isn't vacuously passing -- the identical
        check, run against the real, ambient python3 (which does have
        abicheck importable in this test environment), resolves true."""
        script = (
            '_PY_BIN="$(command -v python3 || command -v python || true)"\n'
            + self._py_bin_has_abicheck_source()
            + 'echo "HAS_ABICHECK=$_PY_BIN_HAS_ABICHECK"\n'
        )
        result = _run_bash_script(script, timeout=30)
        assert result.returncode == 0, result.stderr
        assert "HAS_ABICHECK=true" in result.stdout


class TestPySafeDirCleanedUpOnEarlyExit:
    """Codex review, fresh evidence: $_PY_SAFE_DIR's own creation is
    followed immediately by a ``trap ... EXIT`` covering just it -- not
    left to the script's main cleanup trap, installed much further down.
    An early exit (argument validation, a no-baseline dry-run success, any
    error path before that later trap installs) would otherwise leave the
    private temporary directory behind, accumulating across repeated
    invocations on a persistent self-hosted runner. A later, real
    ``trap ... EXIT`` (this script's main one) replaces this handler
    outright rather than chaining it, which is fine: that later trap
    already covers ``$_PY_SAFE_DIR`` too.
    """

    def test_directory_is_removed_on_an_early_exit(self, tmp_path: Path) -> None:
        script = _py_safe_dir_source() + 'echo "DIR=$_PY_SAFE_DIR"\nexit 0\n'
        result = _run_bash_script(script, timeout=30)
        assert result.returncode == 0, result.stderr
        line = next(ln for ln in result.stdout.splitlines() if ln.startswith("DIR="))
        created_dir = line[len("DIR=") :]
        assert not Path(created_dir).exists()

    def test_without_the_early_trap_the_directory_actually_leaks(
        self, tmp_path: Path
    ) -> None:
        """Proves the test above isn't vacuously passing -- the identical
        mktemp call, minus only the trap, really does leave the directory
        behind after the process exits."""
        script = (
            'if ! _PY_SAFE_DIR="$(mktemp -d)"; then exit 1; fi\n'
            'echo "DIR=$_PY_SAFE_DIR"\nexit 0\n'
        )
        result = _run_bash_script(script, timeout=30)
        assert result.returncode == 0, result.stderr
        line = next(ln for ln in result.stdout.splitlines() if ln.startswith("DIR="))
        created_dir = line[len("DIR=") :]
        try:
            assert Path(created_dir).exists()
        finally:
            subprocess.run(["rm", "-rf", created_dir], timeout=10)


class TestPyBinResolvedAsAbsolute:
    """Codex review, fresh evidence: a self-hosted runner with a relative
    ``PATH`` entry (e.g. ``PATH=tools:$PATH``) makes ``command -v python3``
    return a path relative to the CWD -- every inline Python invocation runs
    as ``(cd "$_PY_SAFE_DIR" && ... "$_PY_BIN" ...)``, so an uncanonicalized
    ``$_PY_BIN`` would resolve against the wrong directory after that ``cd``,
    making a genuinely working, abicheck-capable interpreter falsely
    unusable."""

    def test_relative_path_interpreter_is_anchored_to_the_original_cwd(
        self, tmp_path: Path
    ) -> None:
        tools = tmp_path / "tools"
        tools.mkdir()
        fake_python3 = tools / "python3"
        fake_python3.write_text('#!/bin/bash\necho "ARGS: $@"\n')
        fake_python3.chmod(0o755)

        script = _py_bin_resolution_source() + 'echo "PY_BIN=$_PY_BIN"\n'
        env = {**os.environ, "PATH": f"tools{os.pathsep}{os.environ.get('PATH', '')}"}
        result = _run_bash_script(script, env=env, cwd=tmp_path, timeout=30)
        assert result.returncode == 0, result.stderr
        line = next(
            ln for ln in result.stdout.splitlines() if ln.startswith("PY_BIN=")
        )
        py_bin = line[len("PY_BIN=") :]
        assert Path(py_bin).is_absolute()
        assert Path(py_bin) == fake_python3

    def test_without_the_fix_relative_path_actually_reproduces(
        self, tmp_path: Path
    ) -> None:
        """Proves the test above isn't vacuously passing -- the identical
        ``command -v`` resolution, minus only the canonicalization ``case``
        block, really does leave a relative path in ``$_PY_BIN`` when
        ``PATH`` itself holds a relative entry."""
        tools = tmp_path / "tools"
        tools.mkdir()
        fake_python3 = tools / "python3"
        fake_python3.write_text('#!/bin/bash\necho "ARGS: $@"\n')
        fake_python3.chmod(0o755)

        script = (
            '_PY_BIN="$(command -v python3 || command -v python || true)"\n'
            'echo "PY_BIN=$_PY_BIN"\n'
        )
        env = {**os.environ, "PATH": f"tools{os.pathsep}{os.environ.get('PATH', '')}"}
        result = _run_bash_script(script, env=env, cwd=tmp_path, timeout=30)
        assert result.returncode == 0, result.stderr
        line = next(
            ln for ln in result.stdout.splitlines() if ln.startswith("PY_BIN=")
        )
        py_bin = line[len("PY_BIN=") :]
        assert not Path(py_bin).is_absolute()
