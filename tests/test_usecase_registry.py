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

"""Validate the use-case registry (docs/development/usecase-registry.yaml).

The registry is the machine-checkable definition of abicheck's ABI/API change
use cases. These tests keep it honest so it stays a reliable, extensible map of
what is covered, partially covered, or planned:

  - structural validity + known axis/status enums;
  - coverage claims (complete/partial/modeled) cite evidence whose paths exist;
  - partial/modeled/planned entries carry a gap id and next_steps;
  - by_design_excluded entries explain themselves;
  - referenced gap ids stay in sync with the evaluation doc.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).parent.parent
_REGISTRY = _REPO / "docs" / "development" / "usecase-registry.yaml"
_EVAL_DOC = _REPO / "docs" / "development" / "usecase-coverage-evaluation.md"

_STATUSES = {"complete", "partial", "modeled", "planned", "by_design_excluded"}
_AXES = {"change_class", "archetype", "platform", "workflow", "reporting", "toolchain"}
#: statuses whose coverage claim must be backed by real, existing evidence paths
_EVIDENCED = {"complete", "partial", "modeled"}
#: statuses that must carry a tracked gap id and next_steps
_NEEDS_PLAN = {"partial", "modeled", "planned"}
_EVIDENCE_KEYS = {"modules", "tests", "examples", "docs", "cli"}
_ID_RE = re.compile(r"^UC-[A-Z]+-[a-z0-9-]+$")
_GAP_RE = re.compile(r"^G[0-9]+$")


def _load() -> list[dict]:
    data = yaml.safe_load(_REGISTRY.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and "use_cases" in data, (
        "registry must have use_cases"
    )
    cases = data["use_cases"]
    assert isinstance(cases, list) and cases, "use_cases must be a non-empty list"
    return cases


def test_registry_parses_and_ids_unique() -> None:
    cases = _load()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate use-case ids"
    for cid in ids:
        assert _ID_RE.match(cid), f"id not in UC-AXIS-slug form: {cid}"


@pytest.mark.parametrize("case", _load(), ids=lambda c: c["id"])
def test_entry_is_well_formed(case: dict) -> None:
    for key in ("id", "axis", "name", "status"):
        assert key in case, f"{case.get('id', '?')} missing required key {key!r}"
    assert case["axis"] in _AXES, f"{case['id']}: unknown axis {case['axis']!r}"
    assert case["status"] in _STATUSES, (
        f"{case['id']}: unknown status {case['status']!r}"
    )


@pytest.mark.parametrize("case", _load(), ids=lambda c: c["id"])
def test_evidence_paths_exist(case: dict) -> None:
    """complete/partial/modeled entries must cite at least one real path."""
    if case["status"] not in _EVIDENCED:
        return
    evidence = case.get("evidence")
    assert evidence, f"{case['id']}: status={case['status']} requires evidence"
    cited = 0
    for key, vals in evidence.items():
        assert key in _EVIDENCE_KEYS, f"{case['id']}: unknown evidence key {key!r}"
        for rel in vals:
            assert (_REPO / rel).exists(), f"{case['id']}: evidence path missing: {rel}"
            cited += 1
    assert cited, f"{case['id']}: evidence block is empty"


@pytest.mark.parametrize("case", _load(), ids=lambda c: c["id"])
def test_unfinished_entries_have_a_plan(case: dict) -> None:
    if case["status"] in _NEEDS_PLAN:
        assert _GAP_RE.match(case.get("gap", "")), f"{case['id']}: needs a Gx gap id"
        assert case.get("next_steps", "").strip(), f"{case['id']}: needs next_steps"
        # Each unfinished use case must link a detailed plan that actually exists.
        plan = case.get("plan", "")
        assert plan.startswith("docs/development/plans/"), (
            f"{case['id']}: needs a plan: under docs/development/plans/"
        )
        assert (_REPO / plan).exists(), f"{case['id']}: plan file missing: {plan}"
    if case["status"] == "by_design_excluded":
        assert case.get("note", "").strip(), (
            f"{case['id']}: must explain exclusion in note"
        )


def test_all_axes_are_represented() -> None:
    covered = {c["axis"] for c in _load()}
    assert covered == _AXES, f"axes missing from registry: {_AXES - covered}"


def test_gap_ids_are_documented_in_eval_doc() -> None:
    """Every gap id the registry references must appear in the evaluation doc,
    so the machine registry and the human narrative cannot drift apart."""
    doc = _EVAL_DOC.read_text(encoding="utf-8")
    gaps = {c["gap"] for c in _load() if "gap" in c}
    missing = {g for g in gaps if not re.search(rf"\b{g}\b", doc)}
    assert not missing, f"gap ids in registry but not in eval doc: {missing}"


def test_eval_doc_links_to_registry() -> None:
    assert "usecase-registry.yaml" in _EVAL_DOC.read_text(encoding="utf-8"), (
        "the evaluation doc must point readers at the machine-readable registry"
    )
