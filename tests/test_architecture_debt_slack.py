"""ADR-061 debt ledger: slack (baseline above the real size) fails, and the
tightening script lowers it without touching anything else, idempotently.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import scripts.tighten_debt_baselines as tighten
from scripts.check_architecture import check_repository
from tests.test_architecture_check import _tree, _write

ROOT = Path(__file__).resolve().parents[1]
# _tree() sets the production limit to 8, the lowest legal baseline.
PATH = "abicheck/legacy.py"


def _record(path: str, baseline: int) -> dict[str, object]:
    return {
        "path": path,
        "baseline_lines": baseline,
        "target": "model",
        "rule": "no_growth",
        "disposition": "migrate",
        "category": "legacy_monolith",
        "owner": "maintainers",
        "rationale": "Move it as a tested vertical slice.",
        "review_by": "2026-11-30",
    }


def _ledger(root: Path, *records: dict[str, object]) -> Path:
    path = root / "architecture/debt.yaml"
    _write(
        path,
        json.dumps({"schema_version": 1, "files": list(records)}, indent=2) + "\n",
    )
    return path


def _lines(count: int) -> str:
    return "\n".join(f"V{i} = {i}" for i in range(count)) + "\n"


def _rules(root: Path) -> set[str]:
    return {finding.rule for finding in check_repository(root)}


def test_slack_entry_fails_gate_with_file_baseline_actual_and_fix(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path)
    _write(root / PATH, _lines(9))
    _ledger(root, _record(PATH, 10))

    findings = check_repository(root)
    slack = [f for f in findings if f.rule == "debt-slack"]

    assert len(slack) == 1
    message = slack[0].message
    assert PATH in message
    assert "baseline_lines 10" in message
    assert "actual 9" in message
    assert "scripts/tighten_debt_baselines.py" in message


def test_exact_baseline_passes_gate(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / PATH, _lines(9))
    _ledger(root, _record(PATH, 9))

    assert "debt-slack" not in _rules(root)


def test_growth_beyond_baseline_is_still_no_growth(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / PATH, _lines(10))
    _ledger(root, _record(PATH, 9))

    rules = _rules(root)

    assert "debt-no-growth" in rules
    assert "debt-slack" not in rules


def test_tighten_lowers_slack_only_and_keeps_every_other_byte(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path)
    _write(root / PATH, _lines(9))
    _write(root / "abicheck/exact.py", _lines(10))
    ledger = _ledger(root, _record(PATH, 12), _record("abicheck/exact.py", 10))
    before = ledger.read_text(encoding="utf-8")

    assert tighten.main(["--root", str(root)]) == 0

    after = ledger.read_text(encoding="utf-8")
    assert after != before
    # Only the slack entry's number moved; the rest of the file is byte-identical.
    assert re.sub(r'"baseline_lines": \d+', "", after) == re.sub(
        r'"baseline_lines": \d+', "", before
    )
    data = json.loads(after)
    assert [record["baseline_lines"] for record in data["files"]] == [9, 10]
    assert "debt-slack" not in _rules(root)


def test_tighten_is_idempotent(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / PATH, _lines(9))
    ledger = _ledger(root, _record(PATH, 12))

    assert tighten.main(["--root", str(root)]) == 0
    once = ledger.read_text(encoding="utf-8")
    assert tighten.main(["--root", str(root)]) == 0

    assert ledger.read_text(encoding="utf-8") == once


def test_tighten_never_raises_a_baseline(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _write(root / PATH, _lines(10))
    ledger = _ledger(root, _record(PATH, 9))
    before = ledger.read_text(encoding="utf-8")

    assert tighten.main(["--root", str(root)]) == 0

    assert ledger.read_text(encoding="utf-8") == before


def test_check_mode_reports_slack_without_writing(tmp_path: Path, capsys) -> None:
    root = _tree(tmp_path)
    _write(root / PATH, _lines(9))
    ledger = _ledger(root, _record(PATH, 12))
    before = ledger.read_text(encoding="utf-8")

    assert tighten.main(["--root", str(root), "--check"]) == 1

    assert ledger.read_text(encoding="utf-8") == before
    assert f"{PATH}: baseline 12 -> 9" in capsys.readouterr().out


def test_entry_under_its_cap_is_reported_not_lowered(tmp_path: Path, capsys) -> None:
    # Actual 5 is below the production cap of 8: lowering would break the
    # schema floor, so the entry is reported for manual removal instead.
    root = _tree(tmp_path)
    _write(root / PATH, _lines(5))
    ledger = _ledger(root, _record(PATH, 12))
    before = ledger.read_text(encoding="utf-8")

    assert tighten.main(["--root", str(root)]) == 1

    assert ledger.read_text(encoding="utf-8") == before
    assert "remove this entry" in capsys.readouterr().out


def test_repository_debt_ledger_has_no_slack() -> None:
    assert tighten.find_slack(ROOT) == []
