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

"""Every ``--flag`` a user-facing diagnostic names is one some command accepts.

Bug class ``cli_surface.retired_spelling_in_remediation`` (repo-wide half): ADR-037 D8.1 /
ADR-068 Phase 6 moved the whole L2 compile-context family (``--ast-frontend``,
``--frontend-context``, ``--lang``, ``--compiler*``, ``--sysroot``,
``--nostdinc``, ``--allow-ast-frontend-fallback``) to ``.abicheck.yml``'s
``compile:`` block and deleted ``scan``, but the recovery advice in the
dumper/extract layer was never migrated -- a clang-only host running ``dump``
was told "run with --ast-frontend clang" and got ``No such option``.

The oracle is the live Click tree, not a list of retired spellings: a flag
counts as live only if some registered command (the native CLI with ``compat``,
plus every standalone Click entry point under ``abicheck/``) declares it. A
retired-spelling list would only catch the retirements someone remembered to
record.

Scope is *diagnostic sinks* -- ``raise``, click echo/fail, logging, warnings,
and ``*Error``/``*Exception`` constructors -- so an external tool's argv
(``--target=``, ``--driver-mode``), an option's own definition, and the
ChangeKind catalog's historical descriptions are out of scope by construction
rather than by allowlist. A ``(was --old-flag)`` migration hint is allowed: it
names the retired spelling on purpose, next to its replacement.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import click
import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "abicheck"

_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*[a-z0-9])")
_MIGRATION_HINT = re.compile(r"\bwas\s+$")
_SINK_NAMES = frozenset(
    {
        "echo",
        "secho",
        "fail",
        "warn",
        "warning",
        "error",
        "info",
        "debug",
        "exception",
        "critical",
        "UsageError",
        "BadParameter",
        "BadOptionUsage",
        "ClickException",
    }
)
_SINK_SUFFIXES = ("Error", "Exception", "Warning")

#: Functions whose messages are reachable from tests only: each is kept alive
#: by a test-local re-implementation of a command that was deleted (ADR-043
#: ``collect``/``merge``; ADR-068 ``scan --artifact-set``). Their flags are
#: stale by definition, but so is the code -- deleting it is its own change.
#: :func:`test_allowlisted_functions_have_no_production_caller` keeps this
#: honest: an entry whose function gains a production caller fails.
DEAD_CODE_ALLOWLIST: dict[tuple[str, str], str] = {
    (
        "abicheck/cli_buildsource_helpers.py",
        "parse_from_specs",
    ): "deleted `collect --from`",
    (
        "abicheck/cli_buildsource_helpers.py",
        "_run_adapters",
    ): "deleted `collect` adapters",
    (
        "abicheck/cli_buildsource_helpers.py",
        "_enforce_strict_mode",
    ): "deleted `collect --collection-mode`",
    (
        "abicheck/cli_buildsource_merge.py",
        "_merge_handle_conflicts",
    ): "deleted `merge --on-conflict`",
    ("abicheck/bundle.py", "discover_artifact_set"): "deleted `scan --artifact-set`",
    ("abicheck/bundle.py", "audit_bundle"): "deleted `scan --artifact-set`",
}


def _walk_params(cmd: click.Command, out: set[str]) -> None:
    for p in cmd.params:
        out.update(o for o in (*p.opts, *p.secondary_opts) if o.startswith("--"))
    if isinstance(cmd, click.Group):
        for sub in cmd.commands.values():
            _walk_params(sub, out)


def _registered_flags() -> set[str]:
    from abicheck.cli import main

    flags: set[str] = set()
    _walk_params(main, flags)
    # Standalone Click entry points (the Action's helper CLIs, ...) that are
    # not mounted on `main` still own their flags.
    for path in PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "click.option(" not in text:
            continue
        mod = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        module = importlib.import_module(mod)
        for obj in vars(module).values():
            if isinstance(obj, click.Command):
                _walk_params(obj, flags)
    return flags


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_sink_call(node: ast.Call) -> bool:
    name = _call_name(node.func)
    return name in _SINK_NAMES or name.endswith(_SINK_SUFFIXES)


def _enclosing_functions(tree: ast.AST) -> list[tuple[int, int, str]]:
    return [
        (n.lineno, n.end_lineno or n.lineno, n.name)
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _innermost(spans: list[tuple[int, int, str]], line: int) -> str:
    best = ("", -1)
    for start, end, name in spans:
        if start <= line <= end and start > best[1]:
            best = (name, start)
    return best[0]


def _diagnostic_flag_mentions() -> list[tuple[str, int, str, str]]:
    """``(relpath, line, function, flag)`` for every flag a sink names."""
    found: list[tuple[str, int, str, str]] = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        spans = _enclosing_functions(tree)
        seen: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                sink: ast.AST = node.exc
            elif isinstance(node, ast.Call) and _is_sink_call(node):
                sink = node
            else:
                continue
            for const in ast.walk(sink):
                if not (
                    isinstance(const, ast.Constant) and isinstance(const.value, str)
                ):
                    continue
                if id(const) in seen:
                    continue
                seen.add(id(const))
                for m in _FLAG.finditer(const.value):
                    if _MIGRATION_HINT.search(const.value[: m.start()]):
                        continue
                    fn = _innermost(spans, const.lineno)
                    found.append((rel, const.lineno, fn, m.group(1)))
    return found


@pytest.fixture(scope="module")
def registered() -> set[str]:
    return _registered_flags()


@pytest.fixture(scope="module")
def mentions() -> list[tuple[str, int, str, str]]:
    return _diagnostic_flag_mentions()


def test_every_diagnostic_flag_is_registered(
    registered: set[str], mentions: list[tuple[str, int, str, str]]
) -> None:
    stale = [
        f"{rel}:{line} ({fn}) names {flag}"
        for rel, line, fn, flag in mentions
        if flag not in registered and (rel, fn) not in DEAD_CODE_ALLOWLIST
    ]
    assert not stale, (
        "diagnostics name flags no command accepts -- point them at the live "
        "spelling (a compile.*/debug.* config key, an ABICHECK_* env var, or "
        "the replacement flag):\n  " + "\n  ".join(stale)
    )


def test_allowlisted_functions_have_no_production_caller() -> None:
    """An allowlist entry is only valid while its function is unreachable
    from the product; a production call site turns its stale flags into live
    advice again."""
    callers: dict[str, list[str]] = {}
    defined: set[tuple[str, str]] = set()
    for path in PKG.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add((rel, node.name))
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                callers.setdefault(name, []).append(f"{rel}:{node.lineno}")
    live: list[str] = []
    for rel, fn in DEAD_CODE_ALLOWLIST:
        assert (rel, fn) in defined, f"{rel}: {fn} no longer exists -- drop the entry"
        live.extend(f"{site} calls {fn}" for site in callers.get(fn, ()))
    assert not live, "allowlisted dead code has a production caller:\n  " + "\n  ".join(
        live
    )


def test_allowlist_entries_still_carry_a_stale_mention(
    mentions: list[tuple[str, int, str, str]], registered: set[str]
) -> None:
    """A dead-code entry whose stale mentions are gone is no longer needed."""
    stale_fns = {(rel, fn) for rel, _l, fn, flag in mentions if flag not in registered}
    unused = sorted(set(DEAD_CODE_ALLOWLIST) - stale_fns)
    assert not unused, f"allowlist entries with nothing to excuse: {unused}"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ('raise click.UsageError("use --gone-flag instead")', ["--gone-flag"]),
        ('log.warning("try --gone-flag")', ["--gone-flag"]),
        ('raise ValueError(f"x {y} --gone-flag")', ["--gone-flag"]),
        ('warnings.warn("--gone-flag ignored")', ["--gone-flag"]),
        ('click.echo("see --view show=... (was --gone-flag)")', ["--view"]),
        ('subprocess.run(["clang", "--target=x"])', []),
        ('HELP = "--gone-flag"', []),
    ],
)
def test_sink_classifier(tmp_path: Path, message: str, expected: list[str]) -> None:
    """The scanner's own contract, on synthetic sources: sinks are flagged,
    migration hints and non-sink literals (argv, constants) are not."""
    global PKG, ROOT
    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "m.py").write_text(f"def f():\n    {message}\n")
    saved = PKG, ROOT
    PKG, ROOT = pkg, tmp_path
    try:
        got = [flag for *_rest, flag in _diagnostic_flag_mentions()]
    finally:
        PKG, ROOT = saved
    assert got == expected
