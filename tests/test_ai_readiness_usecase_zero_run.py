"""`scripts/check_ai_readiness.py`'s `usecase-complete-has-runs` check.

A use case whose registry status is ``complete`` must have at least one
automated scenario or usecase-flow run, unless it is grandfathered in
``scripts/usecase_zero_run_grandfather.json``. A grandfathered entry that
gains a run, or stops being ``complete``, is stale and fails, so the list
only shrinks.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_ai_readiness.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_ai_readiness", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_ai_readiness"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def car():
    return _load_module()


def _registry(*entries: tuple[str, str]) -> str:
    body = "schema_version: 1\n\nuse_cases:\n"
    for uc_id, status in entries:
        body += (
            f"  - id: {uc_id}\n"
            f"    axis: workflow\n"
            f"    name: fixture {uc_id}\n"
            f"    status: {status}\n"
            f"    user_task: [pr_review]\n"
            f"    evidence:\n"
            f"      modules: [abicheck/x.py]\n"
        )
    return body


def _scenario(sc_id: str, validates: str, automated: bool) -> str:
    return (
        f"  - id: {sc_id}\n"
        f"    title: t\n"
        f"    narrative: >\n"
        f"      some prose\n"
        f"    flow:\n"
        f"      - abicheck compare a b\n"
        f"    validates: {validates}    # trailing comment\n"
        f"    automated: {'true' if automated else 'false'}\n"
    )


def _tree(
    tmp_path: Path,
    use_cases: tuple[tuple[str, str], ...],
    scenarios: tuple[str, ...] = (),
    flows: tuple[str, ...] = (),
    grandfather: dict | None = None,
) -> Path:
    (tmp_path / "docs/contribute").mkdir(parents=True)
    (tmp_path / "docs/contribute/usecase-registry.yaml").write_text(
        _registry(*use_cases), encoding="utf-8"
    )
    (tmp_path / "tests/scenarios").mkdir(parents=True)
    (tmp_path / "tests/scenarios/fixture.yaml").write_text(
        "schema_version: 1\n\nscenarios:\n" + "".join(scenarios), encoding="utf-8"
    )
    (tmp_path / "scripts").mkdir(parents=True)
    flow_body = "schema_version: 1\n\nflows:\n" + "".join(
        f"  - id: flow-{i}\n    use_case: {uc}\n    argv: [compare]\n"
        for i, uc in enumerate(flows)
    )
    (tmp_path / "scripts/usecase_flows.yaml").write_text(flow_body, encoding="utf-8")
    if grandfather is not None:
        (tmp_path / "scripts/usecase_zero_run_grandfather.json").write_text(
            json.dumps(grandfather), encoding="utf-8"
        )
    return tmp_path


def _entry(uc_id: str, reason: str = "no run yet") -> dict:
    return {"id": uc_id, "reason": reason}


def test_check_is_registered(car):
    assert (
        car.CHECKS["usecase-complete-has-runs"] is car.check_usecase_complete_has_runs
    )


def test_complete_without_runs_and_not_grandfathered_fails(car, tmp_path):
    root = _tree(tmp_path, (("UC-A", "complete"),), grandfather={"entries": []})
    problems = car.usecase_zero_run_problems(root)
    assert len(problems) == 1
    assert problems[0].startswith("UC-A: status complete but no automated")


def test_complete_without_runs_but_grandfathered_passes(car, tmp_path):
    root = _tree(
        tmp_path,
        (("UC-A", "complete"),),
        grandfather={"entries": [_entry("UC-A")]},
    )
    assert car.usecase_zero_run_problems(root) == []


def test_grandfathered_entry_that_gained_a_scenario_is_stale(car, tmp_path):
    root = _tree(
        tmp_path,
        (("UC-A", "complete"),),
        scenarios=(_scenario("SC-A", "UC-A", automated=True),),
        grandfather={"entries": [_entry("UC-A")]},
    )
    problems = car.usecase_zero_run_problems(root)
    assert len(problems) == 1
    assert "grandfathered but now has 1 run(s)" in problems[0]


def test_grandfathered_entry_that_gained_a_flow_is_stale(car, tmp_path):
    root = _tree(
        tmp_path,
        (("UC-A", "complete"),),
        flows=("UC-A",),
        grandfather={"entries": [_entry("UC-A")]},
    )
    problems = car.usecase_zero_run_problems(root)
    assert len(problems) == 1
    assert "now has 1 run(s)" in problems[0]


def test_complete_with_automated_scenario_passes(car, tmp_path):
    root = _tree(
        tmp_path,
        (("UC-A", "complete"),),
        scenarios=(_scenario("SC-A", "UC-A", automated=True),),
        grandfather={"entries": []},
    )
    assert car.usecase_zero_run_problems(root) == []


def test_complete_with_flow_passes(car, tmp_path):
    root = _tree(
        tmp_path,
        (("UC-A", "complete"),),
        flows=("UC-A",),
        grandfather={"entries": []},
    )
    assert car.usecase_zero_run_problems(root) == []


def test_manual_scenario_is_not_a_run(car, tmp_path):
    root = _tree(
        tmp_path,
        (("UC-A", "complete"),),
        scenarios=(_scenario("SC-A", "UC-A", automated=False),),
        grandfather={"entries": []},
    )
    problems = car.usecase_zero_run_problems(root)
    assert len(problems) == 1 and "UC-A" in problems[0]


def test_non_complete_use_cases_are_not_checked(car, tmp_path):
    root = _tree(
        tmp_path,
        (("UC-P", "partial"), ("UC-M", "planned")),
        grandfather={"entries": []},
    )
    assert car.usecase_zero_run_problems(root) == []


def test_grandfathered_entry_no_longer_complete_is_stale(car, tmp_path):
    root = _tree(
        tmp_path,
        (("UC-A", "partial"),),
        grandfather={"entries": [_entry("UC-A")]},
    )
    problems = car.usecase_zero_run_problems(root)
    assert len(problems) == 1 and "not complete" in problems[0]


def test_grandfathered_unknown_id_is_stale(car, tmp_path):
    root = _tree(
        tmp_path,
        (("UC-A", "complete"),),
        scenarios=(_scenario("SC-A", "UC-A", automated=True),),
        grandfather={"entries": [_entry("UC-GONE")]},
    )
    problems = car.usecase_zero_run_problems(root)
    assert len(problems) == 1 and "not in" in problems[0]


@pytest.mark.parametrize(
    "bad",
    [
        {"entries": [{"id": "UC-A", "reason": "  "}]},
        {"entries": [{"id": "UC-A"}]},
        {"entries": [_entry("UC-A"), _entry("UC-A")]},
        {"rows": []},
    ],
)
def test_malformed_grandfather_file_fails(car, tmp_path, bad):
    root = _tree(
        tmp_path,
        (("UC-A", "complete"),),
        scenarios=(_scenario("SC-A", "UC-A", automated=True),),
        grandfather=bad,
    )
    problems = car.usecase_zero_run_problems(root)
    assert problems, "a malformed grandfather file must not pass silently"


def test_missing_grandfather_file_fails(car, tmp_path):
    root = _tree(tmp_path, (("UC-A", "complete"),), grandfather=None)
    problems = car.usecase_zero_run_problems(root)
    assert any("missing or unreadable" in p for p in problems)


def test_unparseable_registry_fails_loudly(car, tmp_path):
    root = _tree(tmp_path, (), grandfather={"entries": []})
    assert car.usecase_zero_run_problems(root) == [
        "docs/contribute/usecase-registry.yaml: no use cases parsed"
    ]


def test_real_tree_passes(car):
    f = car.Findings()
    car.check_usecase_complete_has_runs(f)
    assert f.errors == [], f"usecase zero-run gate: {f.errors}"
