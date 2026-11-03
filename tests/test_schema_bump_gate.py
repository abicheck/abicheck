"""``check_ai_readiness.py``'s ``schema-bump-reason`` gate
(``scripts/schema_bump_gate.py``, ADR-062 D19): a ``SCHEMA_VERSION`` bump
past the baseline needs a declared envelope/wire reason it is not an
extension fact."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "schema_bump_gate.py"
_SPEC = importlib.util.spec_from_file_location("schema_bump_gate", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
sys.modules["schema_bump_gate"] = gate
_SPEC.loader.exec_module(gate)

_SOURCE = gate.VERSIONS.read_text(encoding="utf-8")
_VERSION = re.compile(r"^SCHEMA_VERSION: int = (\d+)", re.MULTILINE)
_CURRENT = int(_VERSION.search(_SOURCE).group(1))
_REASON = (
    "the declarations section is re-keyed by entity id, so an older reader "
    "would pair the wrong records"
)


def _bumped(source: str, to: int) -> str:
    return _VERSION.sub(f"SCHEMA_VERSION: int = {to}", source, count=1)


def _with_rows(source: str, rows: str) -> str:
    anchor = "SCHEMA_BUMP_REASONS: dict[int, SchemaBump] = {}"
    assert anchor in source
    return source.replace(
        anchor, f"SCHEMA_BUMP_REASONS: dict[int, SchemaBump] = {{{rows}}}"
    )


def test_repository_state_passes() -> None:
    assert gate.evaluate(_SOURCE) == []


@pytest.mark.parametrize("steps", [1, 2, 3])
def test_every_unreasoned_bump_fails(steps: int) -> None:
    """Each version past the current one, not only the first, needs a row."""
    problems = gate.evaluate(_bumped(_SOURCE, _CURRENT + steps))
    missing = {
        int(m) for p in problems for m in re.findall(r"bump to v(\d+) has no", p)
    }
    assert missing == set(range(_CURRENT + 1, _CURRENT + steps + 1))


@pytest.mark.parametrize("kind", ["envelope", "wire"])
@pytest.mark.parametrize("spelling", ["positional", "keyword"])
def test_a_reasoned_bump_passes(kind: str, spelling: str) -> None:
    args = (
        f"{kind!r}, {_REASON!r}"
        if spelling == "positional"
        else f"kind={kind!r}, why_not_extension={_REASON!r}"
    )
    source = _with_rows(
        _bumped(_SOURCE, _CURRENT + 1), f"{_CURRENT + 1}: SchemaBump({args})"
    )
    assert gate.evaluate(source) == []


@pytest.mark.parametrize(
    ("row", "fragment"),
    [
        ("SchemaBump('optional', {reason!r})", "is not one of"),
        ("SchemaBump('wire', 'TBD')", "placeholder"),
        ("SchemaBump('wire', 'short')", "placeholder"),
        ("SchemaBump('wire', REASON)", "placeholder"),
        ("SchemaBump(KIND, {reason!r})", "is not one of"),
        ("('wire', {reason!r})", "is not one of"),
    ],
)
def test_a_malformed_row_fails(row: str, fragment: str) -> None:
    rows = f"{_CURRENT + 1}: " + row.format(reason=_REASON)
    problems = gate.evaluate(_with_rows(_bumped(_SOURCE, _CURRENT + 1), rows))
    assert any(fragment in p for p in problems), problems


@pytest.mark.parametrize("offset", [0, 2, -5])
def test_a_row_for_a_version_not_bumped_to_fails(offset: int) -> None:
    """A row at or below the baseline, or past the current version."""
    version = _CURRENT + offset if offset else gate.PINNED_BASELINE
    rows = f"{version}: SchemaBump('wire', {_REASON!r})"
    problems = gate.evaluate(_with_rows(_SOURCE, rows))
    assert any(f"row v{version} is outside" in p for p in problems), problems


def test_moving_the_baseline_does_not_waive_a_bump() -> None:
    source = _bumped(_SOURCE, _CURRENT + 1).replace(
        f"SCHEMA_BUMP_RULE_BASELINE: int = {gate.PINNED_BASELINE}",
        f"SCHEMA_BUMP_RULE_BASELINE: int = {_CURRENT + 1}",
    )
    assert any("does not move" in p for p in gate.evaluate(source))


def test_a_duplicate_row_fails() -> None:
    row = f"{_CURRENT + 1}: SchemaBump('wire', {_REASON!r})"
    source = _with_rows(_bumped(_SOURCE, _CURRENT + 1), f"{row}, {row}")
    assert any("twice" in p for p in gate.evaluate(source))


def test_the_gate_is_wired_into_ai_readiness() -> None:
    text = (_PATH.parent / "check_ai_readiness.py").read_text(encoding="utf-8")
    assert '"schema-bump-reason": check_schema_bump_reasons' in text
