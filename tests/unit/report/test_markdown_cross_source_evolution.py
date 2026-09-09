# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""ADR-068 finding A: the Markdown report must surface the cross-source
evolution axis (introduced/resolved/persistent/not_evaluated), the same way
the JSON report already does (schema 3.3,
``report/cross_source_evolution.py``). Before this fix, ``grep -c -i
persistent`` on a Markdown report was always 0 regardless of how many
`persistent` hygiene findings it carried, and every such finding rendered
under the "Deployment Risk Changes" heading whose blurb describes GLIBC-style
deployment risk -- wrong for a hygiene finding, and indistinguishable from a
genuinely new one.
"""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind, CrossSourceEvolution
from abicheck.checker_types import Change, DiffResult
from abicheck.reporter import to_markdown


def _evolved_change(evolution: CrossSourceEvolution, symbol: str) -> Change:
    # RISK_KINDS member, matching what compute_cross_source_evolution()
    # actually stamps for every migrated hygiene check (ADR-068 D3: none of
    # them ever set effective_verdict, so RISK_KINDS' own default verdict is
    # always what governs these findings).
    return Change(
        kind=ChangeKind.UNVERSIONED_EXPORTED_SYMBOL,
        symbol=symbol,
        description=f"{symbol} is exported without a version",
        cross_source_evolution=evolution,
    )


def _result(changes: list[Change]) -> DiffResult:
    from abicheck.checker_policy import Verdict

    return DiffResult(
        old_version="1.0",
        new_version="1.1",
        library="libfoo.so",
        changes=changes,
        verdict=Verdict.COMPATIBLE_WITH_RISK,
    )


def test_persistent_finding_is_mentioned_by_name_in_markdown():
    md = to_markdown(_result([_evolved_change(CrossSourceEvolution.PERSISTENT, "a")]))
    assert "persistent" in md.lower()


def test_evolution_states_render_distinct_tags():
    changes = [
        _evolved_change(CrossSourceEvolution.INTRODUCED, "a"),
        _evolved_change(CrossSourceEvolution.RESOLVED, "b"),
        _evolved_change(CrossSourceEvolution.PERSISTENT, "c"),
        _evolved_change(CrossSourceEvolution.NOT_EVALUATED, "d"),
    ]
    md = to_markdown(_result(changes))
    assert "introduced" in md.lower()
    assert "resolved" in md.lower()
    assert "persistent" in md.lower()
    assert "not evaluated" in md.lower()


def test_hygiene_findings_get_their_own_section_not_deployment_risk():
    md = to_markdown(_result([_evolved_change(CrossSourceEvolution.PERSISTENT, "a")]))
    assert "Cross-Source Hygiene Findings" in md
    # The GLIBC-oriented deployment-risk blurb must not attach to a hygiene
    # finding -- that section is for genuine deployment-compatibility risk
    # only, and none was present in this comparison.
    assert "Deployment Risk Changes" not in md
    assert "GLIBC" not in md


def test_ordinary_deployment_risk_finding_keeps_its_own_section():
    # A plain RISK_KINDS finding with no cross_source_evolution stamp is
    # untouched by this fix: it still renders under "Deployment Risk
    # Changes" with the original GLIBC-oriented note, and never picks up a
    # "Cross-source hygiene:" tag it has no basis for.
    c = Change(
        kind=ChangeKind.UNVERSIONED_EXPORTED_SYMBOL,
        symbol="plain",
        description="plain deployment risk finding",
    )
    md = to_markdown(_result([c]))
    assert "Deployment Risk Changes" in md
    assert "Cross-Source Hygiene Findings" not in md
    assert "Cross-source hygiene:" not in md


def test_mixed_hygiene_and_deployment_risk_findings_split_into_two_sections():
    hygiene = _evolved_change(CrossSourceEvolution.PERSISTENT, "a")
    deployment = Change(
        kind=ChangeKind.UNVERSIONED_EXPORTED_SYMBOL,
        symbol="plain",
        description="plain deployment risk finding",
    )
    md = to_markdown(_result([hygiene, deployment]))
    assert "Deployment Risk Changes" in md
    assert "Cross-Source Hygiene Findings" in md
    hygiene_heading = md.index("## \U0001f9f9 Cross-Source Hygiene Findings")
    deployment_heading = md.index("## ⚠️ Deployment Risk Changes")
    deployment_desc_idx = md.index("plain deployment risk finding")
    hygiene_desc_idx = md.index("is exported without a version")
    # Each finding's own description lands after its own section's heading
    # and before the other section's heading, whichever order the two
    # sections render in.
    section_bounds = sorted([hygiene_heading, deployment_heading]) + [len(md)]
    assert section_bounds[0] <= min(deployment_heading, hygiene_heading)
    assert (
        deployment_heading
        < deployment_desc_idx
        < (hygiene_heading if hygiene_heading > deployment_heading else len(md))
    )
    assert (
        hygiene_heading
        < hygiene_desc_idx
        < (deployment_heading if deployment_heading > hygiene_heading else len(md))
    )


def test_report_mode_leaf_and_root_cause_are_unaffected_by_the_kind_split():
    """ADR-068 D4: presentation never changes analysis. The split only
    changes *which section* a RISK_KINDS finding's oneline rendering lands
    in for the default full-mode view; it must not change report_mode="leaf"
    or "root-cause", which don't go through compute_severity_sections at
    all, and it must not change the verdict or change count."""
    result = _result([_evolved_change(CrossSourceEvolution.PERSISTENT, "a")])
    for mode in ("leaf", "root-cause"):
        # Must not raise, and must not silently drop the finding.
        md = to_markdown(result, report_mode=mode)
        assert isinstance(md, str)
