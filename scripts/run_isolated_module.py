#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Run an installed module without allowing the checkout to shadow it."""

from __future__ import annotations

import os
import runpy
import site
import sys
from pathlib import Path

RUNNER = Path(__file__).resolve()


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _restore_user_site() -> None:
    """Add the user site ahead of system packages, as normal startup does."""
    before = {_path_key(path) for path in sys.path}
    system_sites = {_path_key(path) for path in site.getsitepackages()}
    insertion = next(
        (i for i, path in enumerate(sys.path) if _path_key(path) in system_sites),
        len(sys.path),
    )

    # addsitedir also processes .pth files. It appends their paths because -I
    # has already populated the system sites, so move every newly-added entry
    # back to the position normal site initialisation gives the user site.
    site.addsitedir(site.getusersitepackages())
    added = [path for path in sys.path if _path_key(path) not in before]
    if not added:
        return
    sys.path[:] = [path for path in sys.path if _path_key(path) in before]
    sys.path[insertion:insertion] = added


def command(*mod_args: str) -> tuple[str, ...]:
    """Return an isolated command for an installed module and its arguments."""
    return (sys.executable, "-I", str(RUNNER), *mod_args)


def _normally_enables_user_site() -> bool:
    """Return whether normal startup enables the user site for this venv."""
    if sys.prefix == sys.base_prefix:
        return True
    try:
        config = Path(sys.prefix, "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError:
        return False
    return any(
        line.strip().lower() == "include-system-site-packages = true"
        for line in config.splitlines()
    )


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: run_isolated_module.py MODULE [ARG ...]")

    # The parent starts us with -I, which prevents the checkout/current working
    # directory from entering sys.path but also disables the user site. Restore
    # it only where normal startup would enable it: a base interpreter or a
    # --system-site-packages venv. Honour an explicit user-site opt-out.
    if "PYTHONNOUSERSITE" not in os.environ and _normally_enables_user_site():
        _restore_user_site()

    module = sys.argv.pop(1)
    runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
