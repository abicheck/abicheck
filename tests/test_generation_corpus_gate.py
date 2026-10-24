"""``check_ai_readiness.py``'s ``generation-corpus`` gate
(``scripts/generation_corpus_gate.py``): bumping ``EXTRACTOR_GENERATION``
requires an audited ``SPELLING_MIGRATIONS`` row and a frozen corpus entry (or
skip note) for the generation being retired."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "generation_corpus_gate.py"
_SPEC = importlib.util.spec_from_file_location("generation_corpus_gate", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
sys.modules["generation_corpus_gate"] = gate
_SPEC.loader.exec_module(gate)

_VERSIONING = gate.VERSIONING.read_text(encoding="utf-8")
_MIGRATIONS = gate.MIGRATIONS.read_text(encoding="utf-8")
_MANIFEST = json.loads((gate.CORPUS / "manifest.json").read_text(encoding="utf-8"))
_CURRENT = int(gate._GENERATION.search(_VERSIONING).group(1))


def _bumped(versioning: str, to: int) -> str:
    return gate._GENERATION.sub(f"EXTRACTOR_GENERATION = {to}", versioning)


def _with_row(migrations: str, generation: int, reason: str) -> str:
    """*migrations* with one more literal row appended to SPELLING_MIGRATIONS."""
    anchor = "\n}\n\n#: Reasons that say nothing"
    assert anchor in migrations
    row = f"\n    {generation}: GenerationSpellingChange({reason!r}),"
    return migrations.replace(anchor, row + anchor, 1)


def _frozen(manifest: dict, generation: int) -> dict:
    out = json.loads(json.dumps(manifest))
    out["entries"][str(generation)] = {"snapshot": "gen1/corpus.json.gz"}
    return out


def test_repository_state_passes() -> None:
    assert gate.evaluate(_VERSIONING, _MIGRATIONS, _MANIFEST, gate.CORPUS) == []


def test_bump_without_a_row_fails() -> None:
    problems = gate.evaluate(
        _bumped(_VERSIONING, _CURRENT + 1),
        _MIGRATIONS,
        _frozen(_MANIFEST, _CURRENT),
        gate.CORPUS,
    )
    assert any(f"no row for generation {_CURRENT + 1}" in p for p in problems)


@pytest.mark.parametrize(
    "reason",
    [
        "",
        "  ",
        "TODO",
        "no change.",
        "No stored type spelling changed in this generation",
    ],
)
def test_bump_with_a_placeholder_reason_fails(reason: str) -> None:
    problems = gate.evaluate(
        _bumped(_VERSIONING, _CURRENT + 1),
        _with_row(_MIGRATIONS, _CURRENT + 1, reason),
        _frozen(_MANIFEST, _CURRENT),
        gate.CORPUS,
    )
    assert [p for p in problems if "placeholder reason" in p], problems


def test_bump_without_freezing_the_retired_generation_fails() -> None:
    problems = gate.evaluate(
        _bumped(_VERSIONING, _CURRENT + 1),
        _with_row(_MIGRATIONS, _CURRENT + 1, "abc1234: new fact X, no respelling"),
        _MANIFEST,
        gate.CORPUS,
    )
    assert len(problems) == 1
    assert re.search(rf"generation {_CURRENT} is retired", problems[0])


def test_complete_bump_passes_with_a_snapshot_or_a_skip_note() -> None:
    versioning = _bumped(_VERSIONING, _CURRENT + 1)
    migrations = _with_row(
        _MIGRATIONS, _CURRENT + 1, "abc1234: new fact X, no respelling"
    )
    assert (
        gate.evaluate(versioning, migrations, _frozen(_MANIFEST, _CURRENT), gate.CORPUS)
        == []
    )
    skipped = json.loads(json.dumps(_MANIFEST))
    skipped["skipped"][str(_CURRENT)] = "its code cannot run any more on this toolchain"
    assert gate.evaluate(versioning, migrations, skipped, gate.CORPUS) == []


def test_entry_naming_a_missing_file_fails() -> None:
    manifest = json.loads(json.dumps(_MANIFEST))
    first = min(manifest["entries"], key=int)
    manifest["entries"][first]["snapshot"] = "gen999/absent.json.gz"
    problems = gate.evaluate(_VERSIONING, _MIGRATIONS, manifest, gate.CORPUS)
    assert problems == [
        f"frozen corpus entry for generation {first} names a missing file absent.json.gz"
    ]


def test_missing_manifest_fails() -> None:
    problems = gate.evaluate(_VERSIONING, _MIGRATIONS, None, gate.CORPUS)
    assert any("manifest.json is missing" in p for p in problems)


def test_gate_placeholders_agree_with_the_runtime_predicate() -> None:
    """The stdlib gate reads PLACEHOLDER_REASONS from source; the runtime
    helper must refuse the same set."""
    import ast

    from abicheck.storage.generation_migrations import (
        PLACEHOLDER_REASONS,
        is_placeholder_reason,
    )

    assert gate._placeholders(ast.parse(_MIGRATIONS)) == set(PLACEHOLDER_REASONS)
    assert all(is_placeholder_reason(r.upper() + ".") for r in PLACEHOLDER_REASONS)
    assert not is_placeholder_reason("abc1234: new fact X, no respelling")
