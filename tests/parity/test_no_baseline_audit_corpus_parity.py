# SPDX-License-Identifier: Apache-2.0
"""The G20 acceptance corpus for ``compare --no-baseline``'s audit findings.

``docs/contribute/known-gaps.md``'s "``compare --no-baseline`` does not yet
reproduce ``scan``'s audit-mode findings" entry named this corpus as the
gate for its own fix, and this module is that gate: for every one of the
eleven committed G20 audit/cross-source fixtures,
``compare --no-baseline FIXTURE`` must report **the same or a strictly
richer** candidate-side finding set than ``scan FIXTURE`` reports today,
with nothing manufactured.

The two halves of that sentence are separate assertions, because only
checking one is how the gap got in:

* **No capability loss.** Every check ``scan``'s ``crosscheck.
  counts_by_check`` reports must appear in ``compare --no-baseline``'s
  ``findings[]`` at least as many times. This is the half the gap failed --
  before the fix, all eleven fixtures aborted with an ``AssertionError``
  from ``workflows/no_baseline_compare.py``'s blanket ``assert not
  diff.changes``, so the audit reported nothing at all.
* **Nothing manufactured.** Every reported finding must be genuinely
  candidate-side (``policy.no_baseline_findings.is_one_sided_finding``) and
  must carry an ADR-068 D3-permitted evolution state. "Richer" may only
  ever mean "``compare`` runs a check ``scan``'s audit mode does not", never
  "``compare`` invented a comparison finding out of a self-diff".

The corpus is read through ``scripts/example_catalog`` (the same accessor
``tests/test_g20_catalog.py`` and this package's sibling parity modules
use), so a renamed or deleted case fails loudly here rather than silently
shrinking the acceptance set.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402

from .runner import invoke_cli, scan_json  # noqa: E402

#: Every G20 audit/cross-source fixture, as ``(case name, file name)``.
#: ``case151`` contributes two: its full snapshot and the deliberately
#: evidence-thin ``thin.abi.json`` variant, which is the corpus's own
#: "weaker evidence narrows conclusions" case and therefore exactly the one
#: an audit path most easily gets wrong.
G20_AUDIT_FIXTURES: tuple[tuple[str, str], ...] = (
    ("case143_audit_accidental_export", "snapshot.abi.json"),
    ("case144_audit_private_header_leak", "snapshot.abi.json"),
    ("case145_audit_unversioned_export", "snapshot.abi.json"),
    ("case146_audit_rtti_for_internal", "snapshot.abi.json"),
    ("case147_scan_depth_ladder", "snapshot.abi.json"),
    ("case148_xcheck_header_build_mismatch", "snapshot.abi.json"),
    ("case149_xcheck_odr_variant", "snapshot.abi.json"),
    ("case150_xcheck_export_public_pair", "snapshot.abi.json"),
    ("case151_xcheck_provider_matrix", "snapshot.abi.json"),
    ("case151_xcheck_provider_matrix", "thin.abi.json"),
    ("case181_xcheck_public_to_internal_dependency", "snapshot.abi.json"),
)

#: The eleven distinct cases the corpus above covers -- asserted separately
#: from the fixture list so adding a second variant of an existing case
#: cannot quietly be mistaken for adding a new case.
_EXPECTED_CASE_COUNT = 10


def _fixture_path(case_name: str, filename: str) -> Path:
    path = example_catalog.case_dir(case_name) / filename
    assert path.is_file(), f"missing committed G20 fixture: {path}"
    return path


def _no_baseline_report(path: Path, *extra: str) -> dict:
    result = invoke_cli(
        "compare", "--no-baseline", str(path), "--format", "json", *extra
    )
    assert result.exit_code in (0, 1), (
        f"compare --no-baseline aborted on {path.name} "
        f"(exit={result.exit_code}):\n{result.output}"
    )
    return json.loads(result.stdout)


def _scan_check_counts(path: Path) -> Counter:
    """``scan``'s audit-mode per-check finding counts for *path*.

    ``scan``'s audit mode (no ``--against``) has no ``diff.findings`` to
    read -- there is no baseline to diff -- so its cross-source findings are
    published as ``crosscheck.counts_by_check``. That is the comparable unit
    against ``compare --no-baseline``'s own per-kind ``findings[]``, and the
    check names are the same ``ChangeKind`` values on both sides.
    """
    report = scan_json(path)
    return Counter(report.get("crosscheck", {}).get("counts_by_check") or {})


def _no_baseline_kind_counts(report: dict) -> Counter:
    return Counter(finding["kind"] for finding in report["findings"])


def test_corpus_covers_every_committed_g20_audit_case() -> None:
    """The acceptance set is the whole corpus, not a convenient subset."""
    assert len({case for case, _ in G20_AUDIT_FIXTURES}) == _EXPECTED_CASE_COUNT
    for case_name, filename in G20_AUDIT_FIXTURES:
        _fixture_path(case_name, filename)


@pytest.mark.parametrize(("case_name", "filename"), G20_AUDIT_FIXTURES)
def test_no_baseline_reports_at_least_what_scan_reports(
    case_name: str, filename: str
) -> None:
    """No capability loss: every check ``scan`` fires, ``compare
    --no-baseline`` fires at least as often."""
    path = _fixture_path(case_name, filename)
    scan_counts = _scan_check_counts(path)
    assert scan_counts, (
        f"{case_name}/{filename} is in the acceptance corpus but scan reports no "
        "cross-source finding on it -- the fixture, not the audit path, has "
        "regressed"
    )
    audit_counts = _no_baseline_kind_counts(_no_baseline_report(path))
    missing = {
        kind: (count, audit_counts[kind])
        for kind, count in scan_counts.items()
        if audit_counts[kind] < count
    }
    assert not missing, (
        f"compare --no-baseline lost findings scan reports on {case_name}/"
        f"{filename}: {missing} (kind -> (scan count, audit count))"
    )


@pytest.mark.parametrize(("case_name", "filename"), G20_AUDIT_FIXTURES)
def test_no_baseline_manufactures_nothing(case_name: str, filename: str) -> None:
    """Nothing manufactured: every reported finding is genuinely
    candidate-side, in a D3-permitted state, and never an addition/removal.

    Checked against the *report*, not against the in-process partition, so
    this proves what a user actually receives rather than re-asserting an
    internal invariant through its own implementation.
    """
    from abicheck.policy.no_baseline_findings import NO_BASELINE_EVOLUTION_STATES

    permitted = {state.value for state in NO_BASELINE_EVOLUTION_STATES}
    report = _no_baseline_report(_fixture_path(case_name, filename))

    assert report["no_baseline"] is True
    assert report["verdict"] is None, "ADR-068 D2: an audit reports no verdict"
    assert report["changes"] == [], (
        "ADR-068 D2: an audit reports no addition, removal or modification -- "
        "the comparison change set must stay empty even now that findings[] is not"
    )
    assert report["run_outcome"]["compatibility"] is None

    for finding in report["findings"]:
        evolution = finding["evolution"]
        assert evolution is None or evolution in permitted, (
            f"{case_name}: finding {finding['kind']} reports evolution "
            f"{evolution!r}; with OLD declared_absent only "
            f"{sorted(permitted)} are permitted (ADR-068 D3)"
        )
        assert evolution is not None or finding["candidate_side_enrichment"], (
            f"{case_name}: finding {finding['kind']} carries neither a "
            "cross-source evolution state nor a candidate-side-enrichment "
            "marker, so it is a comparison finding a self-diff cannot produce"
        )


@pytest.mark.parametrize(("case_name", "filename"), G20_AUDIT_FIXTURES)
def test_no_baseline_exit_code_is_clean_without_a_contract(
    case_name: str, filename: str
) -> None:
    """An audit's hygiene findings never gate on their own (ADR-028 D3 /
    ADR-035 D1: they stay advisory), and without ``--contract`` there is no
    coverage axis either -- so every fixture exits 0. This is what makes the
    exit-1 contract case below a real signal rather than noise."""
    path = _fixture_path(case_name, filename)
    result = invoke_cli("compare", "--no-baseline", str(path), "--format", "json")
    assert result.exit_code == 0, result.output


def test_scan_and_no_baseline_agree_on_the_whole_corpus() -> None:
    """The corpus-wide statement, asserted once rather than per fixture.

    A per-fixture parametrization can pass while the corpus as a whole has
    silently shrunk (every remaining case agrees, because the disagreeing
    ones stopped being collected). This aggregates the same comparison over
    every fixture in one assertion, so the totals have to move together.
    """
    scan_total: Counter = Counter()
    audit_total: Counter = Counter()
    for case_name, filename in G20_AUDIT_FIXTURES:
        path = _fixture_path(case_name, filename)
        scan_total += _scan_check_counts(path)
        audit_total += _no_baseline_kind_counts(_no_baseline_report(path))
    assert sum(scan_total.values()) > 0
    deficits = {
        kind: (count, audit_total[kind])
        for kind, count in scan_total.items()
        if audit_total[kind] < count
    }
    assert not deficits, f"corpus-wide capability loss: {deficits}"
