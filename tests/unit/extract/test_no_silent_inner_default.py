# Copyright 2026 Nikolay Petrov
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

"""Static guard: no ``inner`` read may default a malformed value to empty.

``node.get("inner", [])`` and ``node.get("inner") or []`` coerce a present but
non-list ``inner`` (dict, string, number) to an empty walk, so a malformed AST
reads as a complete, empty surface. Use ``ast_inner(node)`` from
``abicheck.extract.headers.clang.ast_inner`` instead, which raises.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

EXTRACT_ROOT = Path(__file__).resolve().parents[3] / "abicheck" / "extract"

#: Known, not-yet-migrated sites, recorded so the guard stays green while they
#: are visible. Each is the same ``or []`` shape read over a clang AST outside
#: ``headers/clang/``. Shrink this set when a site migrates; a new entry needs
#: a maintainer decision. Keyed by (path relative to abicheck/extract, count).
KNOWN_UNMIGRATED: dict[str, int] = {}


def _is_inner_get(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "inner"
    )


def _is_empty_list(node: ast.AST) -> bool:
    return isinstance(node, ast.List) and not node.elts


def _offending_lines(tree: ast.AST) -> list[int]:
    hits: list[int] = []
    for node in ast.walk(tree):
        # node.get("inner", [])
        if (
            _is_inner_get(node)
            and isinstance(node, ast.Call)
            and len(node.args) == 2
            and _is_empty_list(node.args[1])
        ):
            hits.append(node.lineno)
        # node.get("inner") or []  /  node.get("inner", []) or []
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            first, *rest = node.values
            if _is_inner_get(first) and any(_is_empty_list(v) for v in rest):
                hits.append(node.lineno)
    return sorted(set(hits))


@pytest.mark.repo_scan
def test_no_silent_inner_default_under_extract() -> None:
    offenders: list[str] = []
    for path in sorted(EXTRACT_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        lines = _offending_lines(tree)
        rel = path.relative_to(EXTRACT_ROOT).as_posix()
        if len(lines) == KNOWN_UNMIGRATED.get(rel, 0) and lines:
            continue
        for line in lines:
            offenders.append(f"abicheck/extract/{rel}:{line}")
    assert not offenders, (
        "malformed-evidence default found; use ast_inner(node) instead:\n"
        + "\n".join(offenders)
    )
