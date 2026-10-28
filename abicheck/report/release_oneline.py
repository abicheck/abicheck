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

"""``compare -o oneline=...`` for a directory/package (release) operand.

The "just tell me" flow, at release cardinality. ``compare`` has rendered
``oneline`` for a single pair since CLI cleanup phase two PR 1; a directory
or package operand rejected it, along with ``sarif``/``html``/``review``,
because every one of those renderers takes a single ``DiffResult`` and a
release has N of them (and discards each before rendering, to bound peak
memory). ``oneline`` is the one of the four that never needed a
``DiffResult`` at all -- it is a count summary, and the release summary
already carries every count it wants -- so library count no longer decides
whether this format exists. The other three are recorded in
``docs/contribute/known-gaps.md`` with what each actually needs.

:func:`format_stat_line` is reused rather than reimplemented, so a release
line and a single-pair line cannot drift in wording, pluralization, or the
``gate:``/deployment-floor suffixes. What this module adds is the *label*
(how many libraries, and the release's own worst verdict) and the fold of
the per-library counts into one set.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from .render_text import OUTCOME_BUCKETS, format_stat_line, hygiene_total

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..model.change import Change

__all__ = ["format_release_oneline", "release_global_counts"]

#: Every outcome bucket a member entry carries (the release JSON's own keys),
#: read from the one table the single-pair line also projects. A release
#: breakdown therefore names exactly the buckets a single-pair one does.
_OUTCOME_KEYS = tuple(key for key, _noun in OUTCOME_BUCKETS)


#: A release-global finding's effective verdict -> the oneline bucket it
#: contributes to. ``COMPATIBLE`` is split further by category in
#: :func:`release_global_counts`. ``NO_CHANGE`` contributes to none (it is not
#: a finding's verdict in practice; kept out rather than folded into
#: ``compatible``, which would report it as an observed compatible change).
_VERDICT_BUCKET = {
    "BREAKING": "breaking",
    "API_BREAK": "source_breaks",
    "COMPATIBLE_WITH_RISK": "risk_changes",
    "COMPATIBLE": "compatible_additions",
}


#: How an outcome bucket maps onto the inventory's compatibility counters.
#: Release-global (bundle-coherence, probe-matrix) findings carry no
#: cross-source evolution stamp, so every one of them is an *observed*
#: compatibility change, never standing inventory. The inventory does not
#: split observed compatible findings, so all three compatible-family buckets
#: land on ``compatibility_compatible``.
_GLOBAL_TO_INVENTORY = {
    "breaking": "compatibility_breaking",
    "source_breaks": "compatibility_source_breaks",
    "risk_changes": "compatibility_risk",
    "compatible_additions": "compatibility_compatible",
    "quality_issues": "compatibility_compatible",
    "informational": "compatibility_compatible",
}


def _line_inventory(
    change_inventory: Mapping[str, int] | None,
    library_results: Sequence[Mapping[str, object]],
    release_global: Mapping[str, int] | None,
) -> dict[str, int] | None:
    """The release's inventory-shaped counts, covering every finding the line states.

    The members' inventory block covers only the members that carry one.
    Two kinds of finding it does not cover are folded into the observed half
    here, as observed compatibility changes:

    * bundle/probe-matrix findings (*release_global*): without them a release
      whose only break was a bundle finding printed "no compatibility changes
      (0 total)" beside a BREAKING verdict (found in review);
    * a ``library_results`` entry with no ``change_inventory`` block, such as
      a release-support-promise entry. :func:`format_stat_line` replaces its
      breakdown with the inventory's values whenever standing hygiene is
      present, so such an entry's findings were otherwise dropped from both
      the parts and the total.

    :func:`format_release_oneline` takes its total from this inventory, so each
    finding folded in here is counted in that total as well.

    The members-only view stays the one the JSON document reports, because
    there a reader has the bundle and matrix sections separately; it is only
    this single line, which has no room to list them, that must account for
    them here.
    """
    if change_inventory is None:
        return None
    merged = dict(change_inventory)
    uncovered: list[Mapping[str, object]] = [
        entry
        for entry in library_results
        if not isinstance(entry.get("change_inventory"), Mapping)
    ]
    if release_global:
        uncovered.append(release_global)
    for source in uncovered:
        for global_key, inventory_key in _GLOBAL_TO_INVENTORY.items():
            merged[inventory_key] = merged.get(inventory_key, 0) + _count(
                source, global_key
            )
        merged["compatibility_changes"] = merged.get("compatibility_changes", 0) + sum(
            _count(source, key) for key in _OUTCOME_KEYS
        )
    return merged


def _count(entry: Mapping[str, object], key: str) -> int:
    value = entry.get(key, 0)
    return value if isinstance(value, int) else 0


def release_global_counts(
    bundle_result: object | None,
    matrix_result: object | None,
) -> dict[str, int]:
    """The bundle/probe-matrix findings' own counts, in the per-library
    vocabulary :func:`format_release_oneline` sums.

    A release-global finding belongs to no library, so it never reaches
    ``library_results`` -- and the folded ``worst_verdict`` includes it. A
    release whose only break is a bundle or matrix finding therefore printed
    ``BREAKING: no changes (0 total)``, omitting the findings responsible for
    the verdict it was announcing (Codex review, PR #1238, P2). The JSON,
    Markdown and JUnit branches all fold these; this closes the fourth.

    Bucketed by each finding's **effective** verdict
    (:func:`~abicheck.policy.reclassify.effective_verdict_for_change`), with
    each source's own ``policy``/``policy_file`` -- the same resolution the
    release's exit code already applies to these exact findings, so a policy
    that reclassifies a bundle kind cannot make the line and the exit
    disagree. Not a second classifier: ``matrix_result`` is a real
    ``DiffResult`` whose own ``policy``/``policy_file`` govern it, and
    ``bundle_result`` carries the pair it was analysed under.
    """
    from ..policy.classification import Verdict
    from ..policy.reclassify import effective_verdict_for_change
    from ..policy.severity import IssueCategory, classify_effective_change

    counts = dict.fromkeys(_OUTCOME_KEYS, 0)

    def _fold(changes: Sequence[Change], policy: object, policy_file: object) -> None:
        policy_name = policy if isinstance(policy, str) else None
        for change in changes:
            verdict = effective_verdict_for_change(
                change,
                policy=policy_name,
                policy_file=policy_file,
            )
            bucket = _VERDICT_BUCKET.get(verdict.name)
            if bucket == "compatible_additions":
                # Same split the per-library entries carry: a COMPATIBLE
                # finding is an addition, a quality issue, or informational.
                category = classify_effective_change(
                    change,
                    policy=policy_name,
                    policy_file=policy_file,
                    verdict=Verdict.COMPATIBLE,
                )
                if category is IssueCategory.QUALITY_ISSUES:
                    bucket = "quality_issues"
                elif category is IssueCategory.INFORMATIONAL:
                    bucket = "informational"
            if bucket is not None:
                counts[bucket] += 1

    findings = getattr(bundle_result, "bundle_findings", None)
    if findings:
        _fold(
            [f.to_change() for f in findings],
            getattr(bundle_result, "policy", None),
            getattr(bundle_result, "policy_file", None),
        )
    matrix_changes = getattr(matrix_result, "changes", None)
    if matrix_changes:
        _fold(
            matrix_changes,
            getattr(matrix_result, "policy", None),
            getattr(matrix_result, "policy_file", None),
        )
    # The total is the sum of the buckets, not a separate count of the
    # findings folded in: the parts and the total come from one place, so a
    # finding that resolves to no bucket cannot inflate one without the other.
    counts["total"] = sum(counts[key] for key in _OUTCOME_KEYS)
    return counts


def format_release_oneline(
    worst_verdict: str,
    library_results: Sequence[Mapping[str, object]],
    *,
    severity_exit_code: int | None = None,
    env_matrix_source_sha256: str | None = None,
    release_global: Mapping[str, int] | None = None,
    change_inventory: Mapping[str, int] | None = None,
) -> str:
    """One line for a whole release.

    *library_results* is the fan-out's own per-library summary list, after
    the ``DiffResult``s have been stripped -- the counts are all this needs.
    An empty list is a real state (a run that completed no comparison) and
    renders as ``0 libraries``; it is never treated as a clean pass, since
    the caller's exit code carries ADR-065's own ``no_comparison_completed``
    contribution independently of what this line says.

    *release_global* is :func:`release_global_counts`' output -- the
    bundle/probe-matrix findings, which belong to no library and so are
    absent from *library_results* even though ``worst_verdict`` folds them.
    Omitting it would let a release whose only break is a bundle finding
    print ``BREAKING: no changes``.

    *change_inventory* is the release fold of the members' own
    change-versus-inventory split
    (``report.release_change_inventory.fold_release_change_inventory``,
    computed by the caller, which owns the operational-sentinel vocabulary).
    Passed through to :func:`format_stat_line` for the identical reason the
    single-pair line takes it: a byte-identical rebuild of a release whose
    members carry 32 pre-existing ``exported_not_public`` exports otherwise
    prints ``NO_CHANGE: 32 risk (32 total)`` here too -- the same verdict-
    versus-count contradiction the scalar line already stopped making.
    ``None`` (the default) renders exactly as this function always did.
    """
    counts = dict.fromkeys(_OUTCOME_KEYS, 0)
    for entry in library_results:
        for key in _OUTCOME_KEYS:
            counts[key] += _count(entry, key)
    if release_global:
        for key in _OUTCOME_KEYS:
            counts[key] += _count(release_global, key)
    line_inventory = _line_inventory(change_inventory, library_results, release_global)
    if line_inventory is not None:
        # The total is the JSON population the inventory partitions: the
        # observed compatibility changes plus every stamped hygiene finding.
        # Summing the outcome buckets instead drops a finding no bucket names
        # (a NOT_EVALUATED one), which then prints as a negative "not evaluated"
        # count or as "0 total" against the JSON's ``total_changes``.
        total = line_inventory["compatibility_changes"] + hygiene_total(line_inventory)
    else:
        # No inventory: the parts are the only account of the findings, so
        # their sum is the total (report.headline_breakdown_drift).
        total = sum(counts.values())
    plural = "library" if len(library_results) == 1 else "libraries"
    gate_note = (
        f"; gate: exit {severity_exit_code}"
        if severity_exit_code is not None and severity_exit_code != 0
        else ""
    )
    deployment_note = (
        f"; deployment floor {env_matrix_source_sha256}"
        if env_matrix_source_sha256
        else ""
    )
    return format_stat_line(
        f"{len(library_results)} {plural} — {worst_verdict}",
        outcomes=counts,
        total_changes=total,
        gate_note=gate_note,
        deployment_note=deployment_note,
        change_inventory=line_inventory,
    )
