# SPDX-License-Identifier: Apache-2.0
"""Surviving the *test temp tree* vanishing in the middle of a shelled-out step.

Every harness here follows the same three-beat shape: create an environment
file under the test's own ``tmp_path``, run a real ``bash`` step with
``$GITHUB_OUTPUT`` (or a sibling) pointing at it, then read back what the step
wrote.  Between beat one and beat three the file can simply cease to exist:

    tests/_action_run_sh_harness.py:163: in _run_action
        for line in out.read_text(encoding="utf-8").splitlines():
    E   FileNotFoundError: [Errno 2] No such file or directory:
        '/tmp/pytest-of-runner/pytest-0/popen-gw0/test_the_diagnostic_names_the_1/github_output'

That is not the step misbehaving.  ``action/run.sh`` only ever *appends* to
``$GITHUB_OUTPUT`` (``{ ... } >> "$GITHUB_OUTPUT"``), so a step that reached
its output block recreates the file rather than removing it, and every ``rm``
in it names a path it created itself under its own ``mktemp``.  What is left is
the environment removing part of pytest's temp tree while the step ran -- the
same class ``tests/conftest.py``'s ``_snapshot_cache_bucket`` already heals for
its own bucket, and for the same reason it is stated there over *what was
removed* rather than over one deleter: pytest's retention policy, a tmp reaper,
a sandbox cleanup and a stray ``rmtree`` all arrive here identically.

Recovery, not tolerance.  A vanished environment file means the step produced
*no* answer, so there is nothing to weaken: the tree is rebuilt (with the same
privacy guarantees pytest itself creates it with -- see
``conftest._recreate_private_tree``) and the step is run again from scratch.
Only a step that loses its output on **every** attempt fails, and it fails with
the state of the whole tree attached, so the next occurrence arrives diagnosed
instead of as one more bare ``FileNotFoundError``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import conftest

T = TypeVar("T")

#: How many times a step whose output file vanished is run again. One retry is
#: the whole budget on purpose: a second loss is no longer plausibly a passing
#: reaper, and turning this into "keep trying" would convert a real, repeatable
#: deletion by the step under test into an infinite quiet loop.
ATTEMPTS = 2


def _prepare(env_file: Path) -> None:
    """(Re)create *env_file*'s directory and the empty file itself."""
    conftest._recreate_private_tree(env_file.parent)
    env_file.write_bytes(b"")


def describe_tree(env_file: Path) -> str:
    """Which part of the tree above *env_file* is missing, outermost first.

    The answer is the diagnostic: "the file alone is gone" points at the step,
    while "its directory (or the worker basetemp) is gone too" points at the
    environment. A bare ``FileNotFoundError`` cannot tell those apart, which is
    why the observed failures could not be attributed from CI logs at all.
    """
    levels = [*reversed(env_file.parents), env_file]
    return ", ".join(
        f"{level}: {'present' if level.exists() else 'MISSING'}" for level in levels
    )


def run_writing_env_file(env_file: Path, run: Callable[[], T]) -> tuple[T, bytes]:
    """Run *run* with *env_file* freshly created, and return it with the bytes.

    *run* must be re-runnable: it is called again, against a rebuilt tree, if
    the file it was pointed at did not survive the call.
    """
    losses: list[str] = []
    for _ in range(ATTEMPTS):
        _prepare(env_file)
        result = run()
        try:
            return result, env_file.read_bytes()
        except FileNotFoundError:
            losses.append(describe_tree(env_file))
    raise AssertionError(
        f"{env_file} did not survive any of {ATTEMPTS} attempts at running the "
        "step that writes it, so the step's own output was never observable. "
        "Tree state after each attempt, outermost path first:\n  " + "\n  ".join(losses)
    )
