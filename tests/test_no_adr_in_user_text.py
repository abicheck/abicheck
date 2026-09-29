"""User-facing text must not cite internal design-record (ADR) numbers.

ADR ids ("ADR-049 D8", "ADR-065 S1", ...) are contributor vocabulary. A user
reading ``--help``, an error message or a rendered report has no way to look
one up, so a citation there is noise at best and a dead reference at worst.
Code comments and docstrings may keep them; this test covers the text that
can reach a user:

1. every non-docstring string literal under ``abicheck/`` (help text, error
   and log messages, report text, ChangeKind impact text, fact-registry
   notes), scanned with the AST so comments and docstrings are excluded; and
2. the real Click command tree -- command help (which Click takes from the
   command function's docstring, so the literal scan cannot see it), short
   help, epilog, and every option/argument's ``help=``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import click
import pytest

ADR_RE = re.compile(r"\bADR[- ]?\d")
PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "abicheck"

#: Files whose ADR-citing literals are never shown to a user, with why.
#: Keep this list short and justified; a new user-facing string belongs in
#: neither of these files.
ALLOWLISTED_FILES: dict[str, str] = {
    # Per-option design rulings for compare/dump: an internal rationale table
    # consumed only by tests/test_config_rebalance.py's exact-bijection check
    # (via frontends/cli/options/inventory.py). Never rendered to a user.
    "frontends/cli/options/rulings.py": "internal option-ruling rationale, test-only",
}


def _is_kind_names_comment(
    path: Path, node: ast.Constant, parents: dict[int, ast.AST]
) -> bool:
    """The third element of a ``(NAME, value, comment)`` triple in kind_names_*.py.

    ``kinds.py`` unpacks those triples as ``name, value, _comment`` and
    discards the comment, so it never reaches a user.
    """
    if not path.name.startswith("kind_names_"):
        return False
    parent = parents.get(id(node))
    return (
        isinstance(parent, ast.Tuple)
        and len(parent.elts) == 3
        and parent.elts[2] is node
    )


def _user_visible_adr_literals() -> list[str]:
    hits: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        rel = path.relative_to(PACKAGE_ROOT).as_posix()
        if rel in ALLOWLISTED_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings: set[int] = set()
        parents: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node
            # Docstrings and bare string statements are comments, not output.
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                docstrings.add(id(node.value))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and ADR_RE.search(node.value)
                and not _is_kind_names_comment(path, node, parents)
            ):
                hits.append(f"abicheck/{rel}:{node.lineno}: {node.value[:100]!r}")
    return hits


def test_no_adr_reference_in_user_visible_string_literals() -> None:
    hits = _user_visible_adr_literals()
    assert not hits, "ADR citation in user-visible text:\n" + "\n".join(hits)


def test_allowlisted_files_still_exist() -> None:
    for rel in ALLOWLISTED_FILES:
        assert (PACKAGE_ROOT / rel).is_file(), f"stale allowlist entry: {rel}"


def _walk_commands(cmd: click.Command, path: str):
    yield path, cmd
    if isinstance(cmd, click.Group):
        for name, sub in cmd.commands.items():
            yield from _walk_commands(sub, f"{path} {name}")


def _command_tree() -> list[tuple[str, click.Command]]:
    from abicheck.cli import main

    return list(_walk_commands(main, "abicheck"))


def test_command_tree_is_nontrivial() -> None:
    # Vacuity guard: the walk must actually reach the subcommands.
    paths = {p for p, _ in _command_tree()}
    assert {"abicheck compare", "abicheck dump", "abicheck project"} <= paths
    assert any(p.startswith("abicheck project ") for p in paths)
    assert any(p.startswith("abicheck compat ") for p in paths)


@pytest.mark.parametrize(
    "path,cmd", _command_tree(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_no_adr_reference_in_click_help(path: str, cmd: click.Command) -> None:
    texts = {
        "help": cmd.help,
        "short_help": cmd.short_help,
        "epilog": cmd.epilog,
    }
    for param in cmd.params:
        texts[f"param {param.name}"] = getattr(param, "help", None)
    offending = {k: v for k, v in texts.items() if v and "ADR" in v}
    assert not offending, f"{path}: help text cites an ADR: {offending}"
