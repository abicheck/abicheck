#!/usr/bin/env python3
"""``generation-corpus`` gate: an extractor-generation bump owes a spelling
audit and a frozen corpus entry (bug class
``storage.cross_generation_spelling_drift``, plan H1).

A leaf module imported by ``check_ai_readiness.py`` (already past the
2000-line hard cap, like ``adr_status_sync.py``'s split). Pure stdlib: it
reads the sources with ``ast``/``json`` and never imports ``abicheck``, so it
runs before ``pip install``.

For the ``EXTRACTOR_GENERATION`` G that ``abicheck/storage/versioning.py``
declares, it requires:

* ``SPELLING_MIGRATIONS`` in ``abicheck/storage/generation_migrations.py`` to
  hold exactly one row per generation 1..G, each with a reason that is not
  empty and not one of that module's ``PLACEHOLDER_REASONS`` -- the row says
  what the bump's audit found, or which rewrites it declares;
* ``tests/fixtures/generations/manifest.json`` to cover every retired
  generation 1..G-1: a frozen snapshot that exists on disk
  (``scripts/freeze_generation_corpus.py --ref``), or a recorded skip with a
  reason (``--skip N --reason``).

So "bump the generation" can no longer silently mean "old baselines now lie":
forgetting the audit row or the refreeze fails CI.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent.parent
VERSIONING = ROOT / "abicheck" / "storage" / "versioning.py"
MIGRATIONS = ROOT / "abicheck" / "storage" / "generation_migrations.py"
CORPUS = ROOT / "tests" / "fixtures" / "generations"
CHECK = "generation-corpus"

_GENERATION = re.compile(r"^EXTRACTOR_GENERATION\s*=\s*(\d+)", re.MULTILINE)


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


def _placeholders(tree: ast.Module) -> set[str]:
    value = _assigned(tree, "PLACEHOLDER_REASONS")
    if isinstance(value, ast.Call) and value.args:
        literal = ast.literal_eval(value.args[0])
        return {str(s).strip().lower() for s in literal}
    return {""}


def _rows(tree: ast.Module) -> dict[int, str] | None:
    """``generation -> reason`` from the ``SPELLING_MIGRATIONS`` literal; a
    reason that is not a literal string reads as empty."""
    value = _assigned(tree, "SPELLING_MIGRATIONS")
    if not isinstance(value, ast.Dict):
        return None
    rows: dict[int, str] = {}
    for key, item in zip(value.keys, value.values, strict=True):
        if not (isinstance(key, ast.Constant) and isinstance(key.value, int)):
            continue
        reason = ""
        if isinstance(item, ast.Call) and item.args:
            first = item.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                reason = first.value
        rows[key.value] = reason
    return rows


def evaluate(
    versioning_text: str, migrations_text: str, manifest: dict | None, corpus: Path
) -> list[str]:
    """Every violation, as a message; empty when the bump is accounted for."""
    match = _GENERATION.search(versioning_text)
    if match is None:
        return ["abicheck/storage/versioning.py declares no EXTRACTOR_GENERATION"]
    generation = int(match.group(1))
    tree = ast.parse(migrations_text)
    rows = _rows(tree)
    if rows is None:
        return ["SPELLING_MIGRATIONS is not a dict literal in generation_migrations.py"]
    problems: list[str] = []
    expected = set(range(1, generation + 1))
    for missing in sorted(expected - set(rows)):
        problems.append(
            f"EXTRACTOR_GENERATION is {generation} but SPELLING_MIGRATIONS has no "
            f"row for generation {missing}: audit that bump's extractor diff and "
            "record the spelling rewrites it introduced, or why there are none"
        )
    for extra in sorted(set(rows) - expected):
        problems.append(f"SPELLING_MIGRATIONS has a row for future generation {extra}")
    placeholders = _placeholders(tree)
    for row, reason in sorted(rows.items()):
        if reason.strip().lower().rstrip(".") in placeholders:
            problems.append(
                f"SPELLING_MIGRATIONS[{row}] has a placeholder reason ({reason!r}); "
                "say what the audit of that generation's commit found"
            )
    if manifest is None:
        problems.append(f"{corpus.relative_to(ROOT)}/manifest.json is missing")
        return problems
    entries = manifest.get("entries") or {}
    skipped = manifest.get("skipped") or {}
    for retired in range(1, generation):
        key = str(retired)
        if key in entries:
            snapshot = corpus / str(entries[key].get("snapshot", ""))
            if not snapshot.is_file():
                problems.append(
                    f"frozen corpus entry for generation {retired} names "
                    f"a missing file {snapshot.name}"
                )
        elif not str(skipped.get(key, "")).strip():
            problems.append(
                f"generation {retired} is retired but the frozen corpus has no "
                "snapshot or skip note for it: run "
                f"`python scripts/freeze_generation_corpus.py --ref <last commit "
                f"of generation {retired}>` before bumping, or --skip "
                f"{retired} --reason"
            )
    return problems


def check_generation_corpus(findings: _FindingsLike) -> None:
    manifest_path = CORPUS / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else None
    )
    for problem in evaluate(
        VERSIONING.read_text(encoding="utf-8"),
        MIGRATIONS.read_text(encoding="utf-8"),
        manifest,
        CORPUS,
    ):
        findings.err(CHECK, problem)
