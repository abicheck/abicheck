# Copyright 2026 Nikolay Petrov
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

"""GitHub Actions workflow command annotations for ABI changes.

Emits ``::error``, ``::warning``, and ``::notice`` workflow commands so that
ABI breaking changes appear as inline annotations on PR diffs.

See: https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .checker import (
    Change,
    DiffResult,
)
from .model.change_catalog.kinds import ChangeKind
from .model.contract_finding_relevance import is_evaluated
from .policy.classification import Verdict

if TYPE_CHECKING:
    from datetime import date

    from .finding_identity import MissingContractFinding
    from .policy.severity import IssueCategory, KindSets, SeverityConfig


# GitHub has undocumented limits on annotation message length.
_MAX_MESSAGE_LENGTH = 200

# Severity ordering for sorting (highest first).
_SEVERITY_ORDER = {
    "error": 0,
    "warning": 1,
    "notice": 2,
}

#: The inverse of :data:`_SEVERITY_ORDER` -- recovers the level string from
#: the sort key :func:`_collect_annotations_detailed` already computes, so
#: :func:`annotation_report_entries` doesn't need a second classification
#: pass just to re-derive what level a tuple's ``line`` was rendered at.
_LEVEL_BY_SORT_KEY = {v: k for k, v in _SEVERITY_ORDER.items()}


def _escape_annotation_value(value: str) -> str:
    """Escape special characters for GitHub workflow command values.

    GitHub workflow commands use `%` encoding for special characters
    in property values and messages.
    """
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _escape_annotation_data(data: str) -> str:
    """Escape special characters in the annotation message body.

    The message (data portion after `::`) only needs newline escaping.
    """
    return data.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _truncate_message(message: str) -> str:
    """Truncate message to ``_MAX_MESSAGE_LENGTH``, appending ellipsis if
    truncated."""
    if len(message) <= _MAX_MESSAGE_LENGTH:
        return message
    return message[: _MAX_MESSAGE_LENGTH - 3] + "..."


def _parse_source_location(loc: str | None) -> tuple[str | None, str | None]:
    """Parse a ``file:line[:col]`` source location string.

    Accepts ``path:line`` or ``path:line:col`` (column is discarded).
    Returns (file, line) where either may be ``None``.
    """
    if not loc:
        return None, None
    # Split into segments on ":"
    # Handle Windows drive letters (e.g. C:\foo\bar.h:42) by preserving
    # a single-character first segment followed by a backslash/path.
    parts = loc.split(":")
    if len(parts) >= 3 and len(parts[0]) == 1 and parts[0].isalpha():
        # Windows path: rejoin drive letter with the next segment.
        # e.g. ["C", "\\foo\\bar.h", "42"] -> file="C:\\foo\\bar.h", rest=["42"]
        # or   ["C", "\\foo\\bar.h", "42", "7"] -> file="C:\\foo\\bar.h", rest=["42","7"]
        file_part = parts[0] + ":" + parts[1]
        rest = parts[2:]
    elif len(parts) >= 2:
        # Unix path: everything before the last numeric segments is the file.
        # e.g. ["include/foo.h", "42"] or ["include/foo.h", "42", "7"]
        file_part = parts[0]
        rest = parts[1:]
    else:
        # No colon at all — just a filename.
        return loc, None

    # First element of rest should be the line number.
    if rest and rest[0].isdigit():
        return file_part, rest[0]
    # No valid line number found.
    return loc, None


def _category_for_change_severity(
    change: Change,
    kind_sets: KindSets,
    *,
    policy: str | None = None,
    policy_file: object | None = None,
    today: date | None = None,
) -> IssueCategory:
    """Return the severity-aware :class:`~abicheck.policy.severity.IssueCategory` for *change*.

    *policy_file* must be threaded through (not just the pre-baked
    *kind_sets*) so a frozen-namespace-tagged finding's floor is honoured the
    same way ``severity.compute_exit_code`` honours it for the actual exit
    code: ``kind_sets`` alone already moves a policy-overridden *kind* to its
    new bucket, but the *per-change* frozen-namespace clamp-back (never let an
    override downgrade a frozen-namespace violation below its raw verdict)
    only fires when ``policy_file`` itself is passed to
    ``classify_effective_change`` — omitting it would let this annotation
    under-report a finding that still fails CI at its raw severity. *today*,
    forwarded there too, keeps this agreeing with an already-frozen
    ``ReportEnvelope`` (Codex review, fresh evidence).
    """
    from .policy.severity import classify_effective_change

    return classify_effective_change(
        change,
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
        today=today,
    )


def _legacy_level_for_category(
    category: IssueCategory,
    annotate_additions: bool,
) -> str | None:
    """Fixed-mapping annotation level, keyed off the *effective* category.

    The pre-severity-config behaviour (BREAKING -> error, API_BREAK/RISK ->
    warning, COMPATIBLE -> notice iff *annotate_additions*), driven by each
    change's effective verdict (via *category*) rather than raw kind-set
    membership — so a per-finding override (frozen-namespace clamp, A4
    pattern-verdict modulation) is respected even when no SeverityConfig is
    in play.
    """
    from .policy.severity import IssueCategory as _IssueCategory

    if category == _IssueCategory.ABI_BREAKING:
        return "error"
    if category == _IssueCategory.POTENTIAL_BREAKING:
        return "warning"
    if annotate_additions and category in (
        _IssueCategory.ADDITION,
        _IssueCategory.QUALITY_ISSUES,
    ):
        return "notice"
    return None


def _annotation_level_for_category(
    category: IssueCategory,
    severity_config: SeverityConfig,
    annotate_additions: bool,
) -> str | None:
    """Return the annotation level for *category* under *severity_config*, or None to skip.

    Reflects the actual CI gate (ADR "GateDecision" direction): a category
    configured ``error`` always emits ``::error`` regardless of whether it is
    an addition or a breaking kind, so an annotation is never silently absent
    for a finding that will fail the build. ``warning``/``info`` mirror the
    severity level directly; ``info`` only surfaces (as ``::notice``) when
    *annotate_additions* opts into the noisier informational annotations —
    matching the pre-existing opt-in behaviour for additions.
    """
    from .policy.severity import SeverityLevel

    level = severity_config.level_for(category)
    if level == SeverityLevel.ERROR:
        return "error"
    if level == SeverityLevel.WARNING:
        return "warning"
    if level == SeverityLevel.INFO:
        return "notice" if annotate_additions else None
    return None


def _title_for_change(
    kind: ChangeKind,
    breaking_set: frozenset[ChangeKind],
    api_break_set: frozenset[ChangeKind],
    risk_set: frozenset[ChangeKind],
    compatible_set: frozenset[ChangeKind],
    *,
    category: IssueCategory | None = None,
    effective_verdict: Verdict | None = None,
) -> str:
    """Return the annotation title prefix, distinguishing API Break from Deployment Risk.

    *category*, when given (the severity-aware path), is consulted *before*
    kind-set membership (CodeRabbit review on #549). ``breaking_set``/etc.
    are pre-baked from ``DiffResult._effective_kind_sets()``, which already
    applies *kind-level* policy-file overrides — so a policy-demoted
    ``FUNC_REMOVED`` reads as being "in" ``compatible_set``. But a
    *per-change* frozen-namespace clamp can keep that one finding's
    *effective* category at ``ABI_BREAKING`` despite the kind-level move; a
    title picked from kind-set membership alone would then mislabel an
    ``::error`` annotation as "Quality Issue" (or "ABI Addition"). Checking
    *category* first — the same effective classification that already drove
    the annotation *level* — keeps the title consistent with it.

    *effective_verdict*, when given, resolves the POTENTIAL_BREAKING API
    Break vs. Deployment Risk split instead of raw kind-set membership: a
    per-change ``effective_verdict`` override/modulation (A4 pattern-verdict,
    PolicyFile) can move a finding between the two without changing which
    kind-set its raw *kind* belongs to, so kind-set membership alone could
    label the title with the wrong subtype (CodeRabbit review, PR #557).
    """
    kind_label = kind.value
    if category is not None:
        from .policy.severity import IssueCategory as _IssueCategory

        if category == _IssueCategory.ABI_BREAKING:
            return f"ABI Break: {kind_label}"
        if category == _IssueCategory.QUALITY_ISSUES:
            return f"Quality Issue: {kind_label}"
        if category == _IssueCategory.ADDITION:
            return f"ABI Addition: {kind_label}"
        # POTENTIAL_BREAKING: distinguish API Break from Deployment Risk via
        # the finding's actual effective verdict when available (falling
        # back to the kind sets only if it wasn't supplied), since
        # IssueCategory does not itself split the two.
        if effective_verdict is not None:
            if effective_verdict == Verdict.API_BREAK:
                return f"API Break: {kind_label}"
            if effective_verdict == Verdict.COMPATIBLE_WITH_RISK:
                return f"Deployment Risk: {kind_label}"
            return f"Potential Break: {kind_label}"
        if kind in api_break_set:
            return f"API Break: {kind_label}"
        if kind in risk_set:
            return f"Deployment Risk: {kind_label}"
        return f"Potential Break: {kind_label}"

    if kind in breaking_set:
        return f"ABI Break: {kind_label}"
    if kind in api_break_set:
        return f"API Break: {kind_label}"
    if kind in risk_set:
        return f"Deployment Risk: {kind_label}"
    if kind in compatible_set:
        return f"ABI Addition: {kind_label}"
    return f"ABI Change: {kind_label}"


def _format_annotation(
    level: str,
    change: Change | MissingContractFinding,
    title: str,
    message: str,
) -> str:
    """Format a single GitHub workflow command annotation line.

    Accepts a ``MissingContractFinding`` too (a missing ``--used-by``/
    ``--required-symbol`` label has no backing ``Change``) -- only
    ``source_location`` is read, which both share.
    """
    file, line = _parse_source_location(change.source_location)

    props: list[str] = []
    if file:
        props.append(f"file={_escape_annotation_value(file)}")
    if line:
        props.append(f"line={_escape_annotation_value(line)}")
    props.append(f"title={_escape_annotation_value(title)}")

    props_str = ",".join(props)
    escaped_message = _escape_annotation_data(_truncate_message(message))
    return f"::{level} {props_str}::{escaped_message}"


def _collect_annotations_detailed(
    diff_result: DiffResult,
    *,
    annotate_additions: bool = False,
    severity_config: SeverityConfig | None = None,
    today: date | None = None,
) -> list[tuple[int, str, bool]]:
    """Collect (sort_key, line, always_visible) triples for a DiffResult.

    *always_visible* is True for an entry ``--annotate`` alone (without
    ``--annotate-additions``) would already show — every ``error``/
    ``warning`` level, plus the one unconditional ``notice`` this function
    emits regardless of *annotate_additions* (the not-evaluated-under-
    contract demotion below). It is False for a ``notice`` that exists only
    because *annotate_additions* was truthy (an addition/quality-issue, or
    an ``info``-level severity-config category) — the caller had to opt in
    for this entry to appear at all. :func:`annotation_report_entries` is
    the one consumer, and persists the third element because a report has
    no live ``annotate_additions`` flag to fall back on (Codex review on
    PR E: without this, a renderer that
    drops every ``"notice"`` unless ``annotate-additions`` was requested
    would also hide a contract audit finding the CLI itself always shows).

    Without *severity_config*, levels follow the fixed kind-set mapping
    (:func:`_legacy_level_for_category`); with it, each level mirrors the
    finding's configured severity (:func:`_annotation_level_for_category`).
    Both classify through the change's *effective* category, so a
    per-finding override is never misreported.

    Also walks ``diff_result.scoped_only_changes`` (Codex review on PR E),
    the dynamically-attached findings ``--used-by``/``--required-symbol``
    scoping synthesizes (e.g. ``PE_ORDINAL_RETARGETED``) that never make it
    into ``diff_result.changes`` itself — the same
    ``list(result.changes) + list(getattr(result, "scoped_only_changes",
    ()) or ())`` convention every other consumer of the full finding set
    already applies (``_fold_scoped_compat_into_text``,
    ``_attach_suppression_audit``, ``junit_report.py``). Without this, a
    comparison whose *sole* gating finding is scope-synthesized could exit
    non-zero with that finding present in the rendered report's
    ``changes`` but silently absent from both the stderr annotation and
    the persisted ``annotations`` array.

    ``diff_result.scoped_missing_labels`` — the sibling synthesized-finding
    list for a label ``--used-by``/``--required-symbol`` required but the
    new library lacks entirely — is folded in too (Codex review follow-up,
    fresh evidence), through a separate branch after the main loop rather
    than as an extension of the fold above: a missing-label entry has no
    backing ``Change``/``ChangeKind`` at all
    (``finding_identity.missing_contract_finding`` converts it to a
    ``MissingContractFinding``, a different shape this loop's
    ``ChangeKind``-based classification — ``_effective_kind_sets``,
    ``_category_for_change_severity``, ``is_evaluated`` — cannot consume
    directly), so it is classified the same way every other consumer of
    ``scoped_missing_labels`` already does
    (``sarif._missing_contract_result``, ``junit_report.py``,
    ``reporter_markdown.py``): unconditionally a hard block under the
    legacy scheme, or gated on ``severity.missing_contract_exit_code``
    under a severity scheme.
    """
    from .policy.severity import effective_verdict_for_change

    kind_sets = diff_result._effective_kind_sets()
    breaking_set, api_break_set, compatible_set, risk_set = kind_sets

    annotations: list[tuple[int, str, bool]] = []

    all_changes = list(diff_result.changes) + list(
        getattr(diff_result, "scoped_only_changes", ()) or ()
    )
    for change in all_changes:
        # ADR-049 D1: an annotation states how a finding gated, and a
        # NOT_EVALUATED finding did not -- compatibility policy never scored
        # it. Emitting `::error` for one put a red GitHub annotation on a
        # comparison whose verdict is NO_CHANGE and whose compatibility gate
        # is clean (Codex review, reproduced with a proven-out-of-contract
        # type-size change). Demoted to `::notice` rather than dropped: the
        # fact stays surfaced in the workflow log, it just stops claiming to
        # be a break. Only reachable under `--contract`. Always visible --
        # unlike every other notice below, this one does not need
        # *annotate_additions*.
        if not is_evaluated(change):
            annotations.append(
                (
                    _SEVERITY_ORDER.get("notice", 99),
                    _format_annotation(
                        "notice",
                        change,
                        f"Not evaluated (contract): {change.kind.value}",
                        change.description,
                    ),
                    True,
                )
            )
            continue
        category = _category_for_change_severity(
            change,
            kind_sets,
            policy=diff_result.policy,
            policy_file=diff_result.policy_file,
            today=today,
        )
        if severity_config is not None:
            level = _annotation_level_for_category(
                category,
                severity_config,
                annotate_additions,
            )
        else:
            level = _legacy_level_for_category(category, annotate_additions)
        if level is None:
            continue

        title = _title_for_change(
            change.kind,
            breaking_set,
            api_break_set,
            risk_set,
            compatible_set,
            category=category,
            effective_verdict=effective_verdict_for_change(
                change,
                policy=diff_result.policy,
                kind_sets=kind_sets,
                policy_file=diff_result.policy_file,
                today=today,
            ),
        )
        line = _format_annotation(level, change, title, change.description)
        sort_key = _SEVERITY_ORDER.get(level, 99)
        # Every non-notice level (error/warning) is always visible; the only
        # way this loop reaches "notice" here (as opposed to the always-
        # visible not-evaluated branch above) is that annotate_additions
        # opted it in.
        annotations.append((sort_key, line, level != "notice"))

    # A missing --used-by/--required-symbol contract member (a label the new
    # library lacks *entirely* -- not a Change, since there is nothing to
    # diff against) has no backing ChangeKind at all, so it cannot go
    # through the ordinary per-change loop above -- it needs the same
    # dedicated branch every other consumer of `scoped_missing_labels`
    # already has (`sarif._missing_contract_result`, `junit_report.py`,
    # `reporter_markdown.py`). Without this, a comparison whose *sole*
    # gating finding is a missing label could exit non-zero with nothing in
    # either the stderr annotation stream or the persisted `annotations`
    # array to explain why (Codex review, fresh evidence -- this closes the
    # sibling gap the `scoped_only_changes` fold above already closed for a
    # *present* scope-synthesized finding).
    gate_scope = getattr(diff_result, "gate_scope", None)
    if gate_scope is not None:
        from .finding_identity import missing_contract_finding, missing_contract_kind
        from .policy.severity import SeverityLevel

        # A missing contract member is the same failure class as
        # `abi_breaking` (see `severity.missing_contract_exit_code`'s own
        # docstring), so it is classified by that category's *configured
        # level* directly -- not collapsed to a bare blocks/doesn't-block
        # binary. A first revision of this did exactly that collapse,
        # which silently demoted a `severity.abi_breaking: warning`
        # configuration's ::warning (always visible, matching how every
        # ordinary warning-level finding is treated elsewhere in this same
        # function) down to an opt-in ::notice a plain `annotate: true`
        # Action run would never show at all (Codex review, fresh
        # evidence). Under the legacy scheme (no severity_config) it's
        # unconditionally a hard block, matching
        # sarif._missing_contract_result's identical "unconditionally
        # BREAKING" legacy-scheme behaviour.
        if severity_config is None:
            missing_level: str | None = "error"
        elif severity_config.abi_breaking == SeverityLevel.ERROR:
            missing_level = "error"
        elif severity_config.abi_breaking == SeverityLevel.WARNING:
            missing_level = "warning"
        else:
            missing_level = "notice" if annotate_additions else None
        if missing_level is not None:
            kind = missing_contract_kind(gate_scope)
            title = (
                f"ABI Break: {kind}"
                if missing_level == "error"
                else f"ABI {missing_level.capitalize()}: {kind}"
            )
            for label in getattr(diff_result, "scoped_missing_labels", ()) or ():
                finding = missing_contract_finding(kind, label)
                line = _format_annotation(
                    missing_level,
                    finding,
                    title,
                    finding.description,
                )
                annotations.append(
                    (
                        _SEVERITY_ORDER.get(missing_level, 99),
                        line,
                        missing_level != "notice",
                    )
                )

    return annotations


def annotation_report_entries(
    diff_result: DiffResult,
    *,
    severity_config: SeverityConfig | None = None,
    today: date | None = None,
) -> list[dict[str, object]]:
    """Structured, persistable annotation entries for a JSON report.

    CLI cleanup phase two, PR E (persistence prerequisite): the plan's own
    "New invariant" is that a rendering front end (the composite Action)
    must never infer a finding's annotation from stderr or a re-run
    comparison -- it has to read a persisted, already-classified answer the
    way it already does for ``exit``/``analysis_assurance``. This is that
    answer for annotations specifically.

    It always computes the *superset* -- ``annotate_additions=True`` --
    through the one classification and formatting path
    (:func:`_collect_annotations_detailed`/:func:`_format_annotation`:
    escaping, truncation, title selection). A consumer decides at *read*
    time whether to include a ``"notice"``-level entry (the
    ``annotate-additions`` question), the same way it already decides
    whether to render at all (the ``annotate`` question) -- this function
    answers a different, prior question: what a full annotation pass over
    this comparison found, not what any one caller asked to see.

    Each entry is ``{"level": "error"|"warning"|"notice", "annotation": ...,
    "always_visible": bool}``. ``annotation`` is the exact, already-escaped/
    truncated GitHub workflow-command line :func:`_format_annotation`
    produces, so a renderer can echo it verbatim rather than reassembling
    ``file``/``line``/``title``/``message`` itself. ``always_visible`` is
    what a renderer must actually gate a ``"notice"`` entry on instead of
    the level alone (Codex review on PR E): one class of notice -- a
    ``--contract`` finding compatibility policy never evaluated -- is
    surfaced by plain ``--annotate`` with no ``--annotate-additions`` at
    all, so filtering every ``"notice"`` out by default would silently hide
    it. ``always_visible`` is always True for ``error``/``warning``.
    """
    detailed = _collect_annotations_detailed(
        diff_result,
        annotate_additions=True,
        severity_config=severity_config,
        today=today,
    )
    return [
        {
            "level": _LEVEL_BY_SORT_KEY.get(sort_key, "notice"),
            "annotation": line,
            "always_visible": always_visible,
        }
        for sort_key, line, always_visible in sorted(detailed, key=lambda item: item[0])
    ]
