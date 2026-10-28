# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Every report surface states the gating count the process exit was decided by.

Bug class ``report.finding_entry_builder_parity`` (the derived gating count):
an acknowledged break that ``acknowledgment.breaks: accept`` removes from the
gate was still counted as gating by the severity block (``severity.exit_code``,
``blocking``, SARIF's ``exitCode``, the HTML gate card), by the disposition
ledger (``result_counts.gating``, the audit's ``effective_total``) and by the
review groups (``gate_contribution`` on the Markdown and digest lines), while
the process exited 0. The oracle here is the CLI's own exit decision, read
through ``_exit_with_severity_or_verdict``, which no report helper computes.

The scenarios cross a severity config that demotes an ABI break with an
acknowledged *gating* API break, with and without ``breaks: accept``, and the
legacy verdict scheme.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from abicheck import checker
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import DiffResult
from abicheck.frontends.cli.runtime import _exit_with_severity_or_verdict
from abicheck.frontends.render import render_output
from abicheck.model import AbiSnapshot
from abicheck.model.change import Change
from abicheck.model.declarations import Function
from abicheck.policy.acknowledgment import AcknowledgmentList
from abicheck.policy.effective_gate import EffectiveGate
from abicheck.policy.severity import SeverityConfig, SeverityLevel

_ERROR = SeverityLevel.ERROR
_WARNING = SeverityLevel.WARNING


def _snapshot(version: str, names: list[str]) -> AbiSnapshot:
    return AbiSnapshot(
        version=version,
        library="libfoo.so",
        functions=[
            Function(name=n, mangled=n, return_type="void", params=[]) for n in names
        ],
        variables=[],
        types=[],
        enums=[],
    )


@dataclass(frozen=True)
class _Scenario:
    severity: SeverityConfig | None
    breaks_accept: bool
    #: What the CLI process exits with, which every surface must agree with.
    exit_code: int
    #: The gating total the exit decision was taken over.
    gating: int


# gone_b / gone_c are ABI removals (`abi_breaking`); K1 is an API break
# (`potential_breaking`) that the run acknowledges. gone_a is acknowledged too.
_SCENARIOS = {
    # The severity config demotes the ABI breaks; K1 is the only gating finding,
    # and accepting it leaves the run with nothing gating.
    "demoted_accepted": _Scenario(
        SeverityConfig(abi_breaking=_WARNING, potential_breaking=_ERROR),
        breaks_accept=True,
        exit_code=0,
        gating=0,
    ),
    # Same config, the acknowledgment does not drop the gate: K1 still gates.
    "demoted_gated": _Scenario(
        SeverityConfig(abi_breaking=_WARNING, potential_breaking=_ERROR),
        breaks_accept=False,
        exit_code=2,
        gating=1,
    ),
    # The legacy verdict scheme: gone_b / gone_c still gate after K1 is accepted.
    "legacy_accepted": _Scenario(
        None,
        breaks_accept=True,
        exit_code=4,
        gating=2,
    ),
}


def _run(tmp_path: Path, scenario: _Scenario) -> DiffResult:
    old = _snapshot("1.0", ["keep", "gone_a", "gone_b", "gone_c"])
    new = _snapshot("2.0", ["keep"])
    extra = [Change(ChangeKind.CONSTANT_REMOVED, "K1", "constant K1 removed")]
    path = tmp_path / "acks.yml"
    path.write_text(
        "version: 1\nacknowledgments:\n"
        "  - symbol: gone_a\n    change_kind: func_removed\n    reason: planned\n"
        "  - symbol: K1\n    change_kind: constant_removed\n    reason: planned\n"
    )
    kwargs = {"acknowledgment_breaks": "accept"} if scenario.breaks_accept else {}
    return checker.compare(
        old,
        new,
        extra_changes=extra,
        acknowledgments=AcknowledgmentList.load(path),
        **kwargs,
    )


def _process_exit(result: DiffResult, severity: SeverityConfig | None) -> int:
    """The exit the CLI gives: the one decision every surface must state."""
    try:
        _exit_with_severity_or_verdict(result, EffectiveGate.from_severity(severity))
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _render(result: DiffResult, severity: SeverityConfig | None) -> dict[str, str]:
    old = _snapshot("1.0", [])
    new = _snapshot("2.0", [])
    return {
        fmt: render_output(fmt, result, old, new, severity_config=severity)
        for fmt in (
            "json",
            "markdown",
            "review",
            "terminal",
            "html",
            "junit",
            "sarif",
            "oneline",
        )
    }


def _gating_statements(rendered: dict[str, str]) -> dict[str, int]:
    """Every gating count a rendering states, keyed by where it appears."""
    doc = json.loads(rendered["json"])
    found: dict[str, int] = {
        "json.result_counts.gating": doc["result_counts"]["gating"],
        "json.disposition_audit.effective_total": doc["disposition_audit"][
            "effective_total"
        ],
        "json.review_groups": sum(
            group["gating_findings"] for group in doc["review_groups"]
        ),
    }
    markdown = rendered["markdown"]
    found["markdown.effective"] = int(
        re.search(r"\| Effective \(gating\) \| (\d+) \|", markdown).group(1)
    )
    found["markdown.related"] = sum(
        int(v) for v in re.findall(r"Gate contribution:\*\* (\d+) finding", markdown)
    )
    found["review.digest"] = int(
        re.search(r"\*\*Review:\*\* (\d+) gating finding", rendered["review"]).group(1)
    )
    found["terminal.audit"] = int(
        re.search(r"Audit: \d+ detected; (\d+) gating", rendered["terminal"]).group(1)
    )
    found["oneline.audit"] = int(
        re.search(r"(\d+) gating", rendered["oneline"]).group(1)
    )
    found["junit.disposition"] = int(
        re.search(
            r'abicheck\.disposition\.gating" value="(\d+)"', rendered["junit"]
        ).group(1)
    )
    found["sarif.gating"] = int(
        re.search(r'"gating": (\d+)', rendered["sarif"]).group(1)
    )
    return found


def _exit_statements(rendered: dict[str, str], *, severity: bool) -> dict[str, int]:
    doc = json.loads(rendered["json"])
    found = {
        "json.exit.code": doc["exit"]["code"],
        "sarif.exitCode": int(
            re.search(r'"exitCode": (\d+)', rendered["sarif"]).group(1)
        ),
    }
    if severity:
        # The legacy scheme has no severity block to state an exit in.
        found["json.severity.exit_code"] = doc["severity"]["exit_code"]
    return found


@pytest.mark.parametrize("name", sorted(_SCENARIOS))
def test_every_surface_states_the_gating_count_the_exit_was_decided_by(
    tmp_path: Path, name: str
) -> None:
    scenario = _SCENARIOS[name]
    result = _run(tmp_path, scenario)
    process_exit = _process_exit(result, scenario.severity)
    assert process_exit == scenario.exit_code, "the scenario must exit as designed"

    rendered = _render(result, scenario.severity)
    gating = _gating_statements(rendered)
    assert gating == {key: scenario.gating for key in gating}, gating

    exits = _exit_statements(rendered, severity=scenario.severity is not None)
    assert exits == {key: process_exit for key in exits}, exits
