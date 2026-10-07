# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``scripts/check_usage_ratchet.py``: test-only code and cross-module clones.

Every finding family is checked over several independently built synthetic
packages, each with an expected answer written down by hand (the oracle is
the fixture's own construction, never the gate's helpers), plus the ratchet
semantics: new key, fixed-but-baselined key, stale exception, and a
baseline that grew relative to the base revision.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_usage_ratchet as ur  # noqa: E402


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _package(root: Path, files: dict[str, str]) -> Path:
    _write(root, "abicheck/__init__.py", "")
    for rel, text in files.items():
        _write(root, rel, text)
    return root


_BIG_BODY = """
    total = 0
    for i in range(n):
        if i % 2:
            total += i * 3 + 1
        else:
            total -= i // 2
    while total > 100:
        total = total // 2 + n
    return [total, n, total * n, total - n]
"""


# ── test_only_functions ─────────────────────────────────────────────────────

FUNCTION_CASES = {
    "only-test-caller": (
        {
            "abicheck/a.py": "def used():\n    return 1\n\ndef orphan():\n    return 2\n",
            "abicheck/b.py": "from .a import used\n\ndef run():\n    return used()\n",
            "scripts/entry.py": "from abicheck.b import run\nrun()\n",
            "tests/test_a.py": "from abicheck.a import orphan, used\n\ndef test_x():\n    assert orphan() == 2 and used() == 1\n",
        },
        {"abicheck.a#orphan"},
    ),
    "chain-only-reached-from-test-only": (
        # helper's only production caller is test-only itself: both go.
        {
            "abicheck/a.py": "def helper():\n    return 1\n\ndef wrapper():\n    return helper()\n",
            "tests/test_a.py": "from abicheck.a import wrapper, helper\n\ndef test_x():\n    assert wrapper() == helper()\n",
        },
        {"abicheck.a#helper", "abicheck.a#wrapper"},
    ),
    "method-and-dunder": (
        {
            "abicheck/c.py": "class K:\n    def __repr__(self):\n        return 'K'\n\n    def probe(self):\n        return 1\n",
            "scripts/run.py": "from abicheck.c import K\nK()\n",
            "tests/test_c.py": "from abicheck.c import K\n\ndef test_k():\n    assert K().probe() == 1\n",
        },
        {"abicheck.c#K.probe"},
    ),
    "unreferenced-everywhere-is-not-test-only": (
        {
            "abicheck/d.py": "def nobody():\n    return 0\n",
            "tests/test_d.py": "def test_nothing():\n    assert True\n",
        },
        set(),
    ),
    "workflow-text-keeps-alive": (
        {
            "abicheck/e.py": "def from_ci():\n    return 0\n",
            ".github/workflows/x.yml": "run: python -c 'from abicheck.e import from_ci; from_ci()'\n",
            "tests/test_e.py": "from abicheck.e import from_ci\n\ndef test_e():\n    assert from_ci() == 0\n",
        },
        set(),
    ),
}


@pytest.mark.parametrize("case", sorted(FUNCTION_CASES))
def test_test_only_functions(tmp_path: Path, case: str) -> None:
    files, expected = FUNCTION_CASES[case]
    assert ur.test_only_functions(_package(tmp_path, files)) == expected


def test_baseline_file_is_not_a_reference(tmp_path: Path) -> None:
    """A baselined key names its function; that must not keep it alive."""
    files, expected = FUNCTION_CASES["only-test-caller"]
    root = _package(tmp_path, files)
    _write(
        root,
        ur.BASELINE_REL,
        json.dumps({"test_only_functions": ["abicheck.a#orphan"]}),
    )
    _write(
        root,
        ur.EXCEPTIONS_REL,
        "test_only_functions:\n  - key: abicheck.a#orphan\n    reason: x\n",
    )
    assert ur.test_only_functions(root) == expected


# ── test_only_modules ───────────────────────────────────────────────────────

MODULE_CASES = {
    "plain": (
        {
            "abicheck/live.py": "X = 1\n",
            "abicheck/lonely.py": "Y = 2\n",
            "abicheck/cli.py": "from . import live\n",
            "pyproject.toml": '[project.scripts]\nabicheck = "abicheck.cli:main"\n',
            "tests/test_m.py": "import abicheck.lonely\nfrom abicheck import live\n",
        },
        {"abicheck.lonely"},
    ),
    "imported-only-by-test-only-module": (
        {
            "abicheck/pkg/__init__.py": "",
            "abicheck/pkg/inner.py": "Z = 1\n",
            "abicheck/pkg/outer.py": "from .inner import Z\n",
            "tests/test_m.py": "from abicheck.pkg.outer import Z\n",
        },
        {"abicheck.pkg.outer"},  # inner is not named by tests
    ),
    "string-import-is-a-use-docstring-is-not": (
        {
            "abicheck/dyn.py": "V = 1\n",
            "abicheck/doc.py": "W = 1\n",
            "abicheck/loader.py": '"""See abicheck.doc for details."""\nimport importlib\nimportlib.import_module("abicheck.dyn")\n',
            "scripts/go.py": "import abicheck.loader\n",
            "tests/test_m.py": "import abicheck.dyn, abicheck.doc\n",
        },
        {"abicheck.doc"},
    ),
    "relative-import-from-package": (
        {
            "abicheck/sub/__init__.py": "from . import leaf\n",
            "abicheck/sub/leaf.py": "Q = 1\n",
            "tests/test_m.py": "from abicheck.sub import leaf\n",
        },
        set(),  # the package __init__ is live by definition
    ),
}


@pytest.mark.parametrize("case", sorted(MODULE_CASES))
def test_test_only_modules(tmp_path: Path, case: str) -> None:
    files, expected = MODULE_CASES[case]
    assert ur.test_only_modules(_package(tmp_path, files)) == expected


# ── cross_module_exact_clones ───────────────────────────────────────────────


def _fn(name: str, body: str = _BIG_BODY, doc: str = "") -> str:
    doc_line = f'    """{doc}"""\n' if doc else ""
    return f"def {name}(n):\n{doc_line}{textwrap.indent(textwrap.dedent(body).strip(), '    ')}\n"


CLONE_CASES = {
    "renamed-and-redocumented-clone": (
        {
            "abicheck/x.py": "\n\n" + _fn("crunch", doc="one"),
            "abicheck/y.py": _fn("_crunch_copy", doc="two"),
        },
        {"abicheck.x#crunch = abicheck.y#_crunch_copy"},
    ),
    "same-module-only-is-not-cross-module": (
        {"abicheck/x.py": _fn("a") + "\n" + _fn("b")},
        set(),
    ),
    "three-way-with-method": (
        {
            "abicheck/x.py": _fn("a"),
            "abicheck/y.py": "class C:\n" + textwrap.indent(_fn("m"), "    "),
            "abicheck/z.py": _fn("b"),
        },
        None,  # method has a different first parameter set: see below
    ),
    "one-constant-differs": (
        {
            "abicheck/x.py": _fn("a"),
            "abicheck/y.py": _fn("a", _BIG_BODY.replace("i * 3", "i * 4")),
        },
        set(),
    ),
    "below-size-threshold": (
        {
            "abicheck/x.py": "def f(n):\n    return n + 1\n",
            "abicheck/y.py": "def g(n):\n    return n + 1\n",
        },
        set(),
    ),
}
CLONE_CASES["three-way-with-method"] = (
    CLONE_CASES["three-way-with-method"][0],
    # ``m(n)`` inside a class has the same signature text, so it is a clone.
    {"abicheck.x#a = abicheck.y#C.m = abicheck.z#b"},
)


@pytest.mark.parametrize("case", sorted(CLONE_CASES))
def test_cross_module_exact_clones(tmp_path: Path, case: str) -> None:
    files, expected = CLONE_CASES[case]
    assert ur.cross_module_exact_clones(_package(tmp_path, files)) == expected


def test_clone_keys_are_position_free(tmp_path: Path) -> None:
    files, _ = CLONE_CASES["renamed-and-redocumented-clone"]
    root = _package(tmp_path, files)
    before = ur.cross_module_exact_clones(root)
    _write(root, "abicheck/x.py", "# moved\n\n\n\n" + files["abicheck/x.py"])
    assert ur.cross_module_exact_clones(root) == before


# ── ratchet semantics ───────────────────────────────────────────────────────


def _cats(**kw: set[str]) -> dict[str, set[str]]:
    return {c: set(kw.get(c, set())) for c in ur.CATEGORIES}


@pytest.mark.parametrize(
    ("found", "baseline", "exceptions", "base", "fragments"),
    [
        (
            _cats(test_only_modules={"m"}),
            _cats(test_only_modules={"m"}),
            _cats(),
            None,
            [],
        ),
        (
            _cats(test_only_modules={"m", "n"}),
            _cats(test_only_modules={"m"}),
            _cats(),
            None,
            ["NEW finding: n"],
        ),
        (
            _cats(),
            _cats(test_only_functions={"a#f"}),
            _cats(),
            None,
            ["fixed but still baselined"],
        ),
        (
            _cats(test_only_functions={"a#f"}),
            _cats(),
            _cats(test_only_functions={"a#f"}),
            None,
            [],
        ),
        (
            _cats(),
            _cats(),
            _cats(test_only_functions={"a#f"}),
            None,
            ["stale exception"],
        ),
        (
            _cats(cross_module_exact_clones={"a#f = b#g"}),
            _cats(cross_module_exact_clones={"a#f = b#g"}),
            _cats(),
            _cats(),
            ["absent on the base revision"],
        ),
        (
            _cats(cross_module_exact_clones={"k"}),
            _cats(cross_module_exact_clones={"k"}),
            _cats(),
            _cats(cross_module_exact_clones={"k", "old"}),
            [],
        ),
    ],
)
def test_evaluate(found, baseline, exceptions, base, fragments) -> None:
    errors = ur.evaluate(found, baseline, exceptions, base)
    assert len(errors) == len(fragments)
    for frag in fragments:
        assert any(frag in e for e in errors), errors


def test_exceptions_require_reason(tmp_path: Path) -> None:
    path = tmp_path / "e.yaml"
    path.write_text("test_only_functions:\n  - key: a#f\n", encoding="utf-8")
    with pytest.raises(ValueError, match="reason"):
        ur.load_exceptions(path)


@pytest.mark.parametrize(
    "body",
    [
        "test_only_functions: a#f\n",
        "test_only_functions: {key: a#f, reason: r}\n",
        "cross_module_exact_clones: 3\n",
    ],
)
def test_exceptions_category_must_be_list(tmp_path: Path, body: str) -> None:
    path = tmp_path / "e.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match="must be a list"):
        ur.load_exceptions(path)
    assert ur.main(["--root", str(tmp_path), "--exceptions", str(path)]) == 2


def test_exceptions_null_category_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "e.yaml"
    path.write_text(
        "test_only_functions:\ncross_module_exact_clones: []\n", encoding="utf-8"
    )
    assert ur.load_exceptions(path) == {c: set() for c in ur.CATEGORIES}


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


def test_main_end_to_end_with_base(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files, _ = FUNCTION_CASES["only-test-caller"]
    root = _package(tmp_path, files)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")  # base has no baseline: gate introduced

    argv = ["--root", str(root), "--base", "HEAD"]
    assert ur.main(argv) == 1  # finding, no baseline
    assert ur.main(["--root", str(root), "--update"]) == 0
    assert ur.main(argv) == 0  # base had no baseline at all
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "adopt")

    # A new test-only function on top: baselining it is refused against base.
    _write(
        root,
        "abicheck/a.py",
        files["abicheck/a.py"] + "\ndef orphan2():\n    return 3\n",
    )
    _write(root, "tests/test_b.py", "from abicheck.a import orphan2\n")
    assert ur.main(argv) == 1
    assert ur.main(["--root", str(root), "--update"]) == 0
    capsys.readouterr()
    assert ur.main(argv) == 1
    assert "absent on the base revision: abicheck.a#orphan2" in capsys.readouterr().out

    # Fixing the original finding: the stale baseline key fails until --update.
    _write(
        root,
        "scripts/entry.py",
        files["scripts/entry.py"]
        + "from abicheck.a import orphan, orphan2\norphan(); orphan2()\n",
    )
    assert ur.main(["--root", str(root)]) == 1
    assert ur.main(["--root", str(root), "--update"]) == 0
    assert ur.main(argv) == 0
    assert ur.main(["--root", str(root), "--base", "no-such-ref"]) == 2


def test_exceptions_invalid_yaml_exits_2(tmp_path: Path, capsys) -> None:
    path = tmp_path / "exc.yaml"
    path.write_text("test_only_functions: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid YAML"):
        ur.load_exceptions(path)
    assert ur.main(["--root", str(tmp_path), "--exceptions", str(path)]) == 2
    assert "not valid YAML" in capsys.readouterr().err


def test_exceptions_without_pyyaml_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "exc.yaml"
    path.write_text("test_only_functions: []\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "yaml", None)  # import yaml -> ImportError
    with pytest.raises(ValueError, match="PyYAML is required"):
        ur.load_exceptions(path)
    assert ur.main(["--root", str(tmp_path), "--exceptions", str(path)]) == 2


def test_unresolvable_base_reports_git_stderr(tmp_path: Path) -> None:
    # Not a repository: git prints a diagnostic, which must reach the user.
    with pytest.raises(ValueError, match="does not resolve") as info:
        ur.base_baseline(tmp_path, "HEAD")
    assert "not a git repository" in str(info.value)


def test_unresolvable_base_without_stderr(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    with pytest.raises(ValueError, match=r"'no-such-ref' does not resolve$"):
        ur.base_baseline(tmp_path, "no-such-ref")
