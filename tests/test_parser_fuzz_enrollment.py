"""Every parser entry point in ``abicheck/`` is enrolled in the parser fuzz oracle.

The registry is ``_parser_fuzz_oracle.ENROLLED`` (fuzzed, with the tests that
fuzz it) and ``NOT_ENROLLED`` (deliberately not fuzzed yet, with the reason).
A parser entry point is discovered, not listed by hand:

* a top-level ``parse_<format>_metadata`` function (the platform/debug readers);
* a top-level class defining both ``parse_functions`` and ``parse_types`` (the
  header AST parsers), ``Protocol`` classes excepted;
* a module that calls ``note_export_read_failure`` (an export-read failure site)
  must be the module of, or covered by, an enrolled entry.

A new parser that lands in neither table fails here, so it cannot silently miss
the oracle the way the Mach-O and clang parsers once did.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest
from _parser_fuzz_oracle import ENROLLED, NOT_ENROLLED, REPO_ROOT

pytestmark = pytest.mark.repo_scan

PACKAGE = REPO_ROOT / "abicheck"
FUZZ_TESTS = Path(__file__).with_name("test_parser_fuzz_oracle.py")
_METADATA_PARSER = re.compile(r"^parse_[a-z]+_metadata$")
_FAILURE_SITE = "abicheck.extract.parse_failures"


def _module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
    return ".".join(parts)


def _is_protocol(node: ast.ClassDef) -> bool:
    return any(
        (isinstance(b, ast.Name) and b.id == "Protocol")
        or (isinstance(b, ast.Attribute) and b.attr == "Protocol")
        for b in node.bases
    )


def _calls(tree: ast.AST, func: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else getattr(target, "attr", None)
            )
            if name == func:
                return True
    return False


def discover(package: Path) -> tuple[set[str], set[str]]:
    """``(entry points, failure-recording modules)`` found under *package*."""
    entries: set[str] = set()
    failure_sites: set[str] = set()
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module = _module_name(path, package.parent)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and _METADATA_PARSER.match(node.name):
                entries.add(f"{module}:{node.name}")
            elif isinstance(node, ast.ClassDef) and not _is_protocol(node):
                methods = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
                if {"parse_functions", "parse_types"} <= methods:
                    entries.add(f"{module}:{node.name}")
        if module != _FAILURE_SITE and _calls(tree, "note_export_read_failure"):
            failure_sites.add(module)
    return entries, failure_sites


def test_every_discovered_parser_is_enrolled_or_excluded_with_a_reason() -> None:
    entries, _ = discover(PACKAGE)
    enrolled = {e.entry for e in ENROLLED}
    missing = sorted(entries - enrolled - set(NOT_ENROLLED))
    assert not missing, (
        "parser entry points the fuzz oracle does not cover; enroll them in "
        f"tests/_parser_fuzz_oracle.py ENROLLED or NOT_ENROLLED: {missing}"
    )
    assert all(reason.strip() for reason in NOT_ENROLLED.values())


def test_every_failure_recording_module_is_covered() -> None:
    _, failure_sites = discover(PACKAGE)
    covered = {e.module for e in ENROLLED} | {m for e in ENROLLED for m in e.covers}
    uncovered = sorted(failure_sites - covered)
    assert not uncovered, (
        f"export-read failure sites no fuzz entry reaches: {uncovered}"
    )


def test_registry_entries_resolve_and_name_real_tests() -> None:
    fuzz_tree = ast.parse(FUZZ_TESTS.read_text(encoding="utf-8"))
    defined = {n.name for n in fuzz_tree.body if isinstance(n, ast.FunctionDef)}
    for entry in [e.entry for e in ENROLLED] + list(NOT_ENROLLED):
        module, _, attr = entry.partition(":")
        assert hasattr(importlib.import_module(module), attr), f"stale entry {entry}"
    for enrollment in ENROLLED:
        assert enrollment.tests, f"{enrollment.entry} names no fuzz test"
        absent = [t for t in enrollment.tests if t not in defined]
        assert not absent, f"{enrollment.entry} names missing tests {absent}"
    assert not set(NOT_ENROLLED) & {e.entry for e in ENROLLED}


def test_discovery_finds_each_shape(tmp_path: Path) -> None:
    """The rules themselves: one hit per shape, and the near misses stay out."""
    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "fmt.py").write_text(
        "def parse_foo_metadata(p): ...\n"
        "def parse_foo_bar_metadata(p): ...\n"  # two words: not a format reader
        "class P:\n    def parse_functions(self): ...\n    def parse_types(self): ...\n"
        "class Q(Protocol):\n    def parse_functions(self): ...\n    def parse_types(self): ...\n"
        "class R:\n    def parse_functions(self): ...\n"
    )
    (pkg / "site.py").write_text("def f():\n    note_export_read_failure('x')\n")
    entries, sites = discover(pkg)
    assert entries == {"abicheck.fmt:parse_foo_metadata", "abicheck.fmt:P"}
    assert sites == {"abicheck.site"}
