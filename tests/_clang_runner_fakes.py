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

"""Process fakes for the clang header pass's injected AST runner
(``clang_header_dump(..., run_ast=)`` / ``ClangBackend(runner=)``), shared by
``test_dumper_clang.py`` and ``test_clang_header_dump.py``. A non-``test_``
module so pytest never collects it directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _fake_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    class _P:
        pass

    p = _P()
    p.stdout = stdout
    p.stderr = stderr
    p.returncode = returncode
    return p


def _as_runner(run: Any) -> Any:
    """Adapt a ``run(cmd, stdout=<file>, ...)`` process fake to the clang
    header pass's injected runner contract (``run_ast=`` /
    ``ClangBackend(runner=...)``): spill stdout to a fresh temp file handed to
    ``on_created`` (the caller owns and unlinks it), and return a
    ``subprocess.CompletedProcess`` like the real runner."""
    import subprocess as _sp
    import tempfile as _tf

    def _runner(
        cmd: list[str], *, timeout: float, on_created: Any
    ) -> _sp.CompletedProcess[str]:
        fd, name = _tf.mkstemp(prefix="abicheck-test-ast-", suffix=".json")
        on_created(Path(name))
        with open(fd, "wb") as out:
            proc = run(cmd, stdout=out, stderr=_sp.PIPE, text=True, timeout=timeout)
        return _sp.CompletedProcess(cmd, proc.returncode, stdout="", stderr=proc.stderr)

    return _runner


def _write_stdout_file(kwargs: dict, text: str) -> None:
    """Write *text* to a process fake's ``stdout=<file>`` (``_as_runner`` or
    a mocked ``deadline.run_bounded(..., stdout=<file>)``)
    call's file object, mirroring what a real clang subprocess (its stdout
    redirected to a temp file by dumper.py's L2 streaming, see
    clang_header_dump._run_clang) would have written. A no-op if the mock
    wasn't invoked with a real file (e.g. a test that intentionally leaves the
    AST empty to exercise the "no AST" error path)."""
    fobj = kwargs.get("stdout")
    if fobj is not None:
        fobj.write(text.encode("utf-8"))
