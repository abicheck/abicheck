# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""One outcome, stated identically by every pair-report format.

``report/outcome_summary.project_outcome`` is the single projection of a
comparison's verdict label, merge-effect phrase, severity gate and consumer
scoped verdict. The oracle here is independent of it: the gate is the
envelope's own ``GateDecision`` (the policy decision owner), and the expected
merge-effect phrases are spelled out literally below. Each case runs over every
``Verdict`` crossed with every built-in gate state, and checks that the review
digest, the one-line ``--stat`` view, the full Markdown report and the HTML
gate card all carry the same outcome.
"""

from __future__ import annotations

import pytest

from abicheck.checker import Change, ChangeKind, Verdict
from abicheck.policy.severity import PRESET_DEFAULT, PRESET_INFO_ONLY, PRESET_STRICT
from abicheck.report.dispatch_markdown import to_markdown
from abicheck.report.html_compute import compute_gate_card, compute_scoped_verdict
from abicheck.report.outcome_summary import VERDICT_EMOJI, project_outcome
from abicheck.report.pr_comment_headline import _VERDICT_EMOJI as PR_COMMENT_EMOJI
from abicheck.report.render_release_report import _VERDICT_EMOJI as RELEASE_EMOJI
from tests._report_test_helpers import (
    envelope_for,
    make_result,
    review_digest,
    stat_line,
)

#: The phrase a reader sees per verdict when no severity gate is configured.
_UNGATED_EFFECT = {
    Verdict.NO_CHANGE: "no ABI/API change — safe to merge",
    Verdict.COMPATIBLE: "backward-compatible — safe to merge",
    Verdict.COMPATIBLE_WITH_RISK: "compatible but carries deployment risk — review advised",
    Verdict.API_BREAK: "source-level (API) break — consumers must recompile",
    Verdict.BREAKING: "binary (ABI) break — blocks merge under a strict gate",
}
_GATE_PASS = (
    "no error-level findings under the configured severity policy — safe to merge"
)
_GATE_FAIL = "blocked by severity policy — review required before merge"

#: One finding per verdict, so each gate scores a real change.
_CHANGE_FOR = {
    Verdict.NO_CHANGE: None,
    Verdict.COMPATIBLE: (ChangeKind.FUNC_ADDED, "_Z3addv"),
    Verdict.COMPATIBLE_WITH_RISK: (
        ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
        "GLIBC_2.38",
    ),
    Verdict.API_BREAK: (ChangeKind.ENUM_MEMBER_RENAMED, "Color::Red"),
    Verdict.BREAKING: (ChangeKind.FUNC_REMOVED, "_Z3foov"),
}
_CONFIGS = [None, PRESET_DEFAULT, PRESET_STRICT, PRESET_INFO_ONLY]


def _result(verdict: Verdict):
    spec = _CHANGE_FOR[verdict]
    changes = [] if spec is None else [Change(spec[0], spec[1], f"{spec[1]} changed")]
    return make_result(verdict, changes=changes)


@pytest.mark.parametrize("verdict", list(Verdict))
@pytest.mark.parametrize("config", _CONFIGS, ids=["none", "default", "strict", "info"])
def test_outcome_reads_the_envelopes_gate_and_states_its_phrase(
    verdict, config
) -> None:
    result = _result(verdict)
    envelope = envelope_for(result, severity_config=config)
    outcome = project_outcome(result, envelope=envelope)

    assert outcome.gate is envelope.gate
    if envelope.gate is None:
        assert outcome.merge_effect == _UNGATED_EFFECT[verdict]
        assert outcome.gate_exit_code is None
    else:
        expected = _GATE_PASS if envelope.gate.exit_code == 0 else _GATE_FAIL
        assert outcome.merge_effect == expected
        assert outcome.gate_exit_code == envelope.gate.exit_code
    assert outcome.verdict_label == verdict.value


@pytest.mark.parametrize("verdict", list(Verdict))
@pytest.mark.parametrize("config", _CONFIGS, ids=["none", "default", "strict", "info"])
def test_direct_caller_resolves_the_same_gate_as_the_envelope(verdict, config) -> None:
    result = _result(verdict)
    via_envelope = project_outcome(
        result, envelope=envelope_for(result, severity_config=config)
    )
    direct = project_outcome(result, severity_config=config)
    assert direct.gate == via_envelope.gate
    assert direct.merge_effect == via_envelope.merge_effect


@pytest.mark.parametrize("verdict", list(Verdict))
@pytest.mark.parametrize("config", _CONFIGS, ids=["none", "default", "strict", "info"])
def test_every_format_states_the_same_outcome(verdict, config) -> None:
    result = _result(verdict)
    envelope = envelope_for(result, severity_config=config)
    outcome = project_outcome(result, envelope=envelope)

    digest = review_digest(result, severity_config=config)
    assert outcome.merge_effect in digest
    assert (
        f"{outcome.verdict_emoji} `{outcome.verdict_label}` — {outcome.merge_effect}"
        in digest
    )

    assert stat_line(result, severity_config=config).startswith(outcome.verdict_label)

    markdown = to_markdown(result, severity_config=config, envelope=envelope)
    assert outcome.verdict_label in markdown

    card = compute_gate_card(result, config, envelope=envelope)
    if outcome.gate is None:
        assert card is None
    else:
        assert card is not None
        assert (card.passed, card.exit_code) == (
            not outcome.gate.blocking,
            outcome.gate.exit_code,
        )


@pytest.mark.parametrize("scoped", list(Verdict))
def test_scoped_verdict_box_reads_the_envelopes_scoped_gate(scoped) -> None:
    result = _result(Verdict.BREAKING)
    result.scoped_verdict = scoped
    result.scoped_exit_code = 3
    result.scoped_exit_code_scheme = "severity"
    envelope = envelope_for(result, severity_config=PRESET_DEFAULT)

    box = compute_scoped_verdict(result, envelope=envelope)
    assert box is not None
    assert (box.verdict_value, box.exit_code, box.exit_code_scheme) == (
        scoped.value,
        3,
        "severity",
    )
    assert (
        project_outcome(result, envelope=envelope).scoped_gate is envelope.scoped_gate
    )


def test_unscoped_run_has_no_scoped_verdict_box() -> None:
    result = _result(Verdict.BREAKING)
    envelope = envelope_for(result)
    assert compute_scoped_verdict(result, envelope=envelope) is None
    assert project_outcome(result, envelope=envelope).scoped_gate is None


@pytest.mark.parametrize("verdict", list(Verdict))
def test_release_and_pr_comment_glyphs_extend_the_shared_table(verdict) -> None:
    assert RELEASE_EMOJI[verdict.value] == VERDICT_EMOJI[verdict]
    assert PR_COMMENT_EMOJI[verdict.value] == VERDICT_EMOJI[verdict]
