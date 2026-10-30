# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""scripts/check_static_dead_code.py: each rule against hand-written trees
whose expected findings are stated here, not derived from the checker."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_static_dead_code as gate  # noqa: E402


def _tree(root: Path, files: dict[str, str]) -> Path:
    (root / "pyproject.toml").write_text(
        '[project.scripts]\nabicheck = "abicheck.entry:main"\n'
    )
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    return root


def _found(root: Path, rule: str) -> set[str]:
    return {
        f.name if rule != "unreachable" else f"{f.path}:{f.line}"
        for f in gate.collect(root)
        if f.rule == rule
    }


@pytest.mark.parametrize(
    "terminator",
    ["return 1", "raise ValueError", "continue", "break"],
)
def test_statement_after_a_terminator_is_unreachable(
    tmp_path: Path, terminator: str
) -> None:
    body = f"""
    def f(xs):
        for x in xs:
            {terminator}
            y = 2
        return 0
    """
    root = _tree(tmp_path, {"abicheck/__init__.py": "", "abicheck/m.py": body})
    assert _found(root, "unreachable") == {"abicheck/m.py:5"}


def test_terminator_closing_its_block_is_not_unreachable(tmp_path: Path) -> None:
    body = """
    def f(x):
        if x:
            return 1
        else:
            raise ValueError
        try:
            return 2
        finally:
            pass
    """
    root = _tree(tmp_path, {"abicheck/__init__.py": "", "abicheck/m.py": body})
    assert _found(root, "unreachable") == set()


@pytest.mark.parametrize(
    ("importer", "orphan"),
    [
        ("from abicheck import a\n", {"abicheck.b"}),
        ("import abicheck.a, abicheck.b as bee\n", set()),
        (
            "from . import (  # noqa: E402\n    a as _a,  # side effect\n    b,\n)\n",
            set(),
        ),
        ("from .a import thing\n", {"abicheck.b"}),
        (
            "import importlib\nimportlib.import_module('.b', __package__)\n",
            {"abicheck.a"},
        ),
        ("x = 1\n", {"abicheck.a", "abicheck.b"}),
    ],
)
def test_orphan_modules(tmp_path: Path, importer: str, orphan: set[str]) -> None:
    root = _tree(
        tmp_path,
        {
            "abicheck/__init__.py": "",
            "abicheck/user.py": importer,
            "abicheck/a.py": "thing = 1\n",
            "abicheck/b.py": "",
            "abicheck/entry.py": "def main(): pass\n",  # named by pyproject
            "abicheck/__main__.py": "",
            "tests/test_user.py": "from abicheck import user\n",
        },
    )
    assert _found(root, "orphan-module") == orphan


def test_unread_parameters_and_their_exemptions(tmp_path: Path) -> None:
    body = """
    import functools

    def used(a, b):
        return a + b

    def unread(a, b, *args, _private=None, **kw):
        return a

    def read_in_nested(a):
        return lambda: a

    def stub(a):
        ...

    def abstract(a):
        raise NotImplementedError

    def kept(a):  # noqa: ARG001 -- callers pass it
        return 1

    def introspects(a):
        return locals()

    @functools.lru_cache
    def cached(a, b):
        return a

    @registry.detector("x")
    def registered(old, new):
        return []

    class Plain:
        def m(self, a, b):
            return a

        @staticmethod
        def s(a, b):
            return b

    class Override(Base):
        def m(self, a):
            return 1
    """
    root = _tree(
        tmp_path, {"abicheck/__init__.py": "import abicheck.m\n", "abicheck/m.py": body}
    )
    assert _found(root, "unread-parameter") == {
        "unread(b)",
        "cached(b)",
        "Plain.m(b)",
        "Plain.s(a)",
    }


def test_gate_ratchets_unread_parameters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _tree(
        tmp_path,
        {
            "abicheck/__init__.py": "import abicheck.m\n",
            "abicheck/m.py": "def f(a, b):\n    return a\n",
        },
    )
    baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(gate, "ROOT", root)
    monkeypatch.setattr(gate, "BASELINE", baseline)
    assert gate.main([]) == 1  # new finding, no baseline
    assert gate.main(["--write-baseline"]) == 0
    assert gate.main([]) == 0
    (root / "abicheck/m.py").write_text("def f(a):\n    return a\n")
    assert gate.main([]) == 1  # fixed entry must leave the baseline
    assert "fixed -- delete it" in capsys.readouterr().out


@pytest.mark.repo_scan
def test_repository_is_clean() -> None:
    assert gate.main([]) == 0
