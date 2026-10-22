# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The envelope-driven renders agree with the direct-caller path, byte for byte.

``reporter_markdown.to_stat`` and the Markdown severity table read their gate,
disposition audit and per-category counts from the completed envelope; a
direct caller with no envelope resolves the same values through the same
primitives. These tests pin that the two routes cannot disagree, and that the
one-line view reads the envelope's own result.
"""

from __future__ import annotations

import pytest

from abicheck.checker import Change, ChangeKind, Verdict
from abicheck.frontends.render import render_output
from abicheck.model import AbiSnapshot
from abicheck.policy.severity import PRESET_STRICT
from abicheck.report.dispatch_markdown import to_markdown
from abicheck.reporter_markdown import compute_severity_summary, to_stat
from tests._report_test_helpers import envelope_for, make_result

_SNAP = AbiSnapshot(library="libtest.so.1", version="1.0")


def _removed_result():
    change = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo")
    return make_result(Verdict.BREAKING, changes=[change])


def _no_change_result():
    return make_result(Verdict.NO_CHANGE)


def _compatible_result():
    change = Change(ChangeKind.FUNC_ADDED, "_Z3barv", "Public function added: bar")
    return make_result(Verdict.COMPATIBLE, changes=[change])


@pytest.mark.parametrize(
    "build", [_removed_result, _no_change_result, _compatible_result]
)
@pytest.mark.parametrize("config", [None, PRESET_STRICT])
def test_one_line_view_matches_the_envelope_path_for_every_sibling_pair(
    build, config
) -> None:
    """Sibling inputs: several verdicts, each with and without a severity gate."""
    result = build()
    via_entry_point = render_output("oneline", result, _SNAP, severity_config=config)
    via_envelope = to_stat(
        result, envelope=envelope_for(result, severity_config=config)
    )
    assert via_envelope == via_entry_point


def test_one_line_view_reads_the_envelopes_own_result() -> None:
    """A caller's ``result`` argument cannot mix in fields from another evaluation."""
    result = _removed_result()
    envelope = envelope_for(result, severity_config=PRESET_STRICT)
    other = make_result(Verdict.NO_CHANGE)

    assert to_stat(other, envelope=envelope) == to_stat(result, envelope=envelope)


def test_root_cause_markdown_envelope_and_direct_paths_are_identical() -> None:
    result = _removed_result()
    via_envelope = render_output(
        "markdown",
        result,
        _SNAP,
        severity_config=PRESET_STRICT,
        report_mode="root-cause",
        demangle=False,
    )
    direct = to_markdown(
        result,
        report_mode="root-cause",
        severity_config=PRESET_STRICT,
        show_recommendation=True,
        demangle=False,
    )
    assert via_envelope == direct


def test_full_markdown_envelope_and_direct_paths_are_identical() -> None:
    result = _removed_result()
    via_envelope = render_output(
        "markdown", result, _SNAP, severity_config=PRESET_STRICT, demangle=False
    )
    direct = to_markdown(
        result, severity_config=PRESET_STRICT, show_recommendation=True, demangle=False
    )
    assert via_envelope == direct


def test_severity_summary_uses_precomputed_counts_for_both_columns() -> None:
    summary = compute_severity_summary(
        PRESET_STRICT,
        counts={"abi_breaking": 2},
        exit_counts={"abi_breaking": 1},
    )
    abi_row = summary.rows[0]
    assert abi_row.count == 2
    assert abi_row.impact == "causes non-zero exit"

    no_exit = compute_severity_summary(
        PRESET_STRICT,
        counts={"abi_breaking": 2},
        exit_counts={},
    )
    assert no_exit.rows[0].impact == "no exit impact"


def test_severity_summary_scoped_gate_numbers_override_the_display_counts() -> None:
    summary = compute_severity_summary(
        PRESET_STRICT,
        counts={"abi_breaking": 2},
        exit_counts={},
        scoped_counts={"abi_breaking": 5, "informational": 3},
        scoped_blocking_categories=("abi_breaking",),
    )
    abi_row = summary.rows[0]
    assert abi_row.count == 5
    assert abi_row.impact == "causes non-zero exit"
    assert summary.rows[-1].count == 3
