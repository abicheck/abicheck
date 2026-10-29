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

"""The release report's per-library finding projection.

Every renderer of a release summary reads the same projection of each
member's :class:`~abicheck.checker.DiffResult`: the capped ``findings`` list
(:func:`release_finding_dicts`), the ``show=`` view, the impact table, the
uncapped ``annotations``, and the verdict that folds the proven-removed-member
floor (:func:`project_release_library_results`). It used to live in the
release CLI's render module, so a workflow-level caller could not reach it
without importing a ``cli_*`` file. It is report-owned (ADR-061: a report
field or its projection belongs to ``report/``) and ``report`` may import
``workflows``, so the verdict-fold gate helpers come from
:mod:`abicheck.workflows.gate` directly -- no function-local import.

:func:`project_release_library_results` is the last point each member's live
``DiffResult`` exists: it stashes what the renderers need under private
``_``-prefixed keys, then strips every such key so the entries serialize
straight to JSON.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, cast

from ..annotations import annotation_report_entries
from ..checker import Change, DiffResult
from ..workflows.gate import (
    apply_removed_member_floor,
    categorize_changes,
    effective_kind_sets,
    evaluate,
)
from .diff_report import release_finding_entry
from .markdown_sections import compute_impact_table
from .release_display_limits import MAX_RELEASE_FINDINGS_PER_LIBRARY
from .view import apply_show_only

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..model.change_catalog.kinds import HasKind
    from ..workflows.gate import SeverityConfig

__all__ = [
    "MAX_RELEASE_FINDINGS_PER_LIBRARY",
    "project_release_library_results",
    "release_display_buckets",
    "release_finding_dicts",
    "strip_internal_keys",
]


def _change_kind_str(c: Any) -> str:
    """The same tolerant ``kind`` read ``release_finding_entry`` uses, standalone.

    Mirrored the retired ``cli_scan_baseline._change_kind_str`` -- kept local rather than
    imported since the two ``cli_*`` command families are independently
    owned and this is a five-line, dependency-free primitive. Shared so the
    per-kind truncation ledger below counts a raw ``Change`` the identical
    way the finding dicts spell its ``kind`` -- a mismatch here would make
    the ledger's keys disagree with the ``kind`` values in
    ``entry["findings"]`` itself.
    """
    kind = getattr(c, "kind", None)
    return str(getattr(kind, "value", str(kind)))


def _as_changes(changes: Sequence[HasKind]) -> list[Change]:
    """Narrow what :func:`categorize_changes` returns back to ``Change``.

    That function types its input as the structural ``HasKind`` protocol, so
    its outputs are typed ``list[HasKind]``; it returns the very ``Change``
    objects it was given (``diff.changes``), so the narrowing is the caller's
    knowledge, not a new value.
    """
    return cast("list[Change]", list(changes))


def release_display_buckets(
    diff: DiffResult,
    severity_config: SeverityConfig | None,
) -> list[tuple[str, list[Change]]]:
    """Return every (bucket, changes) group in *diff*, for display purposes.

    Codex review (PR #1154 follow-up, "Filter the complete release finding
    set"): :func:`_release_gating_buckets` (removed) deliberately narrows to only the
    categories that can gate the release's own exit code -- under the
    legacy scheme that's breaking/api_break/risk, and under a severity
    scheme it's further narrowed to whichever categories
    ``gate_decision_for_result`` says are actually *blocking*. That
    restriction is correct for computing the exit code, but the release
    fan-out's own `findings`/`findings_view` display pool used the same
    restricted buckets -- so a `func_added`/other COMPATIBLE finding could
    never appear in a directory/package release's own findings list (nor
    be reachable via `--view show=compatible`/`show=added`), even though
    the identical single-pair `compare` for that same library displays it.
    This function is the unrestricted counterpart: every category, always,
    regardless of what's blocking -- the same "full diff, not the gate's
    own subset" pool a single-pair `compare` report renders from. Gate
    bucket restriction stays reserved for the former :func:`_release_gating_buckets`'s
    own exit-code-facing callers.
    """
    if severity_config is not None:
        kind_sets = effective_kind_sets(diff)
        categorized = categorize_changes(
            diff.changes,
            policy=diff.policy,
            kind_sets=kind_sets,
            policy_file=diff.policy_file,
        )
        # Dict + name-lookup (matching the former `_release_gating_buckets`'s own
        # shape above), not a literal list of tuples -- `categorize_changes`
        # types each field as `list[HasKind]`, and building the return list
        # this way is what keeps mypy happy the same way it already is above.
        cat_changes_by_name = {
            "abi_breaking": _as_changes(categorized.abi_breaking),
            "potential_breaking": _as_changes(categorized.potential_breaking),
            "quality_issues": _as_changes(categorized.quality_issues),
            "addition": _as_changes(categorized.addition),
            # ADR-078: never gates, but a finding is never dropped from the
            # release listing for that.
            "informational": _as_changes(categorized.informational),
        }
        return [(name, changes) for name, changes in cat_changes_by_name.items()]
    classified = evaluate(diff)
    return [
        ("breaking", classified.breaking),
        ("api_break", classified.source_breaks),
        ("risk", classified.risk),
        ("compatible", classified.compatible),
        # Codex review, fresh evidence ("Preserve not-evaluated findings in
        # release summaries"): `breaking`/`source_breaks`/`risk`/`compatible`
        # all route through `evaluated_changes()`, which -- under
        # `--contract` -- excludes a finding contract evaluation left
        # NOT_EVALUATED (unknown/unproven/proven-out-of-contract relevance).
        # Without this bucket, the severity_config-is-None branch above (the
        # legacy, non-severity-aware release path) is the one place such a
        # finding disappears entirely from `findings`/`--view show=...`: the
        # severity_config branch above categorizes `diff.changes` directly
        # (kind-based, relevance-blind), so it never drops them in the first
        # place, and the equivalent scalar `compare` JSON/per-library
        # `--output-dir` report serialize `diff.changes` too. `diff.
        # not_evaluated` is the same disclosure the single-pair renderer's
        # own dedicated not-evaluated section gives these findings -- listed
        # under their own bucket, never silently merged into "compatible"
        # (which would misrepresent an unproven/unresolved relevance as an
        # actual compatibility verdict).
        ("not_evaluated", diff.not_evaluated),
    ]


def release_finding_dicts(
    diff: DiffResult,
    severity_config: SeverityConfig | None = None,
    show_only: str | None = None,
    *,
    uncapped: bool = False,
) -> tuple[list[dict[str, object]], list[str]]:
    """Project a library's gating findings into small, capped dicts.

    *uncapped* builds the **complete** projection instead, and returns no
    cut kinds -- nothing was cut. That is what every machine document gets
    (plan slice 7m: complete machine data is never truncated): a truncated
    JSON/JUnit document nobody asked to truncate is not a summary, it is a
    lossy result, and this release schema has no per-library findings array
    elsewhere for a consumer to fall back to.

    Same shape as the retired ``cli_scan_baseline._baseline_finding_dicts`` /
    ``stack_report._stack_finding_dicts``. Counts (not already-built dicts)
    decide the cap so a large diff never builds more dicts than the cap can
    ever keep. See :func:`release_display_buckets` for which findings this
    walks -- the full diff (all categories), not the narrower
    :func:`_release_gating_buckets` (removed) subset that only ever gates the exit
    code (Codex review, PR #1154 follow-up: "Filter the complete release
    finding set" -- a compatible addition must be reachable here the same
    way it is in a single-pair `compare` report).

    *show_only* (Codex review, PR #1154 follow-up: `compare --view
    show=...` on a directory/package input) filters each display bucket the
    same way a single-pair `compare`'s own report filters `result.changes`
    (`report.view.apply_show_only`, resolved against this library's
    own effective kind sets/policy file so it never disagrees with the
    per-finding severity a single-pair report for the same library would
    show) -- applied before the cap, so a filtered-out finding never
    occupies one of the cap's slots a displayed one needed.

    Each dict also carries ``reclassified_by`` (schema 2.31) when a
    ``reclassify:`` rule decided the change's effective verdict, via
    :func:`abicheck.report.diff_report.release_finding_entry` -- the identical
    resolution `compare`'s ``changes[]``/``scan --against`` use, so the
    three can't drift on which rule fired for a shared finding.

    Returns ``(dicts, cut_kinds)`` -- ``cut_kinds`` is every kind string cut
    from *any* bucket (not just the one that first hit the cap), so a
    caller can accumulate an exact kind -> count truncation ledger the same
    way the retired ``cli_scan_baseline._baseline_summary`` did, instead of a bare
    ``findings_truncated`` boolean that hides the shape of what was cut.
    """
    cap = None if uncapped else MAX_RELEASE_FINDINGS_PER_LIBRARY
    findings: list[dict[str, object]] = []
    cut_kinds: list[str] = []
    for bucket_name, bucket_changes in release_display_buckets(diff, severity_config):
        if show_only:
            bucket_changes = apply_show_only(
                bucket_changes,
                show_only,
                policy=diff.policy or "strict_abi",
                kind_sets=effective_kind_sets(diff),
                policy_file=diff.policy_file,
            )
        if cap is None:
            included, excluded = list(bucket_changes), []
        else:
            remaining = max(0, cap - len(findings))
            included, excluded = bucket_changes[:remaining], bucket_changes[remaining:]
        for c in included:
            findings.append(release_finding_entry(c, bucket_name, diff.policy_file))
        # Keep tallying excluded kinds across every remaining bucket (not
        # just the one that first hit the cap) -- see this function's own
        # "Returns" note above.
        cut_kinds.extend(_change_kind_str(c) for c in excluded)
    return findings, cut_kinds


def strip_internal_keys(entry: dict[str, object]) -> None:
    """Remove every pass-internal key from one rendered member entry.

    By *prefix*, not by a hand-maintained list of names. The list was the
    defect: a member entry is serialized straight to JSON, so a stash key
    nobody remembered to add became ``TypeError: Object of type X is not
    JSON serializable`` at the very end of a release run -- after every
    comparison had been paid for. `_old_junit_inventory` did exactly that
    to a real six-member run, and the same trap was waiting for the next
    key anyone stashed. A leading underscore is this fan-out's own
    convention for "internal to the pass, never rendered" (all ten such
    keys follow it), so the prefix is the rule the list was approximating.

    Its own function rather than a loop inside
    :func:`project_release_library_results`: that function is
    already rank D, and inlining this pushed it from complexity 21 to 24.
    Naming the rule keeps it testable on its own
    (``tests/test_release_entry_internal_key_strip.py``) and leaves the
    caller no more complex than it was.
    """
    for key in [k for k in entry if k.startswith("_")]:
        del entry[key]


def project_release_library_results(
    library_results: list[dict[str, object]],
    removed_keys: list[str],
    worst_verdict: str,
    severity_config: SeverityConfig | None = None,
    *,
    needs_annotations: bool = True,
    show_only: str | None = None,
    show_impact: bool = False,
) -> str:
    """Remove un-serialisable ``_diff_result`` entries and adjust the worst verdict.

    Before the stashed :class:`DiffResult` objects are discarded, each
    library entry gets a capped ``findings`` list (kind/symbol/description/
    location) projected from it — otherwise the JSON summary is entirely
    count-centric (``"breaking": 3``) with no way to identify which symbols
    broke short of a separate `compare` run or the optional per-library
    ``--output-dir`` report file. *severity_config*, when the release's exit
    code is severity-aware, is forwarded to :func:`release_finding_dicts` so
    a library gated by a promoted addition/quality-issue category (not one of
    the legacy breaking/api_break/risk buckets) still gets a matching
    finding, not an empty list next to a nonzero ``severity.exit_code``.
    Stripping the private keys (``_diff_result`` and friends) keeps the
    summary formatter free of Python-only objects. If any library was
    *removed*, the verdict is bumped to at least ``COMPATIBLE_WITH_RISK``.

    *needs_annotations* gates whether the uncapped ``annotations`` array
    (unlike ``findings`` above, deliberately unbounded -- see its own
    comment) is built at all (Codex review, fresh evidence): only a JSON
    render (primary ``-o json=...`` or a secondary ``--write
    json=...``) ever reads it, but every entry in ``library_results`` is
    held until the whole release finishes, so building it unconditionally
    grew peak memory by every library's full finding set even for a
    markdown/JUnit-only render that never reads it.

    *show_only* (Codex review, PR #1154 second follow-up: "Apply release
    show filters inside each renderer"): this is the **last** point a real
    :class:`DiffResult` (with its own ``policy``/``policy_file``/
    ``effective_verdict`` overrides) is available for a library, so it is
    where a ``show_only``-filtered *view* of the findings is computed --
    but ``entry["findings"]`` itself always stays the **full**, unfiltered
    projection, exactly as it would with no ``--view show=`` in effect. The
    filtered view is stashed under the private ``findings_view``/
    ``findings_view_truncated`` keys, consumed (and stripped) by
    :func:`abicheck.report.release_document.release_findings_for_render`
    only for whichever renderer is the *primary* (``--format``) one -- a
    secondary ``-o`` report is documented/contracted to always be full
    (mirroring single-pair ``compare``'s own ``-o`` behaviour), so it
    must never see this filtered view. Computing the view here rather than
    re-filtering ``entry["findings"]`` downstream is deliberate: the
    severity dimension of ``--view show=`` resolves through
    ``effective_verdict_for_change`` (frozen-namespace/reclassify/policy-
    file overrides), which needs the live ``Change`` objects this function
    is the last place to see before they are discarded for memory.

    *show_impact* (Codex review, PR #1154 follow-up: "Reject unsupported
    impact views instead of silently dropping them" -- ``compare --view
    impact`` on a directory/package input) computes each library's own
    impact-summary table (:func:`abicheck.report.markdown_sections.
    compute_impact_table`, the identical helper a single-pair `compare`
    report's own impact table uses) from the same full display pool
    :func:`release_display_buckets` builds, stashed as ``entry[
    "impact_table"]`` (``None``-valued tables are simply omitted, matching
    how ``entry["findings"]`` is only set when non-empty). When *show_only*
    is also active, a second, filtered ``entry["impact_table_view"]`` is
    computed the same way :func:`release_finding_dicts`'s ``findings_view``
    is -- consumed and stripped by
    :func:`abicheck.report.release_document.release_findings_for_render`
    for whichever renderer is primary, never for a secondary ``-o``.

    ``entry["findings"]``/``entry["findings_view"]`` are always the
    **complete** projections (plan slice 7m -- there is no longer any way to
    ask for a truncated machine document, so ``uncapped`` below is
    unconditional). The presentation cap is applied at Markdown render time
    instead (``release_md_library_findings``), which is also where the
    ``findings_truncated_kinds``/``findings_view_truncated_kinds`` (kind ->
    count cut) disclosure of what a human summary omitted is produced.

    Returns the (possibly updated) *worst_verdict* string.
    """
    # `entry["findings"]` feeds every renderer, and only the human ones want
    # a cap -- so it is always complete here and `_release_md_library_
    # findings` applies the presentation cap at render time instead. Plan
    # slice 7m: with `--max-findings-per-library` and its env var retired,
    # "this run asked for truncation" is no longer expressible, and the
    # branch that used to answer it is gone rather than left permanently
    # false.
    uncapped = True
    for entry in library_results:
        if not isinstance(entry, dict):
            continue
        diff = entry.get("_diff_result")
        if isinstance(diff, DiffResult):
            display_buckets = release_display_buckets(diff, severity_config)
            total_findings = sum(len(cat_changes) for _, cat_changes in display_buckets)
            findings, _cut_kinds = release_finding_dicts(
                diff, severity_config, None, uncapped=uncapped
            )
            if findings:
                entry["findings"] = findings
            # Codex review, fresh evidence ("Count uncapped findings in
            # release filter totals"): the uncapped pool size behind
            # `findings` above -- `len(entry["findings"])` alone
            # under-reports once a library crosses the resolved cap (e.g. 25
            # findings reports as 10 under the default). This is the one
            # place the real, uncapped
            # `total_findings` is still available before `diff` is discarded
            # below; a private, `findings_view`-shaped key (popped by
            # `release_findings_for_render`, never rendered) so
            # `release_json_document`'s `filtered_summary`-equivalent totals
            # can read the true pre-filter count instead of re-deriving it
            # from the already-capped display list.
            entry["findings_total_count"] = total_findings
            if show_only:
                # The filtered *view*, alongside (never instead of) the full
                # projection above -- see this function's own docstring.
                total_findings_view = sum(
                    len(
                        apply_show_only(
                            cat_changes,
                            show_only,
                            policy=diff.policy or "strict_abi",
                            kind_sets=effective_kind_sets(diff),
                            policy_file=diff.policy_file,
                        )
                    )
                    for _, cat_changes in display_buckets
                )
                findings_view, _cut_kinds_view = release_finding_dicts(
                    diff, severity_config, show_only, uncapped=uncapped
                )
                entry["findings_view"] = findings_view
                # The filtered counterpart of `findings_total_count` above --
                # same rationale, same private/popped contract.
                entry["findings_total_count_view"] = total_findings_view
            # CLI cleanup phase two, PR E: the uncapped, always-classified
            # counterpart to the capped `findings` list above -- the exact
            # same shape single-library `compare -o json=...` persists at
            # its own top-level `annotations` (schema 2.43,
            # `annotations.annotation_report_entries`), reused verbatim so
            # the two can never disagree. This is what lets the Action's own
            # renderer (`action/run.sh`'s `_emit_annotations`) read a
            # release-style operand's report the same way it reads a
            # single-library one, instead of needing an independent
            # per-library re-run this module used to perform
            # (`_collect_release_extras`, since removed) just to recover the
            # same DiffResult already sitting right here.
            if show_impact:
                full_changes = [
                    c for _, cat_changes in display_buckets for c in cat_changes
                ]
                impact_table = compute_impact_table(diff, full_changes)
                if impact_table is not None:
                    entry["impact_table"] = dataclasses.asdict(impact_table)
                if show_only:
                    filtered_changes = [
                        c
                        for _, cat_changes in display_buckets
                        for c in apply_show_only(
                            cat_changes,
                            show_only,
                            policy=diff.policy or "strict_abi",
                            kind_sets=effective_kind_sets(diff),
                            policy_file=diff.policy_file,
                        )
                    ]
                    impact_table_view = compute_impact_table(diff, filtered_changes)
                    # Unlike `entry["impact_table"]` above (only set when
                    # non-None), this key is *always* set whenever
                    # `show_only` is active -- even to `None` -- so
                    # `release_findings_for_render`'s swap (mirroring
                    # `findings_view`'s own always-set contract) can tell
                    # "no view was computed" apart from "the view is empty".
                    entry["impact_table_view"] = (
                        dataclasses.asdict(impact_table_view)
                        if impact_table_view is not None
                        else None
                    )
            if needs_annotations:
                entry["annotations"] = annotation_report_entries(
                    diff, severity_config=severity_config
                )
        strip_internal_keys(entry)
    return apply_removed_member_floor(worst_verdict, removed=bool(removed_keys))
