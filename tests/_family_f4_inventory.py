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

"""Mechanical inventory of name/spelling heuristic sites for harness H4.

An AST scan (never a hand-kept list) over the decision-making modules --
``compare/``, ``policy/``, ``diff_*.py``, ``detectors*``, ``checker*``,
``classify*`` -- that records every place a name's *shape* can steer a
decision:

* ``re``: ``re.compile(<literal>)`` and module-level ``re.search``/
  ``match``/``fullmatch``/``sub``/``split``/``findall``/``finditer`` with a
  literal pattern;
* ``affix``: ``.endswith``/``.startswith``/``.removesuffix``/
  ``.removeprefix`` calls (any argument -- a suffix tuple bound to a name is
  still a suffix set);
* ``fnmatch``: ``fnmatch``/``fnmatchcase`` calls;
* ``vocab``: a module-level constant whose name says it is a name-shape
  vocabulary (``*_SUFFIX*``/``*_PREFIX*``/``*_TOKEN*``/``*_NAMESPACE*``/...)
  bound to a literal collection of strings -- how a segment-membership
  heuristic such as ``s in DEFAULT_EXPERIMENTAL_NAMESPACES`` is spelled.

Keys are ``module::qualname::kind:pattern`` -- no line numbers, so an edit
that only moves code does not churn the registry.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from _mutmut_names import canonical_def_name

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "abicheck"

SCAN_GLOBS = (
    "compare/**/*.py",
    "policy/**/*.py",
    "diff_*.py",
    "detectors*.py",
    "checker*.py",
    "classify*.py",
    # Flat-root policy owner of the internal-namespace demotion (#1231); it
    # decides contract membership although it predates the policy/ package.
    "internal_leak*.py",
)

_VOCAB_NAME = re.compile(
    r"SUFFIX|PREFIX|TOKEN|PATTERN|NAMESPACE|LEAVES|LEAF|TAIL|MARKER|_NAMES$|_RE$"
)
_RE_FUNCS = frozenset(
    {"search", "match", "fullmatch", "sub", "split", "findall", "finditer"}
)
_AFFIX = frozenset({"endswith", "startswith", "removesuffix", "removeprefix"})
_MAX_PATTERN = 72


def scanned_files() -> list[Path]:
    out: set[Path] = set()
    for pattern in SCAN_GLOBS:
        out.update(p for p in PKG.glob(pattern) if "__pycache__" not in p.parts)
    return sorted(out)


def _module_name(path: Path) -> str:
    rel = path.relative_to(REPO).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_PATTERN else text[: _MAX_PATTERN - 3] + "..."


def _is_str_collection(node: ast.expr) -> bool:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"frozenset", "set", "tuple"}
        and node.args
    ):
        node = node.args[0]
    if not isinstance(node, ast.Tuple | ast.Set | ast.List) or not node.elts:
        return False

    def is_str(e: ast.expr) -> bool:
        return isinstance(e, ast.Constant) and isinstance(e.value, str)

    return all(
        is_str(e) or (isinstance(e, ast.Tuple) and all(is_str(x) for x in e.elts))
        for e in node.elts
    )


class _Visitor(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.stack: list[str] = []
        self.sites: set[str] = set()

    def _add(self, kind: str, pattern: str) -> None:
        qual = ".".join(self.stack) or "<module>"
        self.sites.add(f"{self.module}::{qual}::{kind}:{_short(pattern)}")

    def _scoped(self, node: ast.AST, name: str) -> None:
        self.stack.append(name)
        self.generic_visit(node)
        self.stack.pop()

    def _visit_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        name = canonical_def_name(node.name)
        if name is not None:  # skip mutmut's per-mutant copies
            self._scoped(node, name)

    visit_FunctionDef = _visit_def
    visit_AsyncFunctionDef = _visit_def

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scoped(node, node.name)

    def visit_Call(self, node: ast.Call) -> None:
        fn = node.func
        if isinstance(fn, ast.Attribute):
            if isinstance(fn.value, ast.Name) and fn.value.id == "re":
                if fn.attr == "compile" or fn.attr in _RE_FUNCS:
                    if node.args and isinstance(node.args[0], ast.Constant):
                        self._add("re", repr(node.args[0].value))
                    elif fn.attr == "compile" and node.args:
                        self._add("re", ast.unparse(node.args[0]))
            elif fn.attr in _AFFIX and node.args:
                self._add(f"affix.{fn.attr}", ast.unparse(node.args[0]))
            elif fn.attr in {"fnmatch", "fnmatchcase"} and len(node.args) > 1:
                self._add("fnmatch", ast.unparse(node.args[1]))
        elif isinstance(fn, ast.Name) and fn.id in {"fnmatch", "fnmatchcase"}:
            if len(node.args) > 1:
                self._add("fnmatch", ast.unparse(node.args[1]))
        self.generic_visit(node)


def _vocab_sites(module: str, tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in tree.body:
        target: str | None = None
        value: ast.expr | None = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        if target and value is not None and _VOCAB_NAME.search(target):
            if _is_str_collection(value):
                out.add(f"{module}::<module>::vocab:{target}")
    return out


def heuristic_site_inventory() -> set[str]:
    """Every name-shape heuristic site in the scanned decision modules."""
    sites: set[str] = set()
    for path in scanned_files():
        module = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        v = _Visitor(module)
        v.visit(tree)
        sites |= v.sites
        sites |= _vocab_sites(module, tree)
    return sites
