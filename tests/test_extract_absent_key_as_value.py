"""No new extractor reads an absent AST/debug key as a confirmed value.

Bug class ``evidence.unread_producer_read_as_confirmed_absence``: in the
clang header extractor ``bool(node.get("explicit"))`` turned "Clang never
serializes this key" into a confirmed ``False`` on both sides, hiding a
ctor-became-explicit break (fixed in
``abicheck/extract/headers/clang/explicit_specifier.py``). The two shapes
that collapse *absent* into *False* are ``bool(x.get("k"))`` and
``x.get("k", False)``. This AST scan flags both under ``abicheck/extract/``;
the existing sites are baselined (``file:function:key``) for human triage
and the baseline may only shrink. A genuinely boolean-by-absence key (the
producer documents "omitted means false") goes through a named helper that
states that, not through a new baseline entry.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_scan

_ROOT = Path(__file__).resolve().parents[1]
_EXTRACT = _ROOT / "abicheck" / "extract"

#: Existing sites, awaiting triage. Shrink-only.
_BASELINE = frozenset(
    {
        "abicheck/extract/headers/castxml/variable_template_join.py:_maybe_record:constexpr",
        "abicheck/extract/headers/clang/expr.py:is_const_variable:constexpr",
        "abicheck/extract/headers/clang/functions.py:parse_functions:explicitlyDeleted",
        "abicheck/extract/headers/clang/functions.py:parse_functions:pure",
        "abicheck/extract/headers/clang/functions.py:parse_functions:variadic",
        "abicheck/extract/headers/clang/functions.py:parse_functions:virtual",
        "abicheck/extract/headers/clang/layout_tool.py:run_layout_tool._run_shimmed:ok",
        "abicheck/extract/headers/clang/qualifiers.py:clang_record_is_abstract:isAbstract",
        "abicheck/extract/headers/clang/qualifiers.py:clang_record_type_traits:isStandardLayout",
        "abicheck/extract/headers/clang/qualifiers.py:clang_record_type_traits:isTriviallyCopyable",
        "abicheck/extract/headers/clang/records.py:_make_field:mutable",
        "abicheck/extract/headers/clang/scope.py:scope_segment_for:isInline",
        "abicheck/extract/headers/clang/vtable.py:_collect_virtual_slots:virtual",
    }
)


def _get_key(node: ast.AST, nargs: int) -> str | None:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and len(node.args) == nargs
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return node.args[0].value
    return None


def absent_as_value_keys(tree: ast.AST) -> list[tuple[str, str]]:
    """``(qualified function, key)`` for each absent-read-as-False site."""
    found: list[tuple[str, str]] = []

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = (*scope, node.name)
        if isinstance(node, ast.Call):
            key = None
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "bool"
                and len(node.args) == 1
            ):
                key = _get_key(node.args[0], 1)
            elif (
                len(node.args) == 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value is False
            ):
                key = _get_key(node, 2)
            if key is not None:
                found.append((".".join(scope) or "<module>", key))
        for child in ast.iter_child_nodes(node):
            walk(child, scope)

    walk(tree, ())
    return found


def _scan() -> set[str]:
    sites: set[str] = set()
    for path in sorted(_EXTRACT.rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        sites.update(f"{rel}:{fn}:{key}" for fn, key in absent_as_value_keys(tree))
    return sites


def test_no_new_absent_key_read_as_value() -> None:
    new = sorted(_scan() - _BASELINE)
    assert not new, (
        "absent AST/debug key read as a confirmed value -- record "
        f"NOT_COLLECTED/unknown when the producer omits the key: {new}"
    )


def test_baseline_only_shrinks() -> None:
    stale = sorted(_BASELINE - _scan())
    assert not stale, f"fixed sites -- drop from _BASELINE: {stale}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def f(n):\n    return bool(n.get('explicit'))", [("f", "explicit")]),
        ("def f(n):\n    return n.get('pure', False)", [("f", "pure")]),
        ("def f(n):\n    return n.get('pure', None)", []),
        ("def f(n):\n    return bool(n.get(k))", []),
        ("def f(n):\n    return bool(n.get('a', 0))", []),
        (
            "class C:\n    def m(self, n):\n        return n.get('v', False)",
            [("m", "v")],
        ),
    ],
)
def test_detector_shapes(source: str, expected: list[tuple[str, str]]) -> None:
    assert absent_as_value_keys(ast.parse(source)) == expected
