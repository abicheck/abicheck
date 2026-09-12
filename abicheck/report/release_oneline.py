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

"""``compare --format oneline`` for a directory/package (release) operand.

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

from typing import TYPE_CHECKING

from .render_text import format_stat_line

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..checker_types import Change

__all__ = ["format_release_oneline", "release_global_counts"]

#: The per-library count keys this fold reads, mapped to the
#: :func:`format_stat_line` argument each contributes to. ``quality_issues``
#: deliberately has no slot: the single-pair line has no "quality" clause
#: either, and inventing one here is exactly the kind of divergence reusing
#: that function is meant to prevent. It still reaches ``total_changes``
#: below, so a release whose only findings are quality issues reads
#: "no changes (3 total)" rather than claiming nothing happened -- the same
#: way the single-pair line does.
_COUNT_KEYS = ("breaking", "source_breaks", "risk_changes", "compatible_additions")


#: A release-global finding's effective verdict -> the oneline bucket it
#: contributes to. ``NO_CHANGE`` contributes to none (it is not a finding's
#: verdict in practice; kept out rather than folded into ``compatible``,
#: which would report it as an observed compatible change).
_VERDICT_BUCKET = {
    "BREAKING": "breaking",
    "API_BREAK": "source_breaks",
    "COMPATIBLE_WITH_RISK": "risk_changes",
    "COMPATIBLE": "compatible_additions",
}


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
    from ..policy.reclassify import effective_verdict_for_change

    counts = {bucket: 0 for bucket in _VERDICT_BUCKET.values()}
    total = 0

    def _fold(changes: Sequence[Change], policy: object, policy_file: object) -> None:
        nonlocal total
        for change in changes:
            total += 1
            verdict = effective_verdict_for_change(
                change,
                policy=policy if isinstance(policy, str) else None,
                policy_file=policy_file,
            )
            bucket = _VERDICT_BUCKET.get(verdict.name)
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
    counts["total"] = total
    return counts


def format_release_oneline(
    worst_verdict: str,
    library_results: Sequence[Mapping[str, object]],
    *,
    severity_exit_code: int | None = None,
    env_matrix_source_sha256: str | None = None,
    release_global: Mapping[str, int] | None = None,
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
    """
    counts = {key: 0 for key in _COUNT_KEYS}
    total = 0
    for entry in library_results:
        for key in _COUNT_KEYS:
            counts[key] += _count(entry, key)
        total += sum(_count(entry, key) for key in _COUNT_KEYS)
        total += _count(entry, "quality_issues")
    if release_global:
        for key in _COUNT_KEYS:
            counts[key] += _count(release_global, key)
        total += _count(release_global, "total")
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
        breaking=counts["breaking"],
        source_breaks=counts["source_breaks"],
        risk_count=counts["risk_changes"],
        compatible_additions=counts["compatible_additions"],
        total_changes=total,
        gate_note=gate_note,
        deployment_note=deployment_note,
    )
