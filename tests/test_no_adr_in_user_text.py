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

import _yaml_fast
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
    # The same table for aggregate/project/deps, split out of rulings.py for
    # file size; registered through rulings.RULINGS_BY_COMMAND, never rendered.
    "frontends/cli/options/rulings_integration.py": "internal option-ruling rationale, test-only",
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


@pytest.mark.repo_scan
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


# ── The GitHub Action layer ──────────────────────────────────────────────────

REPO_ROOT = PACKAGE_ROOT.parent
ACTION_YMLS = sorted(
    [REPO_ROOT / "action.yml", *(REPO_ROOT / "actions").glob("*/action.yml")]
)
ACTION_SCRIPTS = sorted(
    [*(REPO_ROOT / "action").glob("*.sh"), *(REPO_ROOT / "actions").rglob("*.sh")]
)

#: A shell line that emits text to a user: echo/printf, the validate-inputs
#: helpers, or a GitHub annotation. Deliberately simple and explicit.
_EMITS_OUTPUT_RE = re.compile(
    r"\b(echo|printf|_fail|_warn)\b|::(error|warning|notice)::|GITHUB_STEP_SUMMARY"
)


def _descriptions(node: object, where: str):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                yield where, value
            else:
                yield from _descriptions(value, f"{where}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _descriptions(value, f"{where}[{i}]")


def test_action_yml_files_found() -> None:
    assert REPO_ROOT / "action.yml" in ACTION_YMLS
    assert len(ACTION_YMLS) > 1
    assert any(p.name == "run.sh" for p in ACTION_SCRIPTS)


@pytest.mark.parametrize(
    "path", ACTION_YMLS, ids=lambda p: p.relative_to(REPO_ROOT).as_posix()
)
def test_no_adr_reference_in_action_descriptions(path: Path) -> None:

    doc = _yaml_fast.safe_load(path.read_text(encoding="utf-8"))
    descriptions = list(_descriptions(doc, path.name))
    assert descriptions, f"{path}: no description strings found"
    offending = [(w, d[:120]) for w, d in descriptions if ADR_RE.search(d)]
    assert not offending, f"{path}: description cites an ADR: {offending}"


def _output_lines_citing_adr(text: str) -> list[str]:
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        if _EMITS_OUTPUT_RE.search(line) and ADR_RE.search(line):
            hits.append(f"{lineno}: {line.strip()[:120]}")
    return hits


def test_output_line_detector_is_not_vacuous() -> None:
    assert _output_lines_citing_adr('echo "::error::gone (ADR-068)."')
    assert _output_lines_citing_adr('_fail "x (ADR-065 S1)"')
    assert not _output_lines_citing_adr("# echo (ADR-068) in a comment")
    assert not _output_lines_citing_adr('echo "no citation here"')


@pytest.mark.parametrize(
    "path", ACTION_SCRIPTS, ids=lambda p: p.relative_to(REPO_ROOT).as_posix()
)
def test_no_adr_reference_in_action_script_output(path: Path) -> None:
    hits = _output_lines_citing_adr(path.read_text(encoding="utf-8"))
    assert not hits, f"{path}: user-visible output cites an ADR:\n" + "\n".join(hits)


@pytest.mark.parametrize(
    "path", ACTION_YMLS, ids=lambda p: p.relative_to(REPO_ROOT).as_posix()
)
def test_no_adr_reference_in_action_yml_run_output(path: Path) -> None:
    # Composite actions embed shell in `run:` blocks; the same rule applies.
    hits = _output_lines_citing_adr(path.read_text(encoding="utf-8"))
    assert not hits, f"{path}: user-visible output cites an ADR:\n" + "\n".join(hits)
