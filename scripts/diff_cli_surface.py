#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Diff two CLI-surface JSON dumps (see `dump_cli_surface.py`) and report changes.

Used by the `cli-interface-check` CI workflow to make it unmissable when a PR
changes abicheck's user-facing CLI surface (a new/removed command, a new/
removed/renamed option, a changed default, a flag gaining/losing a required
argument, etc.).

Usage:
    python scripts/diff_cli_surface.py BASE.json HEAD.json [--markdown]

Exit codes:
    0 = identical surface (no user-facing CLI change)
    1 = the surface differs (see printed report)

With `--markdown` the report is formatted for a PR comment; otherwise it is a
plain-text report for local/terminal use.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _load(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _param_key(param: dict[str, Any]) -> str:
    opts = param.get("opts") or [param.get("name", "?")]
    return "/".join(sorted(str(o) for o in opts))


def _flatten_commands(surface: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Flatten the (possibly nested) command tree into {path: node}."""
    flat: dict[str, dict[str, Any]] = {}

    def walk(node: dict[str, Any]) -> None:
        flat[node["path"]] = node
        for sub in node.get("subcommands", {}).values():
            walk(sub)

    for node in surface.values():
        walk(node)
    return flat


def _diff_params(
    old_params: list[dict[str, Any]], new_params: list[dict[str, Any]]
) -> list[str]:
    old_by_key = {_param_key(p): p for p in old_params}
    new_by_key = {_param_key(p): p for p in new_params}
    lines: list[str] = []
    for key in sorted(set(old_by_key) - set(new_by_key)):
        lines.append(f"    - removed {old_by_key[key]['kind']} `{key}`")
    for key in sorted(set(new_by_key) - set(old_by_key)):
        lines.append(f"    + added {new_by_key[key]['kind']} `{key}`")
    for key in sorted(set(old_by_key) & set(new_by_key)):
        old_p, new_p = old_by_key[key], new_by_key[key]
        changed = [
            f"{field}: {old_p.get(field)!r} -> {new_p.get(field)!r}"
            for field in (
                "required",
                "is_flag",
                "multiple",
                "hidden",
                "default",
                "type",
                "choices",
                "nargs",
            )
            if old_p.get(field) != new_p.get(field)
        ]
        if changed:
            lines.append(f"    ~ changed `{key}`: " + "; ".join(changed))
    return lines


def diff_surfaces(base: dict[str, Any], head: dict[str, Any]) -> list[str]:
    """Return a list of human-readable diff lines; empty means identical."""
    base_flat = _flatten_commands(base)
    head_flat = _flatten_commands(head)
    lines: list[str] = []

    for path in sorted(set(base_flat) - set(head_flat)):
        lines.append(f"- removed command `{path}`")
    for path in sorted(set(head_flat) - set(base_flat)):
        lines.append(f"+ added command `{path}`")
    for path in sorted(set(base_flat) & set(head_flat)):
        old_node, new_node = base_flat[path], head_flat[path]
        if old_node.get("hidden") != new_node.get("hidden"):
            lines.append(
                f"~ `{path}` hidden: {old_node.get('hidden')!r} -> "
                f"{new_node.get('hidden')!r}"
            )
        param_lines = _diff_params(old_node.get("params", []), new_node.get("params", []))
        if param_lines:
            lines.append(f"~ `{path}` options changed:")
            lines.extend(param_lines)
    return lines


def render_report(lines: list[str], *, markdown: bool) -> str:
    if not lines:
        return "No user-facing CLI surface changes detected."
    header = (
        "## CLI interface change detected\n\n"
        "This PR changes abicheck's user-facing CLI surface "
        "(commands and/or options):\n"
        if markdown
        else "CLI interface change detected:\n"
    )
    body = "\n".join(lines)
    return f"{header}\n{body}\n" if markdown else f"{header}{body}\n"


def run(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    markdown = "--markdown" in argv
    if len(args) != 2:
        print(
            "usage: diff_cli_surface.py BASE.json HEAD.json [--markdown]",
            file=sys.stderr,
        )
        return 64
    base, head = _load(args[0]), _load(args[1])
    lines = diff_surfaces(base, head)
    print(render_report(lines, markdown=markdown))
    return 1 if lines else 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
