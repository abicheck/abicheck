#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Dump abicheck's public CLI surface (commands, options, arguments) as JSON.

Used by the `cli-interface-check` CI workflow (`.github/workflows/
cli-interface-check.yml`) to detect user-facing CLI changes in a PR: dumped
once for the PR's base ref and once for its head ref (each in its own venv
with that ref's abicheck installed — never both in the same interpreter, to
avoid one shadowing the other), then diffed with `diff_cli_surface.py`.

Deliberately relies only on whatever `abicheck` is importable in the current
Python environment (no sys.path self-insertion) so the same invocation dumps
whichever checkout's abicheck happens to be installed there — see the
workflow for how base/head are kept in separate venvs.

Usage:
    python scripts/dump_cli_surface.py [OUTPUT.json]

With no OUTPUT argument, prints to stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
    except TypeError:
        return repr(value)
    return value


def _param_shape(param: Any) -> dict[str, Any]:
    import click

    is_option = isinstance(param, click.Option)
    shape: dict[str, Any] = {
        "name": param.name,
        "kind": "option" if is_option else "argument",
        "required": bool(param.required),
    }
    if is_option:
        shape["opts"] = sorted(param.opts)
        shape["secondary_opts"] = sorted(param.secondary_opts)
        shape["is_flag"] = bool(getattr(param, "is_flag", False))
        shape["multiple"] = bool(getattr(param, "multiple", False))
        shape["hidden"] = bool(getattr(param, "hidden", False))
        shape["default"] = _jsonable(param.default)
        type_ = getattr(param, "type", None)
        shape["type"] = type(type_).__name__ if type_ is not None else None
        choices = getattr(type_, "choices", None)
        if choices:
            shape["choices"] = sorted(str(c) for c in choices)
    else:
        shape["opts"] = [param.name]
        shape["nargs"] = param.nargs
    return shape


def _command_shape(cmd: Any, path: str) -> dict[str, Any]:
    import click

    node: dict[str, Any] = {
        "path": path,
        "kind": "group" if isinstance(cmd, click.Group) else "command",
        "hidden": bool(getattr(cmd, "hidden", False)),
        "params": sorted(
            (_param_shape(p) for p in cmd.params),
            key=lambda p: (str(p["kind"]), str(p["name"])),
        ),
    }
    if isinstance(cmd, click.Group):
        node["subcommands"] = {
            name: _command_shape(sub, f"{path} {name}")
            for name, sub in sorted(cmd.commands.items())
        }
    return node


def dump_surface() -> dict[str, Any]:
    """Return the full command tree reachable from ``abicheck --help``."""
    from abicheck.cli import main

    return {
        name: _command_shape(cmd, name) for name, cmd in sorted(main.commands.items())
    }


def run(argv: list[str]) -> int:
    surface = dump_surface()
    text = json.dumps(surface, indent=2, sort_keys=True)
    if argv:
        Path(argv[0]).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
