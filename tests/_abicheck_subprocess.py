# SPDX-License-Identifier: Apache-2.0
"""The one way a test spawns ``abicheck``: this interpreter, this checkout.

``abicheck`` on ``PATH`` is an installed console script, and a bare
``python -m abicheck`` run from a temporary directory imports whichever
``abicheck`` the interpreter has installed. Either can be a different tree from
the one under test (a git worktree, for instance), so a subprocess test silently
checks the wrong code. Every test that runs ``abicheck`` goes through
:func:`abicheck_command` and :func:`abicheck_env`, which pin ``PYTHONPATH`` to the
checkout this file lives in. ``tests/conftest.py`` pins it for the whole session
as well, so a subprocess this helper does not wrap still imports this tree.

``tests/test_abicheck_subprocess_routing.py`` rejects a literal ``"abicheck"``
argv anywhere else under ``tests/``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

#: The checkout this helper belongs to (``tests/`` is one level below it).
REPO_ROOT = Path(__file__).resolve().parents[1]


def abicheck_module_command(
    module: str, *args: str | os.PathLike[str]
) -> list[str]:
    """The argv that runs ``python -m <module>`` of this interpreter with *args*.

    For a package entry point other than the CLI (``abicheck.frontends.action.cli``).
    """
    return [sys.executable, "-m", module, *(str(a) for a in args)]


def abicheck_command(*args: str | os.PathLike[str]) -> list[str]:
    """The argv that runs this checkout's ``abicheck`` with *args*.

    ``sys.executable -m abicheck`` rather than the console script, so the
    interpreter and the package are the ones under test.
    """
    return abicheck_module_command("abicheck", *args)


def abicheck_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of ``os.environ`` whose ``PYTHONPATH`` puts :data:`REPO_ROOT` first.

    *extra* is merged last, so a caller's own variables win.
    """
    env = dict(os.environ)
    inherited = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(REPO_ROOT), inherited) if p)
    if extra:
        env.update(extra)
    return env


def pin_repo_on_path() -> None:
    """Make every subprocess of this session import :data:`REPO_ROOT`.

    Called once from ``tests/conftest.py``. The environment variable is inherited
    by any child interpreter, including an installed console script, so this
    covers subprocess sites that do not yet call :func:`abicheck_env`.
    """
    os.environ["PYTHONPATH"] = abicheck_env()["PYTHONPATH"]
