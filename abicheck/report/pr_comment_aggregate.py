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

"""PR-comment adapter for the ``aggregate`` fan-in document.

``abicheck aggregate`` folds one commit's per-target reports into a single
gate document (``report/aggregate.py``'s ``render_aggregate_json``,
discriminated by its ``aggregate_schema_version`` key). That document is a
*reconciliation* -- per-target verdicts, the gate decision, and four
orthogonal exit-floor axes -- and deliberately carries no ``changes`` array
of its own.

Before this module existed, :func:`abicheck.pr_comment.build_model` had no
branch for it, so an aggregate document fell through to the ``compare``
adapter, which read the absent ``changes`` key, found nothing, and rendered
"✅ No ABI changes" for a run that may have been failing on every target it
covered. That is the defect this module closes, and it is the reason every
path below is written to fail *loudly* rather than quietly: an aggregate
comment's whole job is to say what a fan-in of N reports found, and the one
answer it may never give by accident is "nothing".

Two facts shape the design.

**Per-target detail is not in the document.** Each analyzed target carries a
``report_path`` pointing at the member report it was folded from, and the
detail a reviewer wants -- which symbol, which kind, which location -- lives
only there. So this module loads each member report *relative to the
aggregate document's own directory* and builds its model with the same
per-shape adapters ``build_model`` already dispatches to
(``compare``/``release``/``appcompat``/no-baseline audit), then folds the
results. Nothing here re-classifies a finding, re-derives a verdict, or
invents a gate: the outcome vocabulary is read from the aggregate document
and from the member models, never recomputed from the folded change set.

**A member report is untrusted input.** It is named by a document that may
itself have come from a fork PR's artifact. Every member load therefore goes
through :func:`load_member_report`, which refuses an absolute path, a path
escaping the document's directory, a symlinked component, a non-regular
file, an oversized file, and anything that is not a JSON object -- and every
refusal becomes a visible limitation finding naming the target and the
reason. A member that cannot be read is never silently dropped, because a
dropped target is exactly how a fan-in comment learns to lie.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath, PureWindowsPath

from ..pr_comment_base import CommentModel, Finding
from .change_summary import ChangeSummary, fold_change_summaries

#: Ceiling on one member report's on-disk size. A fan-in comment reads as
#: many of these as the document names, from a directory that may have been
#: unpacked from a contributor-produced artifact, so "just read it" is a
#: memory-exhaustion primitive. 32 MiB is far above any real ``compare``
#: JSON (the largest in this repository's own corpora are single-digit MiB)
#: and far below anything that threatens a runner.
MEMBER_REPORT_MAX_BYTES = 32 * 1024 * 1024

#: Gate categories that mean *this target never reached a comparison*, as
#: opposed to *this target compared and broke*. ``aggregate`` forces a
#: synthetic ``BREAKING`` verdict and a blocking gate for a not-comparable
#: report so it cannot pass a CI gate unnoticed
#: (``workflows/aggregate/load.py``), and ``TargetReport``'s own docstring
#: names ``gate.blocking_categories`` as the documented way to tell the two
#: apart. Reporting that synthetic verdict as an ABI break would tell a
#: reviewer to go looking for a break that was never observed, so a target
#: in one of these states is rendered as a limitation instead.
NON_COMPARISON_GATE_CATEGORIES = frozenset({"not_comparable", "operational_error"})

#: Verdicts that carry no finding at all, so a target whose member report
#: could not be itemized contributes a truthful zero rather than the
#: conservative lower bound below.
_EMPTY_VERDICTS = frozenset({"NO_CHANGE"})

#: Which bucket a target-level verdict belongs in when its member report was
#: not itemizable. Mirrors ``pr_comment._VERDICT_BUCKET`` -- the same
#: vocabulary, read rather than re-derived.
_VERDICT_BUCKET = {
    "BREAKING": "breaking",
    "API_BREAK": "review",
    "COMPATIBLE_WITH_RISK": "review",
    "COMPATIBLE": "safe",
    "NO_CHANGE": "safe",
}


class MemberReportRefused(Exception):
    """A member report named by the aggregate document was not loaded.

    Carries the reviewer-facing reason verbatim: every refusal reaches the
    rendered comment as a limitation, so the message is written for the
    person reading the PR, not for a log.
    """


# ---------------------------------------------------------------------------
# Member-report loading — the untrusted-input boundary
# ---------------------------------------------------------------------------


def _reject_absolute(raw: str) -> None:
    """Refuse an absolute member path under *either* platform's rules.

    ``PurePosixPath("C:/x").is_absolute()`` is ``False`` and
    ``PureWindowsPath("/x").is_absolute()`` is ``False`` too, so a check
    written against only the running platform's flavour lets the other
    platform's absolute spelling through. An aggregate document produced on
    a Windows runner and rendered on a Linux one is an ordinary
    configuration, not a contrived one.
    """
    if PurePosixPath(raw).is_absolute() or PureWindowsPath(raw).is_absolute():
        raise MemberReportRefused(
            f"its report_path `{raw}` is absolute; member reports are read only "
            "from the aggregate document's own directory"
        )
    if PureWindowsPath(raw).drive:
        raise MemberReportRefused(
            f"its report_path `{raw}` names a drive; member reports are read "
            "only from the aggregate document's own directory"
        )


def resolve_member_path(base_dir: Path, raw: str) -> Path:
    """The on-disk path of a member report, or refuse with a stated reason.

    Containment is established twice, deliberately, because the two checks
    fail on different inputs. The *component walk* refuses a symlink at any
    level -- including one whose target is inside *base_dir*, since a
    symlink in a contributor-supplied tree is a redirection primitive
    whatever it currently points at, and including the final component,
    which ``Path.is_file()`` would happily follow. The *resolved-prefix*
    check then refuses anything that still lands outside *base_dir* --
    ``..`` segments, and the case where *base_dir* itself is reached through
    a symlink.

    Neither check alone is sufficient: a pure prefix comparison accepts a
    symlink pointing back inside the tree (a file the document did not
    actually name), and a pure symlink walk accepts ``a/../../etc/passwd``.
    """
    if not raw:
        raise MemberReportRefused(
            "the aggregate document records no report_path for it"
        )
    _reject_absolute(raw)
    parts = [p for p in PurePosixPath(raw.replace("\\", "/")).parts if p != "."]
    if any(part == ".." for part in parts):
        raise MemberReportRefused(
            f"its report_path `{raw}` traverses out of the aggregate document's "
            "directory"
        )
    if not parts:
        raise MemberReportRefused(f"its report_path `{raw}` names no file")
    current = base_dir
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise MemberReportRefused(
                f"its report_path `{raw}` passes through the symlink "
                f"`{part}`; member reports must be plain files under the "
                "aggregate document's directory"
            )
    try:
        resolved_base = base_dir.resolve(strict=False)
        resolved = current.resolve(strict=False)
        resolved.relative_to(resolved_base)
    except (OSError, ValueError) as exc:
        raise MemberReportRefused(
            f"its report_path `{raw}` does not resolve inside the aggregate "
            "document's directory"
        ) from exc
    return current


def load_member_report(
    base_dir: Path,
    raw: str,
    *,
    max_bytes: int = MEMBER_REPORT_MAX_BYTES,
) -> dict[str, object]:
    """Load one member report, or raise :class:`MemberReportRefused`.

    Every failure mode -- refused path, missing file, non-regular file,
    oversized file, unreadable bytes, malformed JSON, a JSON value that is
    not an object -- raises with a reviewer-facing reason. None of them
    returns an empty report, because an empty report is indistinguishable
    from a clean one by the time it reaches a bucket.
    """
    path = resolve_member_path(base_dir, raw)
    try:
        stat = path.stat()
    except OSError as exc:
        raise MemberReportRefused(
            f"its report `{raw}` could not be read ({exc.strerror or exc})"
        ) from exc
    if not path.is_file():
        raise MemberReportRefused(f"its report_path `{raw}` is not a regular file")
    if stat.st_size > max_bytes:
        raise MemberReportRefused(
            f"its report `{raw}` is {stat.st_size} bytes, over the "
            f"{max_bytes}-byte member-report limit"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise MemberReportRefused(
            f"its report `{raw}` could not be read ({exc})"
        ) from exc
    except json.JSONDecodeError as exc:
        raise MemberReportRefused(
            f"its report `{raw}` is not valid JSON ({exc})"
        ) from exc
    if not isinstance(data, dict):
        raise MemberReportRefused(f"its report `{raw}` is not a JSON object")
    return data


# ---------------------------------------------------------------------------
# Reading the aggregate document
# ---------------------------------------------------------------------------


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _str_list(value: object) -> list[str]:
    return [str(x) for x in value] if isinstance(value, list) else []


def _targets(report: Mapping[str, object]) -> list[dict[str, object]]:
    """Every target row the document carries, expected and unexpected alike.

    Unexpected targets are included because they are reported ones: the
    document's own gate counts them under ``include``/``fail``, and a
    comment that showed only the expected set would hide the very leg a
    ``fail`` policy is blocking on.
    """
    out: list[dict[str, object]] = []
    for key in ("targets", "unexpected_targets"):
        raw = report.get(key)
        if isinstance(raw, list):
            out += [dict(t) for t in raw if isinstance(t, Mapping)]
    return out


def _limitation(symbol: str, detail: str, component: str = "") -> Finding:
    """One analysis-incomplete finding.

    Carries no ``severity``/``category`` on purpose: these are not
    compatibility claims, and stamping one would make them eligible for the
    severity-driven blocking derivations that belong to real findings. The
    aggregate document's own axes decide what blocks (see
    :func:`build_aggregate_model`).
    """
    return Finding(
        kind="aggregate_limitation",
        symbol=symbol,
        detail=detail,
        component=component,
    )


def _target_limitations(target: Mapping[str, object]) -> list[Finding]:
    """Limitations a target's own row states, without reading its report."""
    tid = str(target.get("target_id", "?"))
    state = str(target.get("state", ""))
    gate = _mapping(target.get("gate"))
    categories = frozenset(_str_list(gate.get("blocking_categories")))
    reason = str(target.get("reason", "") or "")
    required = bool(target.get("required"))
    unexpected = bool(target.get("unexpected"))
    out: list[Finding] = []
    if state != "analyzed":
        scope = "required" if required else "optional"
        detail = (
            f"No result for this {scope} target — "
            f"{reason or 'no report was produced for it'}."
        )
        if gate.get("blocking") or _int(gate.get("exit_code")) > 0:
            # The abort shapes (a budget overflow, an evidence-contract
            # refusal) publish a real gate decision with no comparison
            # behind it. Saying only "unavailable" would under-report a leg
            # that is actively failing the run.
            detail += " Its own run reported a blocking gate."
        out.append(_limitation(tid, detail))
    elif categories & NON_COMPARISON_GATE_CATEGORIES:
        named = ", ".join(sorted(categories & NON_COMPARISON_GATE_CATEGORIES))
        out.append(
            _limitation(
                tid,
                f"This target reported `{named}` — it never reached a "
                f"comparison, so nothing about its compatibility was "
                f"established. {reason or ''}".strip(),
            )
        )
    if unexpected:
        out.append(
            _limitation(
                tid,
                "This target is not in the declared expected set; it was "
                "reported anyway and is shown here rather than dropped.",
            )
        )
    return out


def _axis_limitations(
    report: Mapping[str, object], already_listed: frozenset[str]
) -> tuple[list[Finding], bool]:
    """The document's own coverage/assurance/scope/contract shortfalls.

    Returns ``(findings, blocking)`` where *blocking* is true when at least
    one axis actually contributed to the document's exit code. The two are
    read separately for the reason ADR-049 already establishes for the
    single-report ledger: an axis accepted under a ``warn`` policy
    contributes ``0`` while its shortfall stays entirely real, and gating
    the finding's existence on the contribution is how an accepted gap
    becomes an invisible one.

    *already_listed* holds the target ids that already have a row of their
    own in the limitation section. The coverage axis still reports -- it
    carries a fact no per-target row does, namely whether the policy in
    force actually gates on the gap -- but it stops repeating names the
    reader has just read.
    """
    out: list[Finding] = []
    blocking = False

    coverage = _mapping(report.get("coverage"))
    missing = _str_list(coverage.get("missing_required_targets"))
    if missing:
        gated = (
            "blocks this run"
            if coverage.get("blocking")
            else "is advisory under the policy in force"
        )
        unlisted = [t for t in missing if t not in already_listed]
        named = f": {', '.join(unlisted)}" if unlisted else " (listed above)"
        out.append(
            _limitation(
                "Required targets",
                f"{len(missing)} required target(s) never reported{named} — "
                f"this {gated}.",
            )
        )
        if coverage.get("blocking"):
            blocking = True

    for key, label in (
        ("contract_coverage", "Contract coverage"),
        ("analysis_assurance", "Analysis assurance"),
        ("scope_completeness", "Comparison scope"),
    ):
        block = _mapping(report.get(key))
        incomplete = _str_list(block.get("incomplete_targets"))
        contribution = _int(block.get("exit_contribution"))
        if not incomplete and contribution == 0:
            continue
        named = ", ".join(incomplete) if incomplete else "see the full report"
        gated = "" if contribution else " (accepted by policy; listed, not gated)"
        out.append(
            _limitation(
                label,
                f"Incomplete on: {named}{gated}.",
            )
        )
        if contribution:
            blocking = True

    unaudited = _str_list(report.get("disposition_audit_missing_targets"))
    if unaudited:
        out.append(
            _limitation(
                "Disposition audit",
                f"{len(unaudited)} target(s) carried no disposition audit, so "
                f"the raw-versus-effective totals below exclude them: "
                f"{', '.join(unaudited)}.",
            )
        )

    policy = _mapping(report.get("effective_policy"))
    unexpected = [t for t in _targets(report) if bool(t.get("unexpected"))]
    if unexpected and str(policy.get("unexpected_target", "")) == "fail":
        blocking = True
    return out, blocking


# ---------------------------------------------------------------------------
# The fold
# ---------------------------------------------------------------------------


def _fallback_row(tid: str, verdict: str) -> tuple[str, str, int, int, int]:
    """A per-target row for a target whose findings could not be itemized.

    Counts come from the target's own verdict, as a *lower bound*: one
    finding in the bucket the verdict names, and a truthful zero for
    ``NO_CHANGE``. This is the same conservative substitution
    ``pr_comment._release_lib_row`` already makes for a library whose
    comparison errored and therefore carries no count fields, and it exists
    for the same reason: a row of zeros beside a ``BREAKING`` verdict would
    let the comment's headline read "no ABI changes" while the table under
    it shows a break.
    """
    if verdict in _EMPTY_VERDICTS:
        return tid, verdict, 0, 0, 0
    bucket = _VERDICT_BUCKET.get(verdict)
    if bucket is None:
        # An unknown verdict is not evidence of safety. Counting it as one
        # needs-review finding keeps it visible without claiming a break.
        return tid, verdict or "?", 0, 1, 0
    return (
        tid,
        verdict,
        1 if bucket == "breaking" else 0,
        1 if bucket == "review" else 0,
        1 if bucket == "safe" else 0,
    )


def _tag(findings: Sequence[Finding], component: str) -> list[Finding]:
    """Copy *findings* with their originating target recorded on each.

    :func:`dataclasses.replace`, not a field-by-field constructor call: the
    hand-written version silently dropped whichever field was added to
    :class:`~abicheck.pr_comment_base.Finding` next (it lost
    ``evolution``, so every folded member's pre-existing hygiene finding
    came back through the fan-in unstamped, and therefore counted as a
    change the comparison introduced). A copy that enumerates fields is a
    copy that goes stale.
    """
    return [replace(f, component=component) for f in findings]


def _member_evidence_limitation(tid: str, member: CommentModel) -> Finding | None:
    """A folded member's own recorded limits on what it could check.

    The member's full :class:`~abicheck.report.evidence_summary.
    EvidenceSummary` is deliberately *not* merged into the aggregate model:
    merging them would need a rule for combining "not applicable on this
    platform" with "missing here", and on a cross-platform matrix -- the
    exact case ``aggregate`` exists for -- those two are the common case and
    the merged answer would be wrong in both directions. Naming the target
    and pointing at its own report states the fact without inventing the
    merge.
    """
    evidence = member.evidence
    if evidence is None or not evidence.has_limitations:
        return None
    warnings = list(evidence.coverage_warnings)
    if warnings:
        head = warnings[0]
        more = f" (+{len(warnings) - 1} more)" if len(warnings) > 1 else ""
        return _limitation(tid, f"Limits on what was checked: {head}{more}", tid)
    return _limitation(
        tid,
        "This target's own report recorded limits on what it could check; "
        "see its full report.",
        tid,
    )


@dataclass
class _Fold:
    """The accumulator one target at a time contributes to.

    A struct rather than a dozen locals threaded through
    :func:`build_aggregate_model`: the per-target work is a loop body with
    ten outputs, and a parameter list that long is how one of them ends up
    silently not updated on some path.
    """

    rows: list[tuple[str, str, int, int, int]] = field(default_factory=list)
    breaking: list[Finding] = field(default_factory=list)
    review: list[Finding] = field(default_factory=list)
    safe: list[Finding] = field(default_factory=list)
    incomplete: list[Finding] = field(default_factory=list)
    background: list[Finding] = field(default_factory=list)
    categories: set[str] = field(default_factory=set)
    severities: set[str] = field(default_factory=set)
    summaries: list[ChangeSummary] = field(default_factory=list)
    suppressed: int = 0
    reclassified: int = 0
    member_blocking: bool = False
    any_analyzed: bool = False
    itemized: int = 0


def _load_member(
    target: Mapping[str, object],
    tid: str,
    *,
    build_member_model: Callable[[dict[str, object]], CommentModel],
    base_dir: Path | None,
    max_member_bytes: int,
) -> tuple[CommentModel | None, Finding | None]:
    """One member's model, or ``(None, limitation)`` stating why not.

    Every failure becomes a *limitation*, never an omission and never an
    exception that escapes: one member written in a shape this build cannot
    model (a newer schema, a retired one) must not cost every other target's
    result too.
    """
    if base_dir is None:
        return None, _limitation(
            tid,
            "Per-target detail is unavailable: the aggregate document's own "
            "directory is not known to this renderer, so no member report "
            "was read.",
            tid,
        )
    raw_path = target.get("report_path")
    try:
        data = load_member_report(
            base_dir,
            str(raw_path) if raw_path is not None else "",
            max_bytes=max_member_bytes,
        )
        return build_member_model(data), None
    except MemberReportRefused as exc:
        return None, _limitation(tid, f"Per-target detail is unavailable: {exc}", tid)
    except Exception as exc:  # noqa: BLE001 - see this function's docstring
        return None, _limitation(
            tid,
            f"Per-target detail is unavailable: its report could not be "
            f"rendered ({type(exc).__name__}: {exc})",
            tid,
        )


def _fold_target(
    fold: _Fold,
    target: Mapping[str, object],
    *,
    build_member_model: Callable[[dict[str, object]], CommentModel],
    base_dir: Path | None,
    max_member_bytes: int,
) -> None:
    """Add one target's contribution to *fold*, in place."""
    tid = str(target.get("target_id", "?"))
    state = str(target.get("state", ""))
    verdict = target.get("compatibility_verdict")
    verdict_str = str(verdict) if verdict is not None else ""
    gate_categories = frozenset(
        _str_list(_mapping(target.get("gate")).get("blocking_categories"))
    )
    fold.incomplete += _target_limitations(target)
    if state != "analyzed":
        # No comparison behind it; the limitation above is the whole report
        # for this target. A row of zeros would put it in the results table
        # as though it had been checked.
        return
    fold.any_analyzed = True
    if gate_categories & NON_COMPARISON_GATE_CATEGORIES:
        # Analyzed in the document's bookkeeping sense, but its verdict is
        # the synthetic one `aggregate` forces so the leg cannot pass
        # unnoticed. Show it with the state it actually reached.
        fold.rows.append((tid, sorted(gate_categories)[0].upper(), 0, 0, 0))
        return

    member, refusal = _load_member(
        target,
        tid,
        build_member_model=build_member_model,
        base_dir=base_dir,
        max_member_bytes=max_member_bytes,
    )
    if refusal is not None:
        fold.incomplete.append(refusal)
    if member is None:
        fold.rows.append(_fallback_row(tid, verdict_str))
        return

    fold.itemized += 1
    nb, nr, ns = member.counts
    fold.rows.append((tid, verdict_str or "?", nb, nr, ns))
    fold.breaking += _tag(member.breaking, tid)
    fold.review += _tag(member.review, tid)
    fold.safe += _tag(member.safe, tid)
    fold.incomplete += _tag(member.incomplete, tid)
    # ADR-068 D3: each member states which of its hygiene findings it
    # introduced; a fan-in may not decide differently, so the folded
    # model keeps the same separation the member's own comment does.
    fold.background += _tag(member.background, tid)
    fold.categories |= set(member.breaking_categories)
    fold.severities |= set(member.breaking_severities)
    fold.suppressed += member.suppressed_count
    fold.reclassified += member.reclassified_count
    fold.member_blocking = fold.member_blocking or member.incomplete_blocking
    evidence_note = _member_evidence_limitation(tid, member)
    if evidence_note is not None:
        fold.incomplete.append(evidence_note)
    if member.change_summary is not None:
        fold.summaries.append(member.change_summary)


def build_aggregate_model(
    report: Mapping[str, object],
    *,
    build_member_model: Callable[[dict[str, object]], CommentModel],
    base_dir: Path | None,
    max_member_bytes: int = MEMBER_REPORT_MAX_BYTES,
) -> CommentModel:
    """Fold an ``aggregate`` document, and its member reports, into one model.

    *build_member_model* is injected rather than imported so this module
    stays free of a cycle back through :mod:`abicheck.pr_comment` (which
    owns the per-shape adapters and dispatches here). *base_dir* is the
    directory the aggregate document itself was read from; ``None`` means no
    directory is known, and every member is then refused with that stated as
    the reason rather than being read from the process's working directory,
    which is not a location the document said anything about.

    What this function decides and what it only reads:

    * It **reads** every target's state, verdict and gate from the document,
      and every folded member's buckets and counts from that member's own
      adapter.
    * It **decides** nothing about compatibility. There is no second verdict
      classifier here: the per-target rows carry each target's own verdict
      string, and the headline is derived by the shared renderer from the
      same bucket counts every other mode produces.
    * Its one derived judgement is *blocking-ness of the limitations*, taken
      from the document's own exit-floor axes and gate -- never from the
      folded findings.
    """
    targets = _targets(report)
    fold = _Fold()
    for target in targets:
        _fold_target(
            fold,
            target,
            build_member_model=build_member_model,
            base_dir=base_dir,
            max_member_bytes=max_member_bytes,
        )
    rows = fold.rows
    incomplete = fold.incomplete

    axis_findings, axis_blocking = _axis_limitations(
        report,
        # Only this module's own limitation rows, never a folded member's
        # finding: a symbol that happens to be spelled like a target id
        # must not make the coverage axis believe that target was already
        # listed.
        frozenset(f.symbol for f in incomplete if f.kind == "aggregate_limitation"),
    )
    incomplete += axis_findings

    gate_block = _mapping(report.get("gate"))
    exit_code = _int(gate_block.get("exit_code"))
    # The one unconditional guarantee this adapter owes: a document whose
    # own gate failed never renders as a clean comment. Everything above is
    # best-effort attribution -- a target row, an axis, a member's own
    # finding -- and any of it can come up empty for a document written by a
    # newer build or an axis this adapter does not yet name. The check is
    # therefore on the *rendered outcome* (would this comment show anything
    # at all?), not on whether a particular attribution path fired, because
    # only the outcome is what a reviewer actually sees.
    if exit_code > 0 and not incomplete and not any(r[2] or r[3] for r in rows):
        blockers = _str_list(gate_block.get("blocking_targets"))
        named = f" Blocking target(s): {', '.join(blockers)}." if blockers else ""
        incomplete.append(
            _limitation(
                "Aggregate gate",
                f"This aggregate run exited {exit_code}, but nothing in the "
                f"document attributes it to a target or axis this renderer "
                f"knows about.{named} See the full report.",
            )
        )
        axis_blocking = True

    contract_block = _mapping(report.get("contract_coverage"))
    contract_blocking = _int(contract_block.get("exit_contribution")) >= 1
    forced_gate_blocking = any(
        (
            str(t.get("state", "")) != "analyzed"
            or frozenset(_str_list(_mapping(t.get("gate")).get("blocking_categories")))
            & NON_COMPARISON_GATE_CATEGORIES
        )
        and bool(_mapping(t.get("gate")).get("blocking"))
        for t in targets
    )

    n_targets = len(targets)
    summary_inexact_reason = ""
    if fold.itemized < len(rows):
        summary_inexact_reason = (
            f"{len(rows) - fold.itemized} of {len(rows)} target(s) could not "
            "be itemized, so these are lower bounds"
        )
    folded_summary = fold_change_summaries(
        fold.summaries, inexact_reason=summary_inexact_reason
    )
    if summary_inexact_reason and folded_summary.rows:
        folded_summary = ChangeSummary(
            rows=folded_summary.rows,
            unit=folded_summary.unit,
            exact=False,
            inexact_reason=summary_inexact_reason,
            counted=folded_summary.counted,
            fell_back_to_registry=folded_summary.fell_back_to_registry,
        )

    return CommentModel(
        mode="aggregate",
        subject=f"{n_targets} target{'' if n_targets == 1 else 's'}",
        old_label="each target vs its own baseline",
        new_label=str(report.get("head_sha", "") or "candidate"),
        policy="per-target policy",
        breaking=fold.breaking,
        review=fold.review,
        safe=fold.safe,
        incomplete=incomplete,
        background=fold.background,
        incomplete_blocking=(
            axis_blocking or fold.member_blocking or forced_gate_blocking
        ),
        contract_coverage_blocking=contract_blocking,
        breaking_categories=frozenset(fold.categories),
        breaking_severities=frozenset(fold.severities),
        library_rows=rows,
        # ADR-065 D7's wording, applied one level up: a fan-in where not one
        # target reached a comparison may not render as "no ABI changes".
        no_comparison_completed=bool(targets) and not fold.any_analyzed,
        suppressed_count=fold.suppressed,
        reclassified_count=fold.reclassified,
        # Read verbatim from the document's own already-folded block
        # (`report/aggregate.py`), never re-folded here: a second fold over
        # the member reports would double-count every target whose audit the
        # document already absorbed.
        disposition_audit=(
            _mapping(report.get("disposition_audit"))
            if isinstance(report.get("disposition_audit"), Mapping)
            else None
        ),
        change_summary=folded_summary if folded_summary.rows else None,
    )


__all__ = [
    "MEMBER_REPORT_MAX_BYTES",
    "MemberReportRefused",
    "build_aggregate_model",
    "load_member_report",
    "resolve_member_path",
]
