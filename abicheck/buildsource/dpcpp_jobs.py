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

"""What a DPC++ driver will actually run for a host-only SYCL request.

``-fsycl -fsycl-host-only`` is documented as one host ``-cc1``, and the clang
header-AST path relies on that to stream a single document. ``icpx``
2026.1.1 (conda-forge ``dpcpp_linux-64``) still runs the ``spir64`` device
``-cc1`` beside it -- the device pass writes the integration header/footer the
host pass includes -- so its AST stream holds two documents. This module asks
the driver (``-###``) and, for exactly that device + host plan, replays the
two jobs itself with the device AST dump removed, so the request still yields
one host document. Lives beside the other compiler probes (``toolchain_probe``) rather than in
``dumper_clang.py``, which is at its line-count ceiling.
"""

from __future__ import annotations

from pathlib import Path

from ..model.execution_cache import memoized

__all__ = ["executable_revision", "host_only_request_usable", "sycl_host_replay_jobs"]


def executable_revision(clang_bin: str) -> str:
    """A memo key that changes when the executable *clang_bin* resolves to
    is replaced: its resolved path, size, inode and modification time."""
    import os
    import shutil

    path = os.path.realpath(shutil.which(clang_bin) or clang_bin)
    try:
        st = os.stat(path)
    except OSError:
        return f"{path}:unavailable"
    return f"{path}:{st.st_size}:{st.st_ino}:{st.st_mtime_ns}"


def host_only_request_usable(
    clang_bin: str, identity: str, tokens: tuple[str, ...]
) -> bool:
    """Whether a ``-fsycl -fsycl-host-only`` request yields one host AST
    document -- natively, or through :func:`sycl_host_replay_jobs`. Anything
    else the driver plans is left to the multi-document selector. Memoized
    per executable revision (*identity*, the caller's content identity of
    *clang_bin*)."""
    return _host_only_single_pass(clang_bin, identity, tokens)


@memoized(maxsize=64)
def _host_only_single_pass(
    clang_bin: str, identity: str, tokens: tuple[str, ...]
) -> bool:
    del identity  # keys the memo on the executable's content
    cmd = [clang_bin, *tokens, "-fsycl", "-fsycl-host-only"]
    cmd += ["-fsyntax-only", "-x", "c++", "-"]
    jobs = _driver_jobs(cmd)
    if jobs is None:
        return True  # cannot ask: keep the documented host-only behaviour
    return _split_device_host(jobs) is not None or not any(
        "-fsycl-is-device" in job for job in jobs
    )


def _driver_jobs(cmd: list[str]) -> list[list[str]] | None:
    """The jobs *cmd* would run (``-###``), each as an argv; ``None`` when
    the driver cannot be asked."""
    import shlex
    import subprocess

    from ..deadline import run_bounded

    try:
        proc = run_bounded(
            [*cmd, "-###"], timeout=30, capture_output=True, text=True, input=""
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return [
        shlex.split(line) for line in proc.stderr.splitlines() if line.startswith(' "')
    ]


def _split_device_host(
    jobs: list[list[str]],
) -> tuple[list[str], list[str]] | None:
    """``(device, host)`` when *jobs* are exactly one SYCL device and one
    host ``-cc1``, else ``None``."""
    device = [j for j in jobs if "-fsycl-is-device" in j]
    host = [j for j in jobs if "-fsycl-is-host" in j]
    if len(jobs) != 2 or len(device) != 1 or len(host) != 1:
        return None
    return device[0], host[0]


def sycl_host_replay_jobs(
    cmd: list[str], scratch: Path
) -> tuple[list[str], list[str]] | None:
    """The two jobs a host-only DPC++ request runs, ready to execute: the
    device ``-cc1`` without its AST dump (it is still needed for the
    integration header/footer the host pass includes) and the host ``-cc1``
    unchanged, both writing those files under *scratch* instead of a driver
    temp directory ``-###`` only names. ``None`` when the request is not a
    two-pass device+host plan (the ordinary single-pass case included).

    Serves the direct host request on a driver whose ``-fsycl-host-only``
    still runs a device pass: one host document instead of a ~2x stream the
    multi-document selector would scan, and the streaming AST path applies.
    """
    if "-fsycl-host-only" not in cmd:
        return None
    jobs = _driver_jobs(cmd)
    split = _split_device_host(jobs) if jobs is not None else None
    if split is None:
        return None
    device, host = split
    temp_dirs = {
        str(Path(tok.split("=", 1)[1]).parent)
        for tok in device
        if tok.startswith(("-fsycl-int-header=", "-fsycl-int-footer="))
    }

    def relocate(job: list[str]) -> list[str]:
        out = []
        for tok in job:
            for d in temp_dirs:
                tok = tok.replace(d, str(scratch))
            out.append(tok)
        return out

    return (
        [t for t in relocate(device) if t != "-ast-dump=json"],
        relocate(host),
    )
