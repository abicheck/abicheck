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

"""Policy capability reachability (ADR-067 P8): every policy capability is
reachable from every surface that claims it, and the claim is checked.

ADR-067's acknowledgment records shipped for the ``.abicheck.yml`` config path
and the typed API, while the CLI flag, the Action input and the break gate
were all missing -- and no test failed, because each surface was tested on its
own and no test asked "can a user reach this capability from every front end?".
The table below is that question. A capability row names the surfaces it claims;
each claimed surface must resolve, and each engine policy field must have a row.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from _yaml_fast import safe_load
from click.testing import CliRunner

from abicheck.buildsource.build_config_acknowledgment import acknowledgment_findings
from abicheck.cli import main
from abicheck.frontends.cli.runtime import _exit_with_severity_or_verdict
from abicheck.model import AbiSnapshot
from abicheck.model.acknowledgment_policy import AcknowledgmentPolicy
from abicheck.model.declarations import Function
from abicheck.policy.effective_gate import EffectiveGate
from abicheck.serialization import save_snapshot
from abicheck.service import CompareRequest, InputSpec, run_compare_request

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: One row per policy capability: the surfaces it is claimed through.
#:
#: ``cli``    -- a ``compare`` option (flag spelling, resolved on the Click command)
#: ``config`` -- a ``.abicheck.yml`` key (resolved by the config block's own
#:               strict-findings check, with a sample value)
#: ``api``    -- ``CompareRequest`` field (dataclass field)
#: ``action`` -- an ``action.yml`` input
#: ``report`` -- a field the JSON report carries for the capability
#: ``policy_file`` -- a ``--policy`` document key (resolved by the policy loader)
CAPABILITIES: dict[str, dict[str, str]] = {
    "acknowledgment.records": {
        "cli": "--acknowledgments",
        "config": "acknowledgments.file",
        "api": "acknowledgments",
        "action": "acknowledgments",
        "report": "disposition_audit.acknowledgments",
    },
    "acknowledgment.breaks": {
        "config": "acknowledgments.breaks",
        "policy_file": "acknowledgments.breaks",
        "api": "acknowledgment_breaks",
        "report": "disposition_audit.acknowledgments",
    },
    "acknowledgment.unacknowledged_additions": {
        "config": "acknowledgments.unacknowledged_additions",
        "policy_file": "acknowledgments.unacknowledged_additions",
        "api": "acknowledgment_unacknowledged_additions",
        "report": "disposition_audit.unacknowledged_additions_review",
    },
    "suppression.file": {
        "cli": "--suppress",
        "api": "suppress",
        "action": "suppress",
        "report": "disposition_audit",
    },
}

#: The engine's own gate fields, one capability row each. A new field on
#: ``AcknowledgmentPolicy`` fails here until it is named, the ratchet that would
#: have flagged ``breaks`` the moment it existed without a surface.
_ACKNOWLEDGMENT_POLICY_FIELDS = {
    f"acknowledgment.{f.name}" for f in dataclasses.fields(AcknowledgmentPolicy)
}

_SAMPLE_CONFIG_VALUES = {
    "file": "acks.yml",
    "breaks": "accept",
    "unacknowledged_additions": "block",
}


def _compare_flags() -> set[str]:
    return {o for p in main.commands["compare"].params for o in p.opts}


def _action_inputs() -> set[str]:
    doc = safe_load((_REPO_ROOT / "action.yml").read_text(encoding="utf-8"))
    return set(doc["inputs"])


def _schema_property_names(node: Any, out: set[str]) -> set[str]:
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            out.update(props)
        for value in node.values():
            _schema_property_names(value, out)
    elif isinstance(node, list):
        for value in node:
            _schema_property_names(value, out)
    return out


def _report_fields() -> set[str]:
    schema = json.loads(
        (_REPO_ROOT / "abicheck" / "schemas" / "compare_report.schema.json").read_text(
            encoding="utf-8"
        )
    )
    return _schema_property_names(schema, set())


def _surface_problems(capability: str, row: dict[str, str]) -> list[str]:
    problems: list[str] = []
    if "cli" in row and row["cli"] not in _compare_flags():
        problems.append(f"{capability}: cli {row['cli']!r} is not a compare option")
    if "config" in row:
        key = row["config"].split(".", 1)[1]
        if key not in _SAMPLE_CONFIG_VALUES:
            problems.append(f"{capability}: config key {key!r} has no sample value")
        elif acknowledgment_findings({key: _SAMPLE_CONFIG_VALUES[key]}):
            problems.append(f"{capability}: config key {key!r} is rejected")
    if "api" in row:
        fields = {f.name for f in dataclasses.fields(CompareRequest)}
        if row["api"] not in fields:
            problems.append(f"{capability}: CompareRequest has no {row['api']!r}")
    if "action" in row and row["action"] not in _action_inputs():
        problems.append(f"{capability}: action input {row['action']!r} missing")
    if "report" in row:
        leaf = row["report"].split(".")[-1]
        if leaf not in _report_fields():
            problems.append(f"{capability}: report field {leaf!r} not in schema")
    if "policy_file" in row:
        key = row["policy_file"].split(".", 1)[1]
        if key not in {f.name for f in dataclasses.fields(AcknowledgmentPolicy)}:
            problems.append(f"{capability}: policy_file key {key!r} unknown")
    return problems


@pytest.mark.parametrize("capability", sorted(CAPABILITIES))
def test_every_claimed_surface_resolves(capability: str) -> None:
    assert _surface_problems(capability, CAPABILITIES[capability]) == []


def test_every_engine_policy_field_has_a_capability_row() -> None:
    missing = _ACKNOWLEDGMENT_POLICY_FIELDS - set(CAPABILITIES)
    assert missing == set(), f"policy fields with no reachability row: {missing}"


def test_every_row_claims_a_surface_beyond_one_front_end() -> None:
    """A capability reachable from one front end only is the P8 failure shape."""
    for capability, row in CAPABILITIES.items():
        front_ends = {k for k in row if k in {"cli", "config", "api", "action"}}
        assert len(front_ends) >= 2, (capability, sorted(front_ends))


# --- behavioural parity: the same resolved request decides the same way ----------


def _snap(version: str, names: list[str]) -> AbiSnapshot:
    return AbiSnapshot(
        version=version,
        library="libfoo",
        functions=[
            Function(name=n, mangled=n, return_type="void", params=[]) for n in names
        ],
        variables=[],
        types=[],
        enums=[],
    )


@pytest.fixture
def removals(tmp_path: Path) -> dict[str, Path]:
    """Two removals; the records below name one or both of them."""
    old = _snap("1.0.0", ["keep", "gone_a", "gone_b"])
    new = _snap("2.0.0", ["keep"])
    paths = {"old": tmp_path / "old.json", "new": tmp_path / "new.json"}
    save_snapshot(old, paths["old"])
    save_snapshot(new, paths["new"])
    return paths


def _records(tmp: Path, symbols: tuple[str, ...]) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    body = "".join(
        f"  - symbol: {s}\n    change_kind: func_removed\n    reason: planned\n"
        for s in symbols
    )
    path = tmp / "acks.yml"
    path.write_text("version: 1\nacknowledgments:\n" + body, encoding="utf-8")
    return path


#: (name, acknowledged symbols, breaks setting, expected exit code)
SCENARIOS = [
    ("gate-default-all-acknowledged", ("gone_a", "gone_b"), "gate", 4),
    ("accept-all-acknowledged", ("gone_a", "gone_b"), "accept", 0),
    ("accept-sibling-stays-gating", ("gone_a",), "accept", 4),
    ("accept-nothing-acknowledged", (), "accept", 4),
]


def _cli_exit(tmp: Path, removals: dict[str, Path], symbols, breaks: str) -> int:
    """The CLI, with the records named by the explicit config (the config path)."""
    records = _records(tmp, symbols)
    cfg = tmp / "cfg.abicheck.yml"
    cfg.write_text(
        f"acknowledgments:\n  file: {records}\n  breaks: {breaks}\n", encoding="utf-8"
    )
    out = tmp / "report.json"
    res = CliRunner().invoke(
        main,
        [
            "compare",
            str(removals["old"]),
            str(removals["new"]),
            "--config",
            str(cfg),
            "-o",
            f"json={out}",
        ],
    )
    assert out.exists(), res.output
    return res.exit_code


def _cli_flag_exit(tmp: Path, removals: dict[str, Path], symbols, breaks: str) -> int:
    """The same decision through the ``--acknowledgments`` flag, not the config key."""
    records = _records(tmp, symbols)
    cfg = tmp / "cfg.abicheck.yml"
    cfg.write_text(f"acknowledgments:\n  breaks: {breaks}\n", encoding="utf-8")
    out = tmp / "report.json"
    res = CliRunner().invoke(
        main,
        [
            "compare",
            str(removals["old"]),
            str(removals["new"]),
            "--config",
            str(cfg),
            "--acknowledgments",
            str(records),
            "-o",
            f"json={out}",
        ],
    )
    assert out.exists(), res.output
    return res.exit_code


def _typed_exit(tmp: Path, removals: dict[str, Path], symbols, breaks: str) -> int:
    records = _records(tmp, symbols)
    request = CompareRequest(
        old=InputSpec(path=removals["old"]),
        new=InputSpec(path=removals["new"]),
        acknowledgments=records,
        acknowledgment_breaks=breaks,
    )
    result = run_compare_request(request)
    try:
        _exit_with_severity_or_verdict(result.diff, EffectiveGate.from_severity(None))
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


@pytest.mark.parametrize(
    ("name", "symbols", "breaks", "expected"),
    SCENARIOS,
    ids=[s[0] for s in SCENARIOS],
)
def test_config_cli_and_typed_api_decide_the_same(
    tmp_path: Path,
    removals: dict[str, Path],
    name: str,
    symbols: tuple[str, ...],
    breaks: str,
    expected: int,
) -> None:
    """Front-end parity (ADR-055/049): one resolved request, one decision."""
    cli = _cli_exit(tmp_path / "cfg", removals, symbols, breaks)
    typed = _typed_exit(tmp_path / "api", removals, symbols, breaks)
    assert (cli, typed) == (expected, expected), name


@pytest.mark.parametrize(
    ("name", "symbols", "breaks", "expected"),
    SCENARIOS,
    ids=[s[0] for s in SCENARIOS],
)
def test_acknowledgments_flag_decides_like_the_config_key(
    tmp_path: Path,
    removals: dict[str, Path],
    name: str,
    symbols: tuple[str, ...],
    breaks: str,
    expected: int,
) -> None:
    flag = _cli_flag_exit(tmp_path / "flag", removals, symbols, breaks)
    config = _cli_exit(tmp_path / "config", removals, symbols, breaks)
    assert (flag, config) == (expected, expected), name


def test_scenarios_are_not_vacuous() -> None:
    """Guard the table itself: it must cover both gates and a sibling case."""
    exits = {expected for _n, _s, _b, expected in SCENARIOS}
    assert exits == {0, 4}
    assert any(b == "accept" and s and len(s) == 1 for _n, s, b, _e in SCENARIOS)
