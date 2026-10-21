#!/usr/bin/env python3
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

"""The observed half of ``backend_capabilities.py``: what the two header-AST
backends' source actually constructs, read with :mod:`ast`.

Split out of ``backend_capabilities.py`` (whose ``FACT_ROWS`` data grows by one
row per new model field) so the claimed matrix and the scanner that checks it
against the parser source live in separate files. ``backend_capabilities``
re-exports every public name here, so callers keep importing from it.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from enum import Enum
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_DIR / "abicheck"

#: The six declaration dataclasses this matrix covers — the model types the two
#: header-AST backends construct. Everything else in ``AbiSnapshot`` is either
#: whole-snapshot metadata or a binary-format sub-model no header parse touches.
COVERED_MODEL_CLASSES: tuple[str, ...] = (
    "Function",
    "Variable",
    "TypeField",
    "RecordType",
    "EnumType",
    "Param",
)


# ---------------------------------------------------------------------------
# Observed evidence — what the two parsers actually do
# ---------------------------------------------------------------------------


class Evidence(str, Enum):
    """What a backend's source says about one field, structurally."""

    #: The backend passes this keyword with a real expression.
    EXTRACTED = "extracted"
    #: The keyword is passed, but as a hardcoded literal (``None``/``False``/
    #: ``[]``), which is a placeholder rather than an extracted fact.
    LITERAL = "literal"
    #: The backend never passes this keyword at all.
    ABSENT = "absent"


#: `Fact` factory methods that never carry a determined value — a call to one
#: of these states a fixed *status* regardless of what the parse actually saw,
#: so it is a placeholder exactly like a hardcoded ``None``/``False`` literal
#: would be, even though structurally it is an `ast.Call`, not an
#: `ast.Constant`. `present`/`partial` are the opposite: their value *is* the
#: extracted fact, so a call to one of those is examined by recursing into its
#: own first argument instead (see `_is_placeholder_literal`).
_FACT_STATUS_ONLY_METHODS = frozenset(
    {"unsupported", "not_collected", "not_applicable", "failed"}
)
_FACT_VALUE_METHODS = frozenset({"present", "partial"})


def _is_placeholder_literal(node: ast.expr) -> bool:
    """Whether a keyword's value is a hardcoded default rather than a parse.

    ``Param(is_restrict=False)`` names the field but extracts nothing, and is
    exactly what a claim of "this backend populates it" must not be built on.
    A constant, or an empty container, is that shape; anything else (a name, a
    call, a comprehension, a conditional) reads a real value off the AST —
    *except* a `Fact.unsupported()`-shaped call (`_FACT_STATUS_ONLY_METHODS`),
    which is a placeholder no matter how it's spelled, since it carries no
    value at all; and a `Fact.present(...)`/`Fact.partial(...)` call
    (`_FACT_VALUE_METHODS`), which is examined by recursing into its own
    wrapped value instead of being treated as extraction on the strength of
    merely being a call.
    """
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "Fact"
    ):
        if node.func.attr in _FACT_STATUS_ONLY_METHODS:
            return True
        if node.func.attr in _FACT_VALUE_METHODS and node.args:
            return _is_placeholder_literal(node.args[0])
    return False


def scan_backend_evidence(
    module_paths: Path | Iterable[Path],
) -> dict[str, dict[str, Evidence]]:
    """``{class name: {field: Evidence}}`` for one backend's module(s).

    Reads the module(s) with :mod:`ast` rather than by regex: a keyword's
    value spans several lines often enough (``bases=[] if is_opaque else
    [...]``) that a line-oriented match reports the wrong answer — an early
    version of this scanner called both `bases` and `virtual_bases`
    hardcoded-empty on the castxml backend for exactly that reason.

    A single path scans one module; a backend that has started splitting
    entity parsing out of its flat facade (ADR-061 D9 — see
    ``abicheck/extract/AGENTS.md``) passes every module a constructor could
    now live in, since the facade's own AST no longer shows evidence for a
    fact an ``extract``-owned entity module now populates.

    A field constructed in more than one place counts as ``EXTRACTED`` if ANY
    construction extracts it (``dumper_castxml`` builds ``TypeField`` from two
    call sites) — this holds across files too, so a field's home moving from
    the facade to an entity module changes nothing about the merged verdict.

    **Constructor keywords only.** An earlier version also counted an
    attribute assignment (``field.access = ...``) as evidence, on the theory
    that a backend might finish a declaration after constructing it. Static
    analysis cannot tell *which* class such a target belongs to, so that
    evidence was applied to every covered class carrying a same-named field —
    real cross-class contamination (CodeRabbit review). It was also inert:
    removing it changes no verdict for either backend today. Should a backend
    start populating a field post-construction, this scanner reports the
    constructor's answer and the row's claim fails
    ``test_matrix_claims_match_parser_source`` — a loud, locatable failure
    rather than a silently wrong published matrix.
    """
    paths = [module_paths] if isinstance(module_paths, Path) else list(module_paths)
    out: dict[str, dict[str, Evidence]] = {c: {} for c in COVERED_MODEL_CLASSES}

    def record(cls: str, field: str, evidence: Evidence) -> None:
        """Keep the strongest evidence seen, so one placeholder among several
        construction sites (in one file or across files) cannot mask a real
        extraction elsewhere."""
        if out[cls].get(field) is not Evidence.EXTRACTED:
            out[cls][field] = evidence

    for module_path in paths:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in out
            ):
                for kw in node.keywords:
                    if kw.arg is None:
                        continue
                    record(
                        node.func.id,
                        kw.arg,
                        Evidence.LITERAL
                        if _is_placeholder_literal(kw.value)
                        else Evidence.EXTRACTED,
                    )
    return out


def _entity_module_paths(header_backend_dir: Path) -> list[Path]:
    """Every entity-parsing module already split out of a backend's facade.

    Sorted for deterministic scan order; excludes ``__init__.py`` (package
    marker, never a construction site) and non-parsing leaves that construct
    nothing (``context.py`` holds only per-parser state, not entity output —
    harmless either way, since a module with no covered-class constructor
    call contributes no evidence, but excluding it keeps this scanner's own
    intent — "entity modules" — honest).
    """
    if not header_backend_dir.is_dir():
        return []
    excluded = {"__init__.py", "context.py"}
    return sorted(p for p in header_backend_dir.glob("*.py") if p.name not in excluded)


def castxml_evidence() -> dict[str, dict[str, Evidence]]:
    """What the castxml backend (``extract/headers/castxml/``) populates."""
    return scan_backend_evidence(
        _entity_module_paths(PKG_DIR / "extract" / "headers" / "castxml")
    )


def clang_evidence() -> dict[str, dict[str, Evidence]]:
    """What ``dumper_clang.py`` and its split-out entity modules populate."""
    return scan_backend_evidence(
        [PKG_DIR / "dumper_clang.py"]
        + _entity_module_paths(PKG_DIR / "extract" / "headers" / "clang")
    )
