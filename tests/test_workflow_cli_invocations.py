"""Every ``abicheck`` command line the shipped workflows run must parse today.

Bug class: a CLI spelling is retired (ADR-091, plan slice 7q/7r) but a shell
line inside a shipped reusable workflow or composite action still uses it, so
every consumer of that workflow fails at runtime while the unit suite stays
green. Reported instance: ``check-project.yml`` ran ``abicheck project plan
... -o run-plan.json`` after ``-o`` became ``FORMAT=DESTINATION``; the
"Generate run-plan" job exited 64 for every caller.

The oracle is click's own option table for the resolved (sub)command, not a
hand-kept list: every option token on an ``abicheck`` line in a ``run:`` block
must be an option that command declares, and a literal value given to an
export option (metavar ``FORMAT=DESTINATION``) must contain ``=``. Lines whose
value is a shell expansion are checked for the option name only.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from _yaml_fast import safe_load

from abicheck.cli import main

pytestmark = pytest.mark.repo_scan

REPO = Path(__file__).resolve().parent.parent
_SHELL_STOP = {"|", "||", "&&", ";", "2>&1"}
_EXPORT_METAVAR = "FORMAT=DESTINATION"


def _shipped_files() -> list[Path]:
    files = sorted((REPO / ".github" / "workflows").glob("*.yml"))
    files.append(REPO / "action.yml")
    files += sorted((REPO / "actions").glob("**/action.yml"))
    return [f for f in files if f.is_file()]


def _run_blocks(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "run" and isinstance(value, str):
                yield value
            else:
                yield from _run_blocks(value)
    elif isinstance(node, list):
        for item in node:
            yield from _run_blocks(item)


def _invocations(script: str) -> Iterator[list[str]]:
    joined = re.sub(r"\\\n\s*", " ", script)
    for line in joined.splitlines():
        match = re.search(r"(?:^|[\s;&|(])abicheck\s+([a-z].*)$", line.strip())
        if not match:
            continue
        try:
            yield shlex.split(match.group(1), comments=True)
        except ValueError:
            continue  # unbalanced quoting spans lines; not statically checkable


def _resolve(tokens: list[str]) -> tuple[click.Command | None, list[str], list[str]]:
    ctx = click.Context(main, info_name="abicheck")
    cmd: click.Command = main
    used: list[str] = []
    rest = list(tokens)
    while isinstance(cmd, click.Group) and rest:
        sub = cmd.get_command(ctx, rest[0])
        if sub is None:
            return None, used, rest
        used.append(rest.pop(0))
        ctx = click.Context(sub, parent=ctx, info_name=used[-1])
        cmd = sub
    return (None if isinstance(cmd, click.Group) else cmd), used, rest


def problems_in(script: str) -> list[str]:
    found: list[str] = []
    for tokens in _invocations(script):
        cmd, path, rest = _resolve(tokens)
        if cmd is None:
            continue
        options = {o: p for p in cmd.params for o in (*p.opts, *p.secondary_opts)}
        label = "abicheck " + " ".join(path)
        for index, token in enumerate(rest):
            if token in _SHELL_STOP or token.startswith(">"):
                break
            if not token.startswith("-") or token == "-":
                continue
            name, _, inline = token.partition("=")
            param = options.get(name)
            if param is None:
                found.append(f"{label}: no such option {name!r}")
                continue
            if getattr(param, "metavar", None) != _EXPORT_METAVAR:
                continue
            value = inline or (rest[index + 1] if index + 1 < len(rest) else "")
            if value and "=" not in value and "$" not in value:
                found.append(f"{label}: {name} {value!r} is not FORMAT=DESTINATION")
    return found


@pytest.mark.parametrize(
    "path", _shipped_files(), ids=lambda p: str(p.relative_to(REPO))
)
def test_shipped_abicheck_command_lines_parse(path: Path) -> None:
    document = safe_load(path.read_text(encoding="utf-8"))
    errors = [e for script in _run_blocks(document) for e in problems_in(script)]
    assert errors == [], f"{path.relative_to(REPO)}: {errors}"


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("abicheck project plan cfg.yml -o run-plan.json", "not FORMAT=DESTINATION"),
        ("abicheck aggregate dir --output report.json", "not FORMAT=DESTINATION"),
        ("abicheck compare a b \\\n  --format json", "no such option '--format'"),
        ("abicheck aggregate d --run-plan p.json", "no such option '--run-plan'"),
        (
            "abicheck dump lib.so --ast-frontend clang -o out.json",
            "no such option '--ast-frontend'",
        ),
    ],
)
def test_retired_spellings_are_reported(script: str, expected: str) -> None:
    errors = problems_in(script)
    assert len(errors) == 1 and expected in errors[0], errors


@pytest.mark.parametrize(
    "script",
    [
        "abicheck project plan cfg.yml -o json=run-plan.json",
        "abicheck dump lib.so -o out.json",  # dump's -o is a plain path
        'abicheck compare a b -o "$OUT"',  # shell expansion: name-only check
        "abicheck compare a b --output=json=r.json --label old=x 2>&1 | tee log",
        "echo abicheck is great",  # not an invocation of a known command
    ],
)
def test_current_spellings_pass(script: str) -> None:
    assert problems_in(script) == []
