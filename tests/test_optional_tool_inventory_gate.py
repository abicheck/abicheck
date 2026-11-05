# SPDX-License-Identifier: Apache-2.0
"""Every optional-tool lookup in ``abicheck/`` has a registered default-install expectation.

The clang companion layout tool was looked up with ``shutil.which`` and an
environment variable, and nothing recorded what a run without it must still
produce, so clang snapshots silently lacked sizes and offsets for months. This
gate scans ``abicheck/`` for every lookup shape and fails when a site is not in
``tests/optional_tool_inventory.py`` (or a registered site no longer exists),
so a new lookup cannot land without its role, its expectation and, for an
optional enrichment, a proof in ``tests/test_default_install_fact_invariants.py``.

Lookup shapes:

* ``which:<path>:<argument>`` -- a ``shutil.which(...)``/``which(...)`` call;
* ``find_bin:<path>:<name>`` -- a ``def find_*_bin`` resolver;
* ``argv:<path>:<program>`` -- a literal program as ``argv[0]``, passed to a
  subprocess call or returned as a command list;
* ``import:<module>`` -- a third-party module imported under
  ``try/except ImportError`` that is neither stdlib nor a declared dependency;
* ``env:<NAME>`` -- any ``ABICHECK_*`` string literal (an env-var name).
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from collections import Counter
from functools import cache
from pathlib import Path

import pytest

from tests.optional_tool_inventory import (
    DEPTHS,
    INVENTORY,
    Role,
    hidden_binaries,
)

pytestmark = pytest.mark.repo_scan

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "abicheck"

_SUBPROCESS_CALLS = frozenset(
    {"run", "Popen", "check_output", "check_call", "call", "run_bounded"}
)
_PROGRAM = re.compile(r"[a-z][\w+.-]*")
_ENV_NAME = re.compile(r"ABICHECK_[A-Z0-9_]+")
_FIND_BIN = re.compile(r"find_\w*_bin")
#: Declared distribution names whose import name differs.
_IMPORT_NAME = {"pyelftools": "elftools", "pyyaml": "yaml", "rich-click": "rich_click"}


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _program(node: ast.expr) -> str | None:
    """The literal ``argv[0]`` of a list/tuple display, if it names a program."""
    if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
        return None
    head = node.elts[0]
    if isinstance(head, ast.Constant) and isinstance(head.value, str):
        return head.value if _PROGRAM.fullmatch(head.value) else None
    return None


@cache
def _declared_imports() -> frozenset[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    names = set()
    for req in data["project"]["dependencies"]:
        dist = re.split(r"[<>=!~;\[ ]", req, maxsplit=1)[0].strip().lower()
        names.add(_IMPORT_NAME.get(dist, dist.replace("-", "_")))
    return frozenset(names)


def _optional_imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        guards = " ".join(ast.unparse(h.type) for h in node.handlers if h.type)
        if "ImportError" not in guards and "ModuleNotFoundError" not in guards:
            continue
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Import):
                    found.update(a.name.split(".")[0] for a in sub.names)
                elif isinstance(sub, ast.ImportFrom) and sub.level == 0 and sub.module:
                    found.add(sub.module.split(".")[0])
    return {
        m
        for m in found
        if m not in sys.stdlib_module_names
        and m not in _declared_imports()
        and m != "abicheck"
    }


def scan_sites(source: str, rel: str) -> set[str]:
    """Every lookup site *source* (the module at repo path *rel*) contains."""
    tree = ast.parse(source)
    sites: set[str] = {f"import:{m}" for m in _optional_imports(tree)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name == "which" and node.args:
                sites.add(f"which:{rel}:{ast.unparse(node.args[0])}")
            elif name in _SUBPROCESS_CALLS and node.args:
                program = _program(node.args[0])
                if program:
                    sites.add(f"argv:{rel}:{program}")
        elif isinstance(node, ast.Return) and node.value is not None:
            values = [node.value]
            if isinstance(node.value, ast.Tuple):
                values += node.value.elts
            for value in values:
                # Only a list display reads as a command; a returned tuple of
                # strings is a record, not an argv.
                program = _program(value) if isinstance(value, ast.List) else None
                if program:
                    sites.add(f"argv:{rel}:{program}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _FIND_BIN.fullmatch(node.name):
                sites.add(f"find_bin:{rel}:{node.name}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _ENV_NAME.fullmatch(node.value):
                sites.add(f"env:{node.value}")
    return sites


@cache
def discovered_sites() -> frozenset[str]:
    sites: set[str] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        sites |= scan_sites(path.read_text(encoding="utf-8"), rel)
    return frozenset(sites)


def _registered() -> Counter[str]:
    return Counter(site for entry in INVENTORY for site in entry.sites)


def test_every_lookup_site_has_a_registered_expectation() -> None:
    missing = sorted(discovered_sites() - set(_registered()))
    assert not missing, (
        "These optional-tool lookups have no default-install expectation. Add "
        "each site to an entry in tests/optional_tool_inventory.py (or a new "
        "entry with a role, `without`, and for optional enrichment a proof in "
        "tests/test_default_install_fact_invariants.py):\n  " + "\n  ".join(missing)
    )


def test_every_registered_site_still_exists() -> None:
    stale = sorted(set(_registered()) - discovered_sites())
    assert not stale, (
        "tests/optional_tool_inventory.py registers sites abicheck/ no longer "
        "contains; remove or rename them:\n  " + "\n  ".join(stale)
    )


def test_each_site_is_registered_once() -> None:
    twice = sorted(site for site, n in _registered().items() if n > 1)
    assert not twice, twice


def test_entry_ids_are_unique() -> None:
    ids = Counter(e.id for e in INVENTORY)
    assert [i for i, n in ids.items() if n > 1] == []


@pytest.mark.parametrize("entry", INVENTORY, ids=lambda e: e.id)
def test_entry_states_its_expectation(entry) -> None:
    assert entry.without.strip(), entry.id
    assert set(entry.depths) <= DEPTHS, entry.depths
    if entry.role is Role.OPTIONAL_ENRICHMENT:
        # The whole point: an optional tool must say what survives without it,
        # and the invariant suite must prove it.
        assert entry.preserved, (
            f"{entry.id}: optional enrichment names no preserved family"
        )
        assert entry.depths, entry.id
        assert any(
            p.startswith("tests/test_default_install_fact_invariants.py::")
            for p in entry.proof
        ), f"{entry.id}: no proof in the default-install invariant suite"
    if entry.marked:
        assert not set(entry.marked) & set(entry.preserved), entry.id
    if entry.binaries or entry.modules:
        assert entry.role in (Role.OPTIONAL_ENRICHMENT, Role.INPUT_ACQUISITION), (
            f"{entry.id}: only optional tools are hidden by the invariant suite"
        )


def _defined_tests(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name.startswith("test")
    }


@pytest.mark.parametrize(
    "node",
    sorted({p for e in INVENTORY for p in e.proof}),
)
def test_every_proof_names_an_existing_test(node: str) -> None:
    file, _, name = node.partition("::")
    path = REPO / file
    assert path.is_file(), node
    assert name.split("::")[-1] in _defined_tests(path), node


def test_required_frontends_are_never_hidden() -> None:
    """Hiding the frontend a depth is defined by is not a default install."""
    assert not hidden_binaries() & {"castxml", "clang", "clang++", "gcc", "g++", "cc"}


# --- the scanner itself ------------------------------------------------------


_SAMPLE = """
import os, shutil, subprocess
try:
    import cxxfilt_like
except ImportError:
    cxxfilt_like = None
try:
    import yaml
except ImportError:
    yaml = None
TOOL_ENV = "ABICHECK_SAMPLE_TOOL"
def find_sample_bin():
    return os.environ.get(TOOL_ENV) or shutil.which("sample-tool")
def cmds():
    return (["sample-filt", "-n"], ["sample-filt"])
def run():
    subprocess.run(["sample-run", "x"], check=False)
    subprocess.run(["-not-a-program"], check=False)
    shutil.which(self_bin)
NOT_AN_ENV = "abicheck_lower"
"""


def test_scanner_finds_every_lookup_shape() -> None:
    sites = scan_sites(_SAMPLE, "abicheck/sample.py")
    assert sites == {
        "import:cxxfilt_like",
        "env:ABICHECK_SAMPLE_TOOL",
        "find_bin:abicheck/sample.py:find_sample_bin",
        "which:abicheck/sample.py:'sample-tool'",
        "which:abicheck/sample.py:self_bin",
        "argv:abicheck/sample.py:sample-filt",
        "argv:abicheck/sample.py:sample-run",
    }


def test_scanner_flags_the_layout_tool_shape_as_unregistered() -> None:
    """The original escape: a find_*_bin + env + which lookup with no entry."""
    sites = scan_sites(
        'E = "ABICHECK_NEW_COMPANION"\n'
        "def find_new_companion_bin():\n"
        "    return os.environ.get(E) or shutil.which('abicheck-new-companion')\n",
        "abicheck/extract/new_companion.py",
    )
    assert sites, sites
    assert not sites & set(_registered())
