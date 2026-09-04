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

"""Process-group kill machinery for :mod:`abicheck.service_scan`'s killable
subprocess harness (``run_scan_subprocess``/``run_scan_set_subprocess``,
ADR-035 / ADR-056).

Split out of :mod:`abicheck.service_scan` purely for that module's own
``no_growth`` line budget (ADR-063 Phase 4's residual, see
``docs/contribute/adr/063-one-semantic-pipeline.md``'s Phase 4 status entry
and `architecture/debt.yaml`'s matching entry for
``abicheck/service_scan.py``). Unlike the worker/harness functions that call
back into ``service_scan.run_scan``/``run_scan_set``, :func:`_descendant_pgids`
and :func:`_kill_process_tree` have zero dependency on anything in
``service_scan`` (or on anything scan-specific at all -- both operate on a
bare ``multiprocessing.Process`` handle) and moved with no import-direction
consequence, mirroring `cxx20_pair_dialect.py`'s own precedent: a genuine
one-directional edge, :mod:`abicheck.service_scan` imports these back for
re-export, not the mutual-dependency shape moving the *worker* functions
would have created.

Lives under :mod:`abicheck.workflows` rather than as a new flat
``service_``-prefixed root sibling (ADR-061's ``frozen_root_families`` closes
that family to new members -- `architecture/modules.yaml`) since it
coordinates `scan` behavior, the exact responsibility ADR-061's task-routing
table assigns to `workflows/`.
"""

from __future__ import annotations

from typing import Any


def _descendant_pgids(root_pid: int) -> set[int]:
    """Every process-group id in *root_pid*'s live process tree (POSIX,
    best-effort). A clang/castxml child spawned via ``deadline.run_bounded``
    detaches into its own session/group, so a plain ``killpg(root_pgid)``
    no longer reaches it even though the PPID chain still leads back to
    *root_pid*. Walking a live ``ps -eo pid,ppid`` snapshot finds every such
    descendant's own pgid too, so it can be killed alongside the worker's
    group instead of surviving as an orphan (Codex review, PR #591). Empty
    set on any failure (missing ``ps``, non-POSIX, ...) -- never raises.
    """
    import os
    import subprocess

    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,ppid="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    children: dict[int, list[int]] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
    pgids: set[int] = set()
    stack, seen = [root_pid], set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            pgids.add(os.getpgid(pid))
        except (ProcessLookupError, PermissionError, AttributeError, OSError):
            pass
        stack.extend(children.get(pid, []))
    return pgids


def _kill_process_tree(proc: Any) -> None:
    """Terminate *proc* and every group in its process tree (best-effort,
    never raises). Killing only ``proc``'s own group used to miss a clang/
    castxml child detached into its own session via ``deadline.run_bounded``
    (needed so *its* inner-timeout ``killpg`` can't self-kill the worker,
    but also made it invisible to this outer ``killpg``).
    :func:`_descendant_pgids` finds it by PPID while ``proc`` is alive, so it
    gets killed here too instead of surviving as an orphan (Codex review,
    PR #591).
    """
    import os
    import signal

    if not proc.is_alive():
        return
    try:
        own_pgid = os.getpgrp()
    except (AttributeError, OSError):
        try:
            proc.terminate()
        except (OSError, AttributeError):
            pass
        proc.join(5)
        return
    pgids: set[int] = set()
    try:
        pgids.add(os.getpgid(proc.pid))
    except (ProcessLookupError, PermissionError, AttributeError, OSError):
        pass
    pgids |= _descendant_pgids(proc.pid)
    # Only kill *proc*'s own group when it actually detached into its own (``os. setsid`` ran). If it timed out
    # before that, its pgid still equals the parent's group — killpg would then terminate the MCP server itself, so
    # that group is excluded and only its (already-detached) descendants, if any, are targeted (Codex review).
    pgids.discard(own_pgid)
    # Unconditional: proc itself never detached (own_pgid was excluded above precisely because it's still in the
    # parent's group), so it is never reached by the killpg sweep below over *descendant* groups. Skipping this when
    # pgids is non-empty left the direct worker process running forever whenever it had spawned a detached
    # clang/castxml child but had not itself detached (CodeRabbit review, PR #591).
    try:
        proc.terminate()
    except (OSError, AttributeError):
        pass
    if not pgids:
        proc.join(5)
        return
    for pgid in pgids:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    proc.join(3)
    # Unconditional SIGKILL sweep, not gated on proc's own exit: a detached
    # clang/castxml session or a SIGTERM-ignoring grandchild can outlive the
    # grace period even once the direct worker has already exited (mirrors
    # deadline._kill_process_tree's same escalation fix).
    for pgid in pgids:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    proc.join(5)
