#!/usr/bin/env python3
"""Inventory every user-settable option on abicheck's four surfaces (ADR-091 Phase 0).

Surfaces read live, never transcribed:

- ``cli``     -- every option of every command in the click tree (hidden ones too);
- ``config``  -- every ``.abicheck.yml`` key in ``build_config.KEY_TABLE``;
- ``action``  -- every input of ``action.yml`` and ``actions/*/action.yml``;
- ``python``  -- the fields of ``CompareRequest``/``DumpRequest``/``InputSpec``
  and the keyword parameters of ``run_compare``/``run_dump``.

Writes ``architecture/option-vocabulary-inventory.json``. ``--check`` fails when
the committed file is stale. ``architecture/option-vocabulary.yaml`` groups these
rows into concepts; ``tests/test_option_vocabulary.py`` checks that every row is
claimed by exactly one concept.
"""

from __future__ import annotations

import argparse
import dataclasses
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_DIR = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_DIR / "architecture" / "option-vocabulary-inventory.json"

#: Click's own options, not abicheck options.
_CLI_SKIP = frozenset({"--help", "--help-all"})


def _cli_rows() -> list[dict[str, Any]]:
    import click

    from abicheck.cli import main

    rows: list[dict[str, Any]] = []

    def walk(cmd: click.Command, path: list[str]) -> None:
        if isinstance(cmd, click.Group):
            for name in sorted(cmd.commands):
                walk(cmd.commands[name], [*path, name])
            return
        for p in cmd.params:
            if not isinstance(p, click.Option):
                continue
            longs = [o for o in p.opts + p.secondary_opts if o.startswith("--")]
            if not longs or longs[0] in _CLI_SKIP:
                continue
            rows.append(
                {
                    "id": f"{' '.join(path)} {longs[0]}",
                    "command": " ".join(path),
                    "option": longs[0],
                    "aliases": sorted(set(p.opts + p.secondary_opts) - {longs[0]}),
                    "hidden": bool(p.hidden),
                    "multiple": bool(p.multiple),
                    "is_flag": bool(p.is_flag),
                }
            )

    walk(main, [])
    return rows


def _config_rows() -> list[dict[str, Any]]:
    from abicheck.buildsource.build_config import KEY_TABLE

    return [
        {"id": spec.path, "kind": str(getattr(spec.kind, "name", spec.kind)).lower()}
        for spec in KEY_TABLE.specs
    ]


def _input_status(description: str) -> str:
    """Classify an Action input by its own description.

    ``retired`` uses the rule ``tests/test_action_retired_inputs_contract.py``
    owns ("RETIRED" anywhere in the description): a fail-closed tombstone that
    stays declared. ``ignored`` is an input that still parses but does nothing
    ("... and ignored."); ``deprecated`` still works under an old name.
    """
    lead = " ".join(description.split()[:4]).lower()
    if "RETIRED" in description:
        return "retired"
    if "and ignored" in lead:
        return "ignored"
    if lead.startswith("deprecated"):
        return "deprecated"
    return "live"


def _action_rows() -> list[dict[str, Any]]:
    files = [
        REPO_DIR / "action.yml",
        *sorted((REPO_DIR / "actions").glob("*/action.yml")),
    ]
    rows: list[dict[str, Any]] = []
    for f in files:
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        owner = "action" if f.parent == REPO_DIR else f"actions/{f.parent.name}"
        for name, spec in (doc.get("inputs") or {}).items():
            desc = str((spec or {}).get("description", ""))
            rows.append(
                {
                    "id": f"{owner}:{name}",
                    "action": owner,
                    "input": name,
                    "status": _input_status(desc),
                }
            )
    return rows


def _python_rows() -> list[dict[str, Any]]:
    from abicheck import service

    rows: list[dict[str, Any]] = []
    for cls_name in ("CompareRequest", "DumpRequest", "InputSpec"):
        cls = getattr(service, cls_name)
        for field in dataclasses.fields(cls):
            if field.name.startswith("_"):
                continue
            rows.append(
                {
                    "id": f"{cls_name}.{field.name}",
                    "owner": cls_name,
                    "name": field.name,
                }
            )
    for fn_name in ("run_compare", "run_dump"):
        sig = inspect.signature(getattr(service, fn_name))
        for name, param in sig.parameters.items():
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            rows.append({"id": f"{fn_name}({name})", "owner": fn_name, "name": name})
    return rows


def render() -> str:
    if str(REPO_DIR) not in sys.path:
        sys.path.insert(0, str(REPO_DIR))
    doc = {
        "generated_by": "scripts/gen_option_vocabulary.py -- do not hand-edit",
        "cli": _cli_rows(),
        "config": _config_rows(),
        "action": _action_rows(),
        "python": _python_rows(),
    }
    return json.dumps(doc, indent=1, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--check", action="store_true", help="fail if the committed inventory is stale"
    )
    args = ap.parse_args(argv)
    text = render()
    if args.check:
        current = OUT_PATH.read_text(encoding="utf-8") if OUT_PATH.exists() else ""
        if current != text:
            print(
                f"{OUT_PATH.relative_to(REPO_DIR)} is stale; run python scripts/gen_option_vocabulary.py",
                file=sys.stderr,
            )
            return 1
        return 0
    OUT_PATH.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
