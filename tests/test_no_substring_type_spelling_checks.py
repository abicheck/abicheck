"""No ``"<TypeTag>" in <spelling>`` substring test on a type spelling.

``"FunctionType" in type_str`` also matches real names such as
``MyFunctionTypeWrapper*``, ``FunctionTypeT`` or ``struct FunctionTypeHelper&``.
Those were mistaken for castxml's opaque tag in ``idioms`` and in
``policy.generation_drift_demotion`` before the whole-token predicates in
``model.castxml_spelling_artifacts`` replaced them. This gate fails if the
pattern comes back in ``abicheck/``.

The scan walks the AST, not the text, so a docstring or comment that quotes
the old pattern (as the shared module's own docs do) does not trip it. Only
a membership comparison whose left operand is a literal tag name, or whose
right operand is a literal tag name, is a finding.

An exact-membership test that happens to use the same literal is allowed
only through ``ALLOWLIST`` with a reason. Any new entry is a review decision.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "abicheck"

#: Literal spellings whose substring test on a type spelling is the bug class.
#: castxml/clang kind names plus the castxml wrapper word ``_Atomic``.
TAG_MARKERS = frozenset(
    {
        "FunctionType",
        "PointerType",
        "ReferenceType",
        "ArrayType",
        "RecordType",
        "TypedefType",
        "ElaboratedType",
        "_Atomic",
    }
)

#: ``"<marker>" in <expr>`` sites that are exact membership, not a substring
#: test, keyed ``relpath::function::marker``. Each reason must say why the
#: operand cannot be a spelling that contains the literal as part of a name.
ALLOWLIST: dict[str, str] = {
    "abicheck/policy/generation_drift_demotion.py::_is_opaque_callback_respelled::FunctionType": (
        "``declared`` is a frozenset of identifier tokens built by "
        "``_declared_names``, so this is exact token membership, not a substring test"
    ),
    "abicheck/policy/generation_drift_demotion.py::_is_opaque_callback_respelled::_Atomic": (
        "reached only after ``is_castxml_opaque_function_type(old)`` accepted "
        "``old``; the only identifiers that predicate admits are the tag, cv "
        "words and the ``_Atomic(`` wrapper, so the substring is that wrapper"
    ),
}


class _MembershipScanner(ast.NodeVisitor):
    """Collects ``"<marker>" in x`` and ``x in "<marker>"`` comparisons,
    keyed by the enclosing function name (``<module>`` at top level)."""

    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self._scope: list[str] = ["<module>"]
        self.findings: list[tuple[str, int]] = []

    def _enter(self, node: ast.AST, name: str) -> None:
        self._scope.append(name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter(node, node.name)

    def visit_Compare(self, node: ast.Compare) -> None:
        left = node.left
        for op, right in zip(node.ops, node.comparators, strict=True):
            if isinstance(op, (ast.In, ast.NotIn)):
                for operand in (left, right):
                    if (
                        isinstance(operand, ast.Constant)
                        and isinstance(operand.value, str)
                        and operand.value in TAG_MARKERS
                    ):
                        key = f"{self.relpath}::{self._scope[-1]}::{operand.value}"
                        self.findings.append((key, node.lineno))
            left = right
        self.generic_visit(node)


def scan_source(
    source: str, relpath: str = "abicheck/example.py"
) -> list[tuple[str, int]]:
    scanner = _MembershipScanner(relpath)
    scanner.visit(ast.parse(source))
    return scanner.findings


def _package_findings() -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relpath = path.relative_to(REPO).as_posix()
        found.extend(scan_source(path.read_text(encoding="utf-8"), relpath))
    return found


@pytest.mark.repo_scan
def test_no_substring_test_on_a_type_tag_in_abicheck() -> None:
    bad = sorted(
        {
            f"{key} (line {line})"
            for key, line in _package_findings()
            if key not in ALLOWLIST
        }
    )
    assert not bad, (
        "substring membership test on a type-spelling tag -- use a whole-token "
        "predicate (model.castxml_spelling_artifacts) instead of `in`, or add a "
        "reasoned ALLOWLIST entry:\n" + "\n".join(bad)
    )


@pytest.mark.repo_scan
def test_allowlist_has_no_stale_entry() -> None:
    found = {key for key, _ in _package_findings()}
    stale = sorted(set(ALLOWLIST) - found)
    assert not stale, "ALLOWLIST names sites the scan no longer finds:\n" + "\n".join(
        stale
    )


def test_allowlist_states_a_reason_for_every_entry() -> None:
    assert all(len(reason) > 30 for reason in ALLOWLIST.values())


# -- the scanner's own contract (independent of today's tree) ---------------


@pytest.mark.parametrize(
    ("source", "expected_markers"),
    [
        pytest.param(
            'def f(type_str):\n    return "FunctionType" in type_str\n',
            ["FunctionType"],
            id="literal-left-substring",
        ),
        pytest.param(
            'def f(s):\n    return s in "PointerType"\n',
            ["PointerType"],
            id="literal-right-substring",
        ),
        pytest.param(
            'def f(old):\n    return not ("_Atomic" in old)\n',
            ["_Atomic"],
            id="negated",
        ),
        pytest.param(
            'def f(x):\n    return "ReferenceType" not in x\n',
            ["ReferenceType"],
            id="not-in",
        ),
        pytest.param(
            'def f(t):\n    return "FunctionType" in t and "ArrayType" in t\n',
            ["FunctionType", "ArrayType"],
            id="two-markers",
        ),
    ],
)
def test_scanner_flags_each_tag_membership_form(
    source: str, expected_markers: list[str]
) -> None:
    markers = sorted(key.rsplit("::", 1)[1] for key, _ in scan_source(source))
    assert markers == sorted(expected_markers)


@pytest.mark.parametrize(
    "source",
    [
        # a docstring or comment quoting the old pattern is text, not code
        '"""Old: ``"FunctionType" in raw_type`` matched MyFunctionTypeWrapper."""\n',
        '# "FunctionType" in raw_type  (the bug this gate guards)\nx = 1\n',
        # the whole-token predicate itself
        "from abicheck.model.castxml_spelling_artifacts import "
        "mentions_function_type_tag\nok = mentions_function_type_tag(s)\n",
        # membership on a non-tag literal (dict key) is not a tag check
        'def f(d):\n    return "qualType" in d\n',
        # the tag as a variable, not a literal, is out of this gate's scope
        "def f(t, tag):\n    return tag in t\n",
    ],
)
def test_scanner_ignores_text_and_non_tag_membership(source: str) -> None:
    assert scan_source(source) == []


def test_scanner_keys_findings_by_enclosing_function() -> None:
    source = 'def outer():\n    def inner(s):\n        return "PointerType" in s\n'
    [(key, line)] = scan_source(source, "abicheck/demo.py")
    assert key == "abicheck/demo.py::inner::PointerType"
    assert line == 3
