"""ADR-061 debt ledger: a raised or new no-growth entry must link its plan and stay short.

Lowered and unchanged entries are grandfathered, so they never fail here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.check_architecture as architecture
from scripts.check_architecture import Finding, check_repository
from tests.test_architecture_check import _tree, _write

PLAN = "docs/contribute/plans/debt-move.md"
LINKED = f"Move it as a tested vertical slice along {PLAN}."
# _tree() sets the production limit to 8, the lowest legal baseline.
ORIGINAL = 8


def _entry(baseline: int, rationale: str) -> dict[str, object]:
    return {
        "path": "abicheck/legacy.py",
        "baseline_lines": baseline,
        "target": "model",
        "rule": "no_growth",
        "disposition": "migrate",
        "category": "legacy_monolith",
        "owner": "maintainers",
        "rationale": rationale,
        "review_by": "2026-11-30",
    }


def _raise_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    current: dict[str, object],
    base: list[dict[str, object]],
) -> list[Finding]:
    root = _tree(tmp_path)
    _write(root / "abicheck/legacy.py", "VALUE = 1\n" * 3)
    _write(root / PLAN, "# Debt move plan\n")
    _write(
        root / "architecture/debt.yaml",
        json.dumps({"schema_version": 1, "files": [current]}),
    )
    base_records = {str(entry["path"]): entry for entry in base}
    monkeypatch.setattr(
        architecture, "_base_has_architecture_contract", lambda *_: True
    )
    monkeypatch.setattr(architecture, "_base_debt_records", lambda *_: base_records)

    findings = check_repository(root, base_revision="base")
    return [finding for finding in findings if finding.rule.startswith("debt-raise")]


def test_raised_entry_without_link_fails(tmp_path: Path, monkeypatch) -> None:
    findings = _raise_findings(
        tmp_path,
        monkeypatch,
        current=_entry(ORIGINAL + 1, "Move it as a tested vertical slice."),
        base=[_entry(ORIGINAL, "Move it as a tested vertical slice.")],
    )

    assert [finding.rule for finding in findings] == ["debt-raise-link"]


def test_raised_entry_with_existing_link_passes(tmp_path: Path, monkeypatch) -> None:
    findings = _raise_findings(
        tmp_path,
        monkeypatch,
        current=_entry(ORIGINAL + 1, LINKED),
        base=[_entry(ORIGINAL, LINKED)],
    )

    assert findings == []


def test_new_entry_with_existing_link_passes(tmp_path: Path, monkeypatch) -> None:
    findings = _raise_findings(
        tmp_path, monkeypatch, current=_entry(ORIGINAL, LINKED), base=[]
    )

    assert findings == []


def test_new_entry_over_limit_fails(tmp_path: Path, monkeypatch) -> None:
    long_rationale = LINKED + " " + "detail " * 300
    assert len(long_rationale) > architecture.DEBT_RATIONALE_MAX_CHARS

    findings = _raise_findings(
        tmp_path, monkeypatch, current=_entry(ORIGINAL, long_rationale), base=[]
    )

    assert [finding.rule for finding in findings] == ["debt-raise-rationale"]
    assert str(len(long_rationale)) in findings[0].message


@pytest.mark.parametrize(
    ("length", "expected"),
    [(1500, []), (1501, ["debt-raise-rationale"])],
)
def test_rationale_length_boundary_is_inclusive(
    tmp_path: Path, monkeypatch, length: int, expected: list[str]
) -> None:
    rationale = LINKED + " " + "x" * (length - len(LINKED) - 1)
    assert len(rationale) == length

    findings = _raise_findings(
        tmp_path, monkeypatch, current=_entry(ORIGINAL, rationale), base=[]
    )

    assert [finding.rule for finding in findings] == expected


def test_unchanged_over_limit_entry_passes(tmp_path: Path, monkeypatch) -> None:
    grandfathered = "No link, and long. " + "detail " * 300
    assert len(grandfathered) > architecture.DEBT_RATIONALE_MAX_CHARS

    findings = _raise_findings(
        tmp_path,
        monkeypatch,
        current=_entry(ORIGINAL, grandfathered),
        base=[_entry(ORIGINAL, grandfathered)],
    )

    assert findings == []


def test_lowered_entry_without_link_passes(tmp_path: Path, monkeypatch) -> None:
    findings = _raise_findings(
        tmp_path,
        monkeypatch,
        current=_entry(ORIGINAL, "Move it as a tested vertical slice."),
        base=[_entry(ORIGINAL + 1, LINKED)],
    )

    assert findings == []


def test_raised_entry_linking_a_missing_file_fails(tmp_path: Path, monkeypatch) -> None:
    rationale = "Move it along docs/contribute/plans/no-such-plan.md."

    findings = _raise_findings(
        tmp_path,
        monkeypatch,
        current=_entry(ORIGINAL + 1, rationale),
        base=[_entry(ORIGINAL, LINKED)],
    )

    assert [finding.rule for finding in findings] == ["debt-raise-link"]
    assert "no-such-plan.md" in findings[0].message
