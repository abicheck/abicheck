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

"""``$_PY_SAFE_PATH`` (``action/run.sh``): the checkout-shadowing prevention
snippet every inline Python invocation that imports a real ``abicheck``
module is prefixed with (Codex review, PR #774).

``python -c``/``-m`` insert the current working directory into
``sys.path``, so on a ``pull_request``-triggered workflow -- where the
checkout is the PR author's own, untrusted code -- a PR that added a
same-named module (e.g. its own ``abicheck/_compiler_options.py``) could
make one of run.sh's inline scripts import and execute that code instead
of the real, pip-installed package.

These tests exercise the actual runtime property (a real subprocess,
started with its CWD set to a directory containing a fake, "malicious"
``abicheck`` package) rather than just parsing ``run.sh``'s source text --
the two real attack shapes a review round found, one after the other, both
verified to be blocked (and, for the first fix's own gap, first verified
to reproduce against the *unfixed* snippet before trusting the second
fix): the plain empty-string CWD entry ``python -c`` inserts, and a
``PYTHONPATH=.``-style entry, which Python resolves to the checkout's own
absolute path *before* this snippet ever runs -- not the literal string
``"."`` -- so an earlier revision's ``p not in ("", ".")`` string-equality
filter left that second shape open.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

_PY_SAFE_PATH_START = "_PY_SAFE_PATH='"
_PY_SAFE_PATH_END = "'\n"

_MALICIOUS_MARKER = "MALICIOUS CODE EXECUTED"


def _py_safe_path_source() -> str:
    """Extract ``$_PY_SAFE_PATH``'s own Python body verbatim (the part
    between its single-quote bash delimiters) -- not the whole
    ``_PY_SAFE_PATH='...'`` assignment, since this module runs it directly
    with ``python3 -c``, not through bash."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_PY_SAFE_PATH_START) + len(_PY_SAFE_PATH_START)
    end = text.index(_PY_SAFE_PATH_END, start)
    return text[start:end]


def _write_fake_abicheck_package(root: Path) -> None:
    pkg = root / "abicheck"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "_compiler_options.py").write_text(
        f'def split_gcc_options(text):\n    raise RuntimeError("{_MALICIOUS_MARKER}")\n'
    )


def _run_import_under_safe_path(
    cwd: Path, extra_env: dict[str, str] | None = None, *, use_dash_s: bool = True
) -> subprocess.CompletedProcess[str]:
    script = (
        _py_safe_path_source()
        + "from abicheck._compiler_options import split_gcc_options\n"
        "print(split_gcc_options('-DFOO=1'))\n"
    )
    env = {**os.environ, **(extra_env or {})}
    argv = [sys.executable, *(["-S"] if use_dash_s else []), "-c", script]
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=30,
    )


def _write_fake_sitecustomize(root: Path) -> None:
    (root / "sitecustomize.py").write_text(
        f'import sys\nprint("{_MALICIOUS_MARKER}", file=sys.stderr)\n'
    )


class TestPySafePathPreventsCheckoutShadowing:
    def test_plain_cwd_shadowing_is_blocked(self, tmp_path: Path) -> None:
        # The base case: python -c's own implicit '' sys.path[0] entry,
        # with no PYTHONPATH involved at all.
        _write_fake_abicheck_package(tmp_path)
        result = _run_import_under_safe_path(tmp_path)
        assert result.returncode == 0, result.stderr
        assert _MALICIOUS_MARKER not in result.stderr
        assert "['-DFOO=1']" in result.stdout

    def test_pythonpath_dot_shadowing_is_blocked(self, tmp_path: Path) -> None:
        # Codex review, second round on this same fix: PYTHONPATH=. is
        # resolved by Python into the checkout's own absolute path before
        # this snippet ever runs (confirmed directly:
        # `PYTHONPATH=. python3 -c "import sys; print(sys.path)"` puts the
        # resolved absolute CWD in sys.path, never the literal string ".")
        # -- a filter checking for "" or "." by string equality alone never
        # matches it, so a p != cwd resolved-path comparison is required.
        _write_fake_abicheck_package(tmp_path)
        result = _run_import_under_safe_path(tmp_path, extra_env={"PYTHONPATH": "."})
        assert result.returncode == 0, result.stderr
        assert _MALICIOUS_MARKER not in result.stderr
        assert "['-DFOO=1']" in result.stdout

    def test_without_the_fix_shadowing_actually_reproduces(
        self, tmp_path: Path
    ) -> None:
        """Proves the two tests above aren't vacuously passing (e.g. because
        the real abicheck._compiler_options was importable some other way
        regardless of CWD) -- the identical import, with no sys.path
        filtering applied at all, really does execute the fake package's
        code."""
        _write_fake_abicheck_package(tmp_path)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from abicheck._compiler_options import split_gcc_options\n"
                "print(split_gcc_options('-DFOO=1'))\n",
            ],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=dict(os.environ),
            timeout=30,
        )
        assert result.returncode != 0
        assert _MALICIOUS_MARKER in result.stderr


class TestPySafePathPreventsSitecustomizeExecution:
    """Codex review, third round on this same fix: ``site.py`` auto-imports
    a ``sitecustomize.py`` it finds on ``sys.path`` as part of ordinary
    interpreter *startup* -- before a single line of ``$_PY_SAFE_PATH``'s
    own ``-c`` script body ever runs, since that startup processing happens
    before the script body is executed at all. A checked-out PR's own
    top-level ``sitecustomize.py`` therefore ran regardless of the filter,
    with no import statement of ours even involved. Every real
    ``"$_PY_BIN" -c "$_PY_SAFE_PATH"...`` call site in ``run.sh`` now also
    passes ``-S`` (skip automatic site processing, including
    sitecustomize/usercustomize) so nothing checkout-derived can execute
    before the filter has a chance to run; ``$_PY_SAFE_PATH`` itself then
    calls ``site.main()`` manually, once the checkout is already gone from
    ``sys.path``, to restore normal site-packages access for the real
    ``import abicheck...`` that follows.

    The control test below deliberately reproduces via ``PYTHONPATH=.``
    (matching the reviewer's own reported scenario -- "a calling workflow
    has PYTHONPATH=."), not via a bare CWD entry: empirically, on the
    Python build this test suite actually runs under, the *implicit*
    startup ``site.main()`` call resolves its own ``sys.path`` snapshot
    before the ``-c`` command's own ``""`` (CWD) entry is appended to it,
    so a bare-CWD ``sitecustomize.py`` with no ``PYTHONPATH`` set never
    reliably wins the module-cache race against this build's own
    ``/usr/lib/python3*/sitecustomize.py`` stub during that implicit call
    (verified directly: even an explicit, later ``import sitecustomize``
    from *inside* the running script -- well after "" is confirmed present
    in ``sys.path`` -- still resolves to the cached stdlib stub, not a
    same-named file placed in the CWD). A ``PYTHONPATH`` entry, by
    contrast, is part of ``sys.path`` from before interpreter startup even
    begins, so it reliably wins that race and reproduces the vulnerability
    -- this is also the literal scenario named in the finding. The fix
    itself (``-S`` before site ever runs) is not specific to either
    vector; it is verified here against the one that actually reproduces
    in this environment.
    """

    def test_sitecustomize_is_not_executed_with_the_real_fix(
        self, tmp_path: Path
    ) -> None:
        _write_fake_sitecustomize(tmp_path)
        result = _run_import_under_safe_path(tmp_path, use_dash_s=True)
        assert result.returncode == 0, result.stderr
        assert _MALICIOUS_MARKER not in result.stderr

    def test_sitecustomize_is_not_executed_with_pythonpath_dot(
        self, tmp_path: Path
    ) -> None:
        _write_fake_sitecustomize(tmp_path)
        result = _run_import_under_safe_path(
            tmp_path, extra_env={"PYTHONPATH": "."}, use_dash_s=True
        )
        assert result.returncode == 0, result.stderr
        assert _MALICIOUS_MARKER not in result.stderr

    def test_without_dash_s_pythonpath_dot_sitecustomize_actually_reproduces(
        self, tmp_path: Path
    ) -> None:
        """Proves the two tests above aren't vacuously passing -- the
        identical filter script and PYTHONPATH=. setup, minus only the
        real fix's own ``-S`` flag, really does let the checkout's
        sitecustomize.py run before the filter gets a chance to matter."""
        _write_fake_sitecustomize(tmp_path)
        result = _run_import_under_safe_path(
            tmp_path, extra_env={"PYTHONPATH": "."}, use_dash_s=False
        )
        assert _MALICIOUS_MARKER in result.stderr
