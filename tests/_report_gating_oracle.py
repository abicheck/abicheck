# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Cross-format oracle: every gating count a report states equals the JSON's.

Shared by the fast stored-snapshot lane
(``tests/unit/report/test_report_gating_consistency.py``) and the compiled
example lane (``tests/test_report_gating_consistency_examples.py``).

The oracle is the JSON document's own per-finding ADR-049 D1
``gate_contribution`` (stamped whenever a finding carries a contract
decision) and, independently, the policy-owned disposition ledger's
``effective_total`` and the gate's exit code -- never the review-group
builder or the ``gating_totals`` helper the renderers use. SVS field report:
a review group of contract-excluded breaks rendered "6 gating finding(s)"
while every member's JSON ``gate_contribution`` was ``0``.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from abicheck.contract_relevance_types import ContractRelevance, evaluation_status_for
from abicheck.policy.severity import SeverityConfig, SeverityLevel
from abicheck.report.dispatch_markdown import to_markdown, to_review_digest
from abicheck.reporter import to_json
from tests._report_test_helpers import envelope_for

if TYPE_CHECKING:
    from abicheck.checker_types import DiffResult

E, W, INFO = SeverityLevel.ERROR, SeverityLevel.WARNING, SeverityLevel.INFO

#: Gate schemes: legacy verdict exit, the default severity preset, a preset
#: that demotes ABI breaks (the "gate passes over breaking findings" shape),
#: and one that promotes every category to an error.
SEVERITY_CONFIGS: dict[str, SeverityConfig | None] = {
    "legacy": None,
    "default": SeverityConfig(),
    "abi_warning": SeverityConfig(abi_breaking=W),
    "all_error": SeverityConfig(
        abi_breaking=E, potential_breaking=E, quality_issues=E, addition=E
    ),
    "all_info": SeverityConfig(
        abi_breaking=INFO, potential_breaking=INFO, quality_issues=INFO, addition=INFO
    ),
}

#: Contract decision stamped on every finding: none (no ``--contract``),
#: all in contract, all proven out (the SVS shape), and an alternating mix.
CONTRACT_STATES = ("none", "in", "out", "mixed")

_RELATED = re.compile(
    r"(\d+) review group\(s\) across \d+ famil(?:y|ies); (\d+) gating finding\(s\)"
)
_GATE_CONTRIBUTION = re.compile(r"\*\*Gate contribution:\*\* (\d+) finding\(s\)")
_DIGEST_REVIEW = re.compile(
    r"\*\*Review:\*\* (\d+) gating finding\(s\) in (\d+) gating group\(s\); "
    r"(\d+) retained group\(s\) total"
)
_POPULATIONS = re.compile(r"retained \((\d+) gating, (\d+) non-gating\)")


def stamp_contract(result: DiffResult, state: str) -> None:
    """Give every finding of *result* the contract decision *state* names."""
    if state == "none":
        return
    for index, change in enumerate(result.changes):
        out = state == "out" or (state == "mixed" and index % 2)
        relevance = (
            ContractRelevance.PROVEN_OUT_OF_CONTRACT
            if out
            else ContractRelevance.IN_CONTRACT
        )
        change.contract_relevance = relevance
        change.compatibility_evaluation_status = evaluation_status_for(relevance)
    # `compare()` closed its ledger before this stamping; a real `--contract`
    # run stamps first. Drop it so the policy fallback re-finalizes over the
    # stamped findings, as the real pipeline would have recorded them.
    result.disposition_ledger = None


def _one(pattern: re.Pattern[str], text: str) -> tuple[int, ...] | None:
    matches = pattern.findall(text)
    assert len(matches) <= 1, f"{pattern.pattern!r} stated twice"
    if not matches:
        return None
    match = matches[0]
    return tuple(int(v) for v in (match if isinstance(match, tuple) else (match,)))


def render_all(
    result: DiffResult, severity_config: SeverityConfig | None
) -> tuple[dict[str, Any], str, str]:
    """JSON (parsed), full Markdown and review digest from one envelope."""
    envelope = envelope_for(result, severity_config=severity_config)
    document = json.loads(to_json(result, severity_config=severity_config))
    markdown = to_markdown(result, severity_config=severity_config, envelope=envelope)
    digest = to_review_digest(result, envelope=envelope)
    return document, markdown, digest


def assert_gating_consistent(
    result: DiffResult, severity_config: SeverityConfig | None, *, contract: str
) -> int:
    """Assert every format's gating counts agree; return the gating total."""
    document, markdown, digest = render_all(result, severity_config)
    entries = {entry["finding_id"]: entry for entry in document["changes"]}
    groups = document["review_groups"]

    # JSON review groups against the JSON per-finding field.
    for group in groups:
        members = [entries[fid] for fid in group["member_finding_ids"]]
        if contract != "none":
            expected = sum(entry["gate_contribution"] > 0 for entry in members)
            assert group["gating_findings"] == expected, group["display_name"]
        if contract == "out":
            assert group["gating_findings"] == 0, group["display_name"]
    total = sum(group["gating_findings"] for group in groups)
    gating_groups = sum(group["gating_findings"] > 0 for group in groups)
    if contract != "none":
        assert total == sum(e["gate_contribution"] > 0 for e in entries.values())

    # JSON against the policy ledger and the gate itself.
    # A redundant finding kept for the legacy verdict is `gating` in the
    # ledger but in no review group (docs/contribute/known-gaps.md, "The
    # legacy gate counts a redundant finding the review groups omit").
    counts = document["result_counts"]
    held_out = 0 if severity_config is not None else document.get("redundant_count", 0)
    assert total <= counts["gating"] <= total + held_out
    # The retained population is partitioned exactly: its gating part is the
    # review groups' sum, and gating beyond it is only the folded redundants.
    assert counts["gating_retained"] == total
    assert counts["gating_retained"] + counts["non_gating"] == counts["retained"]
    assert counts["gating_review_groups"] == gating_groups
    assert document["disposition_audit"]["effective_total"] == counts["gating"]
    if severity_config is not None:
        assert (total > 0) == (document["severity"]["exit_code"] > 0)

    # Full Markdown: the summary line and every per-family contribution.
    related = _one(_RELATED, markdown)
    if groups:
        assert related == (len(groups), total)
        shown = [int(v) for v in _GATE_CONTRIBUTION.findall(markdown)]
        if "more famil" in markdown:
            assert sum(shown) <= total
        else:
            assert sum(shown) == total
    else:
        assert related is None

    # Review digest: the review line and the count-population line.
    review = _one(_DIGEST_REVIEW, digest)
    if groups:
        assert review == (total, gating_groups, len(groups))
    populations = _one(_POPULATIONS, digest)
    if populations is not None:
        assert populations[0] == counts["gating_retained"]
    return total
