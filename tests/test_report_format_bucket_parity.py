"""Cross-format parity: JSON, Markdown, HTML and the PR comment agree on
which bucket (added/removed/changed) each finding belongs to, how many
removals there are, and which findings were reclassified.

Bug class: ``report.cross_format_bucket_drift``. HTML used to bucket by two
hand-maintained kind subsets (``REMOVED_KINDS``/``ADDED_KINDS``) and title its
summary rows by kind-*name* prefix; Markdown's impact table counted
``kind.endswith("_removed")``. Both disagreed with the registry-declared
``operation``/``entity`` the JSON report and the PR comment's change summary
read -- e.g. ``func_deleted`` (a removal) read "changed" in HTML and was not
counted by Markdown, while ``func_noexcept_removed`` (a trait change on a
persisting function) was counted as a Markdown removal.

The oracle below is a hand-written table, not any renderer's helper or the
registry lookup the renderers now share: if the shared projection is wrong,
every format agrees *and* this table disagrees.

**One intended divergence.** JSON's per-finding ``operation`` and the PR
comment's entity-by-operation summary state the *observed* operation, which
policy never changes (AGENTS.md, "Policy decides acceptance, not facts").
HTML's green "Added" section (and its nav/summary counts) is the *effective*
bucket: an observed addition a policy escalates away from ``COMPATIBLE`` is
listed under "Changed", because "Added" reads as safe. The oracle states
both columns, and the tests assert each format against the one it renders.
"""

from __future__ import annotations

import json
import re
from collections import Counter

import pytest

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.frontends.render import render_envelope
from abicheck.html_report import build_html_document
from abicheck.model import AbiSnapshot
from abicheck.policy.reclassify import ReclassifyRule
from abicheck.policy_file import PolicyFile
from abicheck.pr_comment import build_model
from abicheck.report.build import build_report_envelope
from abicheck.report.envelope import RenderOptions, ReportEnvelope

_RULE_LABEL = "accepted-removal"

#: kind, symbol -> (observed operation, effective bucket, entity, reclassified_by)
_ORACLE: dict[tuple[str, str], tuple[str, str, str, str | None]] = {
    ("func_removed", "_Z3foov"): ("removed", "removed", "function", None),
    # Reclassified to COMPATIBLE by a selector-scoped `reclassify:` rule:
    # still a removal -- policy changes acceptance, not the observation.
    ("func_removed", "_Z3oldv"): ("removed", "removed", "function", _RULE_LABEL),
    # Escalated to BREAKING by a kind-global `overrides:` entry.
    ("func_added", "_Z3barv"): ("added", "changed", "function", None),
    ("var_added", "g_counter"): ("added", "added", "variable", None),
    # Previously "changed" in HTML (absent from ADDED_KINDS).
    ("needed_added", "libz.so.1"): ("added", "added", "binary", None),
    # Previously counted as a Markdown removal by its name suffix.
    ("func_noexcept_removed", "_Z3bazv"): ("modified", "changed", "function", None),
    ("param_default_value_removed", "_Z3quxi"): (
        "modified",
        "changed",
        "function",
        None,
    ),
    # Previously "changed" in HTML and uncounted by Markdown.
    ("func_deleted", "_Z3delv"): ("removed", "removed", "function", None),
    ("type_size_changed", "Point"): ("modified", "changed", "type", None),
}


def _expected(column: int) -> Counter[str]:
    return Counter(row[column] for row in _ORACLE.values())


def _snapshot(version: str) -> AbiSnapshot:
    return AbiSnapshot(library="libtest.so.1", version=version)


def _envelope(*, show_impact: bool = False) -> tuple[DiffResult, ReportEnvelope]:
    changes = [
        Change(ChangeKind(kind), symbol, f"{kind}: {symbol}")
        for kind, symbol in _ORACLE
    ]
    policy_file = PolicyFile(
        overrides={ChangeKind.FUNC_ADDED: Verdict.BREAKING},
        reclassify=[
            ReclassifyRule(
                to_verdict=Verdict.COMPATIBLE, symbol="_Z3oldv", label=_RULE_LABEL
            )
        ],
    )
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so.1",
        changes=changes,
        policy="strict_abi",
        policy_file=policy_file,
    )
    return result, build_report_envelope(
        result,
        _snapshot("1.0"),
        _snapshot("2.0"),
        options=RenderOptions(show_impact=show_impact),
    )


@pytest.fixture(scope="module")
def json_report() -> dict[str, object]:
    _, envelope = _envelope()
    return json.loads(render_envelope("json", envelope))


def test_projection_matches_oracle() -> None:
    _, envelope = _envelope()
    got = {
        (f.change.kind.value, f.change.symbol): (f.bucket, f.entity, f.reclassified_by)
        for f in envelope.findings
    }
    want = {key: (row[1], row[2], row[3]) for key, row in _ORACLE.items()}
    assert got == want


def test_json_states_observed_operation_entity_and_reclassified_by(
    json_report: dict[str, object],
) -> None:
    changes = json_report["changes"]
    assert isinstance(changes, list)
    got = {
        (c["kind"], c["symbol"]): (
            c["operation"],
            c["entity"],
            c.get("reclassified_by"),
        )
        for c in changes
    }
    want = {key: (row[0], row[2], row[3]) for key, row in _ORACLE.items()}
    assert got == want


def test_html_buckets_and_summary_table_match_oracle() -> None:
    result, envelope = _envelope()
    doc = build_html_document(result, envelope=envelope).to_mapping()
    effective = _expected(1)
    nav = doc["nav_bar"]
    assert (nav["removed"], nav["changed"], nav["added"]) == (
        effective["removed"],
        effective["changed"],
        effective["added"],
    )
    table = doc["summary_table"]
    assert (
        table["total_removed"],
        table["total_changed"],
        table["total_added"],
    ) == (effective["removed"], effective["changed"], effective["added"])
    # Rows: entity x effective bucket, from the oracle alone.
    want_rows: dict[str, Counter[str]] = {}
    labels = {
        "function": "Functions",
        "variable": "Variables",
        "type": "Types",
        "binary": "Binary/container",
    }
    for _op, bucket, entity, _rb in _ORACLE.values():
        want_rows.setdefault(labels[entity], Counter())[bucket] += 1
    got_rows = {
        row["label"]: Counter(
            {k: row[k] for k in ("removed", "changed", "added") if row[k]}
        )
        for row in table["rows"]
    }
    assert got_rows == want_rows

    html = render_envelope("html", envelope)
    assert f"Removed ({effective['removed']})" in html
    assert f"Changed ({effective['changed']})" in html
    assert f"Added ({effective['added']})" in html


def test_markdown_direct_removals_match_oracle() -> None:
    _, envelope = _envelope(show_impact=True)
    markdown = render_envelope("markdown", envelope)
    match = re.search(r"\| removals \((\d+)\) \| direct \|", markdown)
    assert match is not None, markdown
    assert int(match.group(1)) == _expected(1)["removed"]


def test_pr_comment_change_summary_and_reclassified_count(
    json_report: dict[str, object],
) -> None:
    model = build_model(json_report)
    observed = _expected(0)
    rows = model.change_summary.rows
    assert sum(r.removed for r in rows) == observed["removed"]
    assert sum(r.added for r in rows) == observed["added"]
    assert sum(r.modified for r in rows) == observed["modified"]
    # The intended divergence (module docstring): the escalated func_added
    # is an observed addition here, an effective "changed" in HTML.
    assert observed["added"] - _expected(1)["added"] == 1

    # `reclassified_count` counts *changes* moved by --policy-file: the one
    # `reclassify:` match plus every change of a kind-global `overrides:`
    # kind. HTML's "Policy reclassify" row lists the active *rules*, a
    # different fact (one rule here), not a competing count.
    by_rule = sum(1 for row in _ORACLE.values() if row[3] is not None)
    by_override = sum(1 for (kind, _s) in _ORACLE if kind == "func_added")
    assert model.reclassified_count == by_rule + by_override


def test_html_lists_each_active_reclassify_rule_once() -> None:
    result, envelope = _envelope()
    doc = build_html_document(result, envelope=envelope).to_mapping()
    reclassify = doc["confidence"]["policy_reclassify"]
    assert len(reclassify) == 1
    assert "_Z3oldv" in reclassify[0]
