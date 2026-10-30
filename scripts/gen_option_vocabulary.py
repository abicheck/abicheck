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
import enum
import inspect
import json
import sys
import types
import typing
from pathlib import Path
from typing import Any

import yaml

REPO_DIR = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_DIR / "architecture" / "option-vocabulary-inventory.json"

#: Click's own options, not abicheck options.
_CLI_SKIP = frozenset({"--help", "--help-all"})


def _default_text(value: Any) -> str:
    """A deterministic, address-free rendering of a default value.

    ``repr`` of an arbitrary object can carry a memory address, which would make
    the committed inventory differ between runs and between Python versions.
    Primitives and containers of them render literally; anything else renders as
    its type name, and a callable as ``<callable>``.
    """
    if value is inspect.Parameter.empty or value is dataclasses.MISSING:
        return "<required>"
    if type(value).__name__ == "Sentinel":
        # Click's "not given" marker: its class and repr moved between Click
        # releases, so the inventory must not depend on the installed version.
        return "<unset>"
    if isinstance(value, enum.Enum):
        return f"{type(value).__qualname__}.{value.name}"
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_default_text(v) for v in value) + "]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: str(kv[0]))
        return "{" + ", ".join(f"{k!r}: {_default_text(v)}" for k, v in items) + "}"
    if callable(value):
        return "<callable>"
    return f"<{type(value).__qualname__}>"


def _click_default_text(p: Any) -> str:
    """A CLI option's default, the same on every Click release.

    An option given no default has ``None`` on Click 8.2 and the unset sentinel
    on 8.3+; both mean "no default", so both render as ``<unset>``, or ``False``
    for a boolean flag, whose omitted value is ``False`` on every release. An
    explicit default keeps its own spelling.
    """
    default = p.default
    if default is None or type(default).__name__ == "Sentinel":
        return "False" if p.is_flag else "<unset>"
    return _default_text(default)


def _choices(hint: Any) -> list[Any] | None:
    """The closed value set a type annotation admits, or None when it is open.

    ``Literal[...]`` and ``enum.Enum`` subclasses are closed; ``Optional``/``|``
    unions contribute the closed members among their arms.
    """
    if typing.get_origin(hint) is typing.Literal:
        return list(typing.get_args(hint))
    if isinstance(hint, type) and issubclass(hint, enum.Enum):
        return [m.value for m in hint]
    if typing.get_origin(hint) in (typing.Union, types.UnionType):
        found: list[Any] = []
        for arm in typing.get_args(hint):
            sub = _choices(arm)
            if sub:
                found.extend(sub)
        return found or None
    return None


def _annotation_text(hint: Any) -> str:
    if hint is inspect.Parameter.empty:
        return "<none>"
    if isinstance(hint, str):
        return hint
    if isinstance(hint, type):
        return hint.__qualname__
    return str(hint).replace("typing.", "")


def _cli_rows() -> list[dict[str, Any]]:
    import click

    from abicheck.cli import main
    from abicheck.frontends.cli.help import (
        COMPARE_COMMON_OPTION_NAMES,
        DUMP_COMMON_OPTION_NAMES,
    )

    # The options each command's plain ``--help`` shows (ADR-091 D4's "visible"
    # set). A command without a curated pair shows every non-hidden option.
    common = {"compare": COMPARE_COMMON_OPTION_NAMES, "dump": DUMP_COMMON_OPTION_NAMES}
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
                    "help_common": (
                        p.name in common[path[0]]
                        if len(path) == 1 and path[0] in common
                        else not p.hidden
                    ),
                    "multiple": bool(p.multiple),
                    "is_flag": bool(p.is_flag),
                    "type": p.type.name,
                    "choices": (
                        list(p.type.choices)
                        if isinstance(p.type, click.Choice)
                        else None
                    ),
                    "default": _click_default_text(p),
                }
            )

    walk(main, [])
    return rows


def _config_rows() -> list[dict[str, Any]]:
    from abicheck.buildsource.build_config import KEY_TABLE

    return [
        {
            "id": spec.path,
            "kind": str(getattr(spec.kind, "name", spec.kind)).lower(),
            "default": _default_text(spec.default),
        }
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
            default = (spec or {}).get("default")
            rows.append(
                {
                    "id": f"{owner}:{name}",
                    "action": owner,
                    "input": name,
                    "status": _input_status(desc),
                    "required": bool((spec or {}).get("required", False)),
                    "default": default
                    if default is None or isinstance(default, (bool, int, str))
                    else str(default),
                }
            )
    return rows


def _hints(owner: Any) -> dict[str, Any]:
    try:
        return typing.get_type_hints(owner)
    except Exception:  # an unresolvable forward reference keeps the raw text
        return {}


def _python_rows() -> list[dict[str, Any]]:
    from abicheck import service

    rows: list[dict[str, Any]] = []
    for cls_name in ("CompareRequest", "DumpRequest", "InputSpec"):
        cls = getattr(service, cls_name)
        hints = _hints(cls)
        for field in dataclasses.fields(cls):
            if field.name.startswith("_"):
                continue
            hint = hints.get(field.name, field.type)
            if field.default is not dataclasses.MISSING:
                default: Any = field.default
            elif field.default_factory is not dataclasses.MISSING:
                try:
                    default = field.default_factory()
                except Exception:  # a factory that needs context keeps its type name
                    default = inspect.Parameter.empty
            else:
                default = inspect.Parameter.empty
            rows.append(
                {
                    "id": f"{cls_name}.{field.name}",
                    "owner": cls_name,
                    "name": field.name,
                    "annotation": _annotation_text(hint),
                    "choices": _choices(hint),
                    "default": _default_text(default),
                }
            )
    for fn_name in ("run_compare", "run_dump"):
        fn = getattr(service, fn_name)
        hints = _hints(fn)
        for name, param in inspect.signature(fn).parameters.items():
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            hint = hints.get(name, param.annotation)
            rows.append(
                {
                    "id": f"{fn_name}({name})",
                    "owner": fn_name,
                    "name": name,
                    "annotation": _annotation_text(hint),
                    "choices": _choices(hint),
                    "default": _default_text(param.default),
                }
            )
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
