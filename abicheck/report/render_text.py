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

"""The pure text projection for ``--stat`` output: the one-line
``LABEL: detail (N total) [...]`` summary format.

ADR-061 Phase 2: :func:`render_stat_document` is a real ``ReportDocument ->
str`` projection — it reads only already-resolved primitives (a verdict
label, summary counts, an optional precomputed severity exit code) out of
the document's mapping and formats them. It never classifies a change,
computes an exit code, or otherwise makes a compatibility/gate decision;
that happens once, before the document is built, exactly as D9 (renderers
are pure projections) requires. :func:`format_stat_line` is the lower-level
formatter shared with a second caller that has no ``ReportDocument`` to
build one from: ``cli_compare_fold._ScopedFold.into_oneline`` (CLI cleanup
phase two, PR 1's ``-o oneline=...`` + ``--used-by``/``consumers.required_symbols``
scoped-gate fix) renders the identical layout from a *scoped* verdict/counts
pair instead of a whole-library comparison result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .document import ReportDocument


#: Hygiene states rendered in the ``; hygiene: ...`` clause, in the order a
#: reviewer cares about them: what this release *did* to the standing
#: inventory first, what it left alone after.
_HYGIENE_CLAUSE_ORDER: tuple[tuple[str, str], ...] = (
    ("hygiene_introduced", "introduced"),
    ("hygiene_resolved", "resolved"),
    ("hygiene_persistent", "persistent"),
    ("hygiene_not_evaluated", "not evaluated"),
)


#: The clause noun a one-line summary gives standing cross-source hygiene
#: findings in its breakdown. The report's own vocabulary for the same
#: population (``render_review``'s "Header/binary consistency" section).
HYGIENE_CLAUSE_NOUN = "header/binary consistency"


def format_hygiene_note(inventory: Mapping[str, int] | None) -> str:
    """``"; hygiene: 32 persistent"`` for a run carrying standing inventory.

    Empty for a run with no cross-source hygiene finding at all, which is
    every comparison whose findings are all genuine compatibility changes --
    so a pre-existing report's one-line summary is unchanged.
    """
    if not inventory:
        return ""
    parts = [
        f"{inventory[key]} {label}"
        for key, label in _HYGIENE_CLAUSE_ORDER
        if inventory.get(key)
    ]
    if not parts:
        return ""
    return "; hygiene: " + ", ".join(parts)


def hygiene_total(inventory: Mapping[str, int] | None) -> int:
    """Every stamped (cross-source hygiene) finding, in any evolution state.

    The complement of ``compatibility_changes`` within the JSON
    ``summary.total_changes``: the four ``hygiene_*`` counters of
    ``report/change_inventory.py`` partition it.
    """
    if not inventory:
        return 0
    return sum(int(inventory.get(key, 0)) for key, _label in _HYGIENE_CLAUSE_ORDER)


#: Every outcome bucket a one-line summary states, as ``(summary key, clause
#: noun)``, in display order. The keys are the ``summary`` block's own JSON
#: names, and every bucket the report counts appears here exactly once: the
#: breakdown is a projection of this table, never a hand-kept subset of it.
#: ``tests/unit/report/test_oneline_breakdown_sums.py`` pins this table
#: against the JSON summary's bucket keys. A count the JSON reports but this
#: table omits is the ``report.headline_breakdown_drift`` bug class: the
#: headline's parts no longer add up to its total.
OUTCOME_BUCKETS: tuple[tuple[str, str], ...] = (
    ("breaking", "breaking"),
    ("source_breaks", "source-level breaks"),
    ("risk_changes", "risk"),
    ("compatible_additions", "compatible"),
    ("quality_issues", "quality"),
    ("informational", "informational"),
)


def format_stat_line(
    label: str,
    *,
    outcomes: Mapping[str, int],
    total_changes: int,
    redundant_count: int = 0,
    gate_note: str = "",
    audit_note: str = "",
    deployment_note: str = "",
    change_inventory: Mapping[str, int] | None = None,
) -> str:
    """Render already-resolved counts/label as the one-line summary.

    *outcomes* maps every :data:`OUTCOME_BUCKETS` key to its count. A missing
    key raises ``KeyError`` rather than defaulting to zero: a silent zero is
    exactly how a bucket drops out of the parts while its findings stay in
    the total.

    The breakdown always adds up to *total_changes*, which is the JSON
    ``summary.total_changes`` population (ADR-067: a finding is recorded
    before any disposition acts on it, so standing hygiene findings are
    counted, not dropped). Whatever the compatibility buckets do not name is
    a finding the compatibility policy did not score (``NOT_EVALUATED``,
    ADR-049 D1); it is stated as ``N not evaluated`` rather than dropped, so
    "5 breaking (6 total)" can never be the line for a run that observed six
    findings. Standing hygiene findings (*change_inventory*'s ``hygiene_*``
    counters) are stated as their own ``N header/binary consistency`` clause.

    *audit_note* is ADR-067 D3's raw-versus-effective suffix, already
    formatted by ``report.disposition_audit.render_disposition_audit_note``.
    Defaulted to the empty string so the one caller with no whole-library
    result to compute one from (``cli_compare_fold._ScopedFold.into_oneline``,
    which renders a *scoped* consumer gate) is unchanged.

    *deployment_note* is the ``; deployment floor <digest>`` clause (Codex
    review, fresh evidence) -- the same convention
    ``report.no_baseline.render_no_baseline_oneline`` already established
    for the no-baseline audit report's own oneline renderer, kept consistent
    here rather than reinvented. Empty (the default) when no declared
    ``deployment:`` contract governed this comparison.
    """
    hygiene_note = format_hygiene_note(change_inventory)
    standing = hygiene_total(change_inventory)
    counts = {key: outcomes[key] for key, _noun in OUTCOME_BUCKETS}
    if standing and change_inventory is not None:
        # Standing hygiene inventory is still a finding the JSON summary
        # counts, so the total below stays the JSON ``total_changes``; it is
        # not dropped from the total. The compatibility buckets, though, must
        # not carry it: the JSON bucket counts fold a persistent finding into
        # its verdict's bucket (a PERSISTENT ``exported_not_public`` is
        # ``risk_changes``), which would announce ``NO_CHANGE: 32 risk`` for a
        # byte-identical rebuild. So the compatibility clauses are the
        # observed subset the inventory block states, and the standing
        # findings get their own clause.
        #
        # The inventory block does not split observed compatible findings
        # into additions / quality / informational (its JSON shape is fixed),
        # so in this mode the whole observed COMPATIBLE verdict is the
        # ``compatible`` clause and the two finer clauses are zero. Recorded
        # as a gap in docs/contribute/known-gaps.md.
        counts = dict.fromkeys(counts, 0)
        counts["breaking"] = change_inventory.get(
            "compatibility_breaking", outcomes["breaking"]
        )
        counts["source_breaks"] = change_inventory.get(
            "compatibility_source_breaks", outcomes["source_breaks"]
        )
        counts["risk_changes"] = change_inventory.get(
            "compatibility_risk", outcomes["risk_changes"]
        )
        counts["compatible_additions"] = change_inventory.get(
            "compatibility_compatible", outcomes["compatible_additions"]
        )
    parts = [f"{counts[key]} {noun}" for key, noun in OUTCOME_BUCKETS if counts[key]]
    # What no compatibility bucket names, and is not standing inventory, is a
    # finding the compatibility policy did not score (ADR-049 D1).
    not_evaluated = total_changes - sum(counts.values()) - standing
    if not_evaluated:
        parts.append(f"{not_evaluated} not evaluated")
    if standing:
        if not parts:
            parts.append("no compatibility changes")
        parts.append(f"{standing} {HYGIENE_CLAUSE_NOUN}")
    if parts:
        detail = ", ".join(parts)
    else:
        detail = "no changes"
    redundant_note = (
        f" [{redundant_count} redundant hidden]" if redundant_count > 0 else ""
    )
    return (
        f"{label}: {detail} ({total_changes} total)"
        f"{redundant_note}{hygiene_note}{audit_note}{deployment_note}{gate_note}"
    )


def render_stat_document(document: ReportDocument) -> str:
    """Render a stat-mode :class:`ReportDocument` as the one-line summary.

    Expects the document's root to carry ``verdict_label`` (a pre-resolved
    display label, not the raw ``Verdict`` value), a ``summary`` object with
    every :data:`OUTCOME_BUCKETS` key plus ``total_changes``, an optional
    top-level ``redundant_count``, and an
    optional ``severity`` object with ``exit_code`` — the same shape
    :func:`abicheck.report.diff_report.to_stat_json`'s severity block already carries,
    so a future caller building one document for both the JSON and text
    stat outputs only has to supply these fields once. The ``gate:``
    suffix is pure formatting of that already-resolved ``exit_code``, not a
    new computation of it. An optional top-level ``env_matrix_source_
    sha256`` (Codex review, fresh evidence) becomes the ``; deployment
    floor <digest>`` clause -- omitted when absent, the same additive
    convention every other field here already follows.
    """
    d = document.to_mapping()
    summary = d["summary"]
    assert isinstance(summary, dict)  # narrows for the keyword calls below
    redundant_count = d.get("redundant_count", 0)
    assert isinstance(redundant_count, int)
    gate_note = ""
    severity = d.get("severity")
    if isinstance(severity, dict) and "exit_code" in severity:
        exit_code = severity["exit_code"]
        gate_note = (
            f" [gate: FAIL (exit {exit_code})]" if exit_code else " [gate: PASS]"
        )
    audit_note = d.get("disposition_audit_note", "")
    assert isinstance(audit_note, str)
    inventory = summary.get("change_inventory")
    if not isinstance(inventory, dict):
        inventory = None
    digest = d.get("env_matrix_source_sha256")
    deployment_note = f"; deployment floor {digest}" if digest is not None else ""
    return format_stat_line(
        str(d["verdict_label"]),
        outcomes={key: summary[key] for key, _noun in OUTCOME_BUCKETS},
        total_changes=summary["total_changes"],
        redundant_count=redundant_count,
        gate_note=gate_note,
        audit_note=audit_note,
        deployment_note=deployment_note,
        change_inventory=inventory,
    )
