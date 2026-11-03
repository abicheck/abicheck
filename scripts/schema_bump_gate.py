#!/usr/bin/env python3
"""``schema-bump-reason`` gate: a global ``SCHEMA_VERSION`` bump owes a
declared reason it is not an extension fact (ADR-062 D19).

A leaf module imported by ``check_ai_readiness.py`` (already past the
2000-line hard cap, like ``generation_corpus_gate.py``). Pure stdlib: it
reads ``abicheck/storage/snapshot_schema_versions.py`` with ``ast`` and never
imports ``abicheck``, so it runs before ``pip install``.

For every version ``SCHEMA_BUMP_RULE_BASELINE + 1 .. SCHEMA_VERSION`` it
requires one literal ``SCHEMA_BUMP_REASONS`` row ``SchemaBump(kind,
why_not_extension)`` whose kind is ``envelope`` or ``wire`` and whose reason
is a real sentence, not a placeholder. It refuses a row for a version not
allocated (stale or premature), and a baseline other than the one pinned
here, so the gate cannot be passed by moving the baseline.

An optional new fact -- one whose absence an older reader already reads as
"not collected" -- is declared in ``storage/extension_fact_catalog.py`` and
bumps nothing; that is the change this gate pushes a bump towards.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = ROOT / "abicheck" / "storage" / "snapshot_schema_versions.py"
CHECK = "schema-bump-reason"

#: The version ADR-062 D19 was adopted at. Pinned here, not only in the
#: module, so raising the module's baseline cannot waive a bump.
PINNED_BASELINE = 69
KINDS = frozenset({"envelope", "wire"})
MIN_REASON_CHARS = 40
PLACEHOLDERS = frozenset(
    {"", "tbd", "todo", "n/a", "na", "none", "-", "?", "wip", "fixme", "later"}
)


class _FindingsLike(Protocol):
    def err(self, check: str, msg: str) -> None: ...


def _assigned(tree: ast.Module, name: str) -> ast.expr | None:
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                return node.value
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value
    return None


def _int_literal(tree: ast.Module, name: str) -> int | None:
    value = _assigned(tree, name)
    if (
        isinstance(value, ast.Constant)
        and isinstance(value.value, int)
        and not isinstance(value.value, bool)
    ):
        return value.value
    return None


def _row(call: ast.expr) -> tuple[str | None, str | None]:
    """``(kind, reason)`` of one ``SchemaBump(...)`` literal; ``None`` for a
    part that is not a literal string."""
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "SchemaBump"
    ):
        return None, None
    parts: dict[str, ast.expr] = dict(zip(("kind", "why_not_extension"), call.args))
    parts.update({kw.arg: kw.value for kw in call.keywords if kw.arg})

    def text(node: ast.expr | None) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    return text(parts.get("kind")), text(parts.get("why_not_extension"))


def evaluate(source: str) -> list[str]:
    """Every problem with *source* (the versions module's text)."""
    tree = ast.parse(source)
    current = _int_literal(tree, "SCHEMA_VERSION")
    baseline = _int_literal(tree, "SCHEMA_BUMP_RULE_BASELINE")
    if current is None:
        return ["SCHEMA_VERSION is not a literal int"]
    if baseline != PINNED_BASELINE:
        return [
            f"SCHEMA_BUMP_RULE_BASELINE is {baseline!r}, not the pinned "
            f"{PINNED_BASELINE} (ADR-062 D19); the baseline does not move"
        ]
    table = _assigned(tree, "SCHEMA_BUMP_REASONS")
    if not isinstance(table, ast.Dict):
        return ["SCHEMA_BUMP_REASONS is not a literal dict"]
    problems: list[str] = []
    rows: dict[int, tuple[str | None, str | None]] = {}
    for key, value in zip(table.keys, table.values, strict=True):
        if not (
            isinstance(key, ast.Constant)
            and isinstance(key.value, int)
            and not isinstance(key.value, bool)
        ):
            problems.append("SCHEMA_BUMP_REASONS has a key that is not a literal int")
            continue
        if key.value in rows:
            problems.append(f"SCHEMA_BUMP_REASONS states v{key.value} twice")
        rows[key.value] = _row(value)
    for version in sorted(rows):
        if not baseline < version <= current:
            problems.append(
                f"SCHEMA_BUMP_REASONS row v{version} is outside the bumps "
                f"v{baseline + 1}..v{current}: a stale or premature row"
            )
    for version in range(baseline + 1, current + 1):
        if version not in rows:
            problems.append(
                f"SCHEMA_VERSION bump to v{version} has no SCHEMA_BUMP_REASONS "
                "row: state why it is an envelope or wire change, or store the "
                "new fact as an extension fact instead (ADR-062 D19, "
                "storage/extension_fact_catalog.py)"
            )
            continue
        kind, reason = rows[version]
        if kind not in KINDS:
            problems.append(
                f"v{version}: kind {kind!r} is not one of {sorted(KINDS)} (a "
                "literal string)"
            )
        stripped = (reason or "").strip()
        if stripped.lower() in PLACEHOLDERS or len(stripped) < MIN_REASON_CHARS:
            problems.append(
                f"v{version}: why_not_extension must be a literal sentence of at "
                f"least {MIN_REASON_CHARS} characters saying what an older "
                "reader would misread, not a placeholder"
            )
    return problems


def check_schema_bump_reasons(f: _FindingsLike) -> None:
    for problem in evaluate(VERSIONS.read_text(encoding="utf-8")):
        f.err(CHECK, problem)


if __name__ == "__main__":
    import sys

    found = evaluate(VERSIONS.read_text(encoding="utf-8"))
    for line in found:
        print(f"{CHECK}: {line}")
    sys.exit(1 if found else 0)
