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

"""Sticky GitHub PR-comment rendering.

Renders a single, updatable PR comment from an abicheck JSON report
(``compare``, ``compare-release`` or ``appcompat`` mode). The comment is a
*content* channel and is independent of the check's red/green gate: ABI/API
breaks turn the step red via exit codes (see ``action/run.sh``), while this
comment groups every finding into three compatibility buckets so a reviewer
sees, in one place:

* **Breaking** — clear ABI breaks (and gated source breaks);
* **Needs review** — source breaks / risk a human should sign off on;
* **Compatible** — compatible changes: new public-API surface and quality
  findings.

The compatible bucket is a pure mirror of the severity the checker already
assigned (``severity`` field in the JSON, which honours public-surface
scoping and the active policy) — this module never re-classifies anything.
Within it, new public-API surface (the ``ADDITION_KINDS`` registry —
``func_added``, ``type_added``, ``enum_member_added``, etc.) renders as its
own "➕ Public API additions" table with per-symbol kind/detail/location,
separate from the generic "ℹ️ Informational findings" list of quality
findings ("Safe" reads as an absolute guarantee it isn't) — a reviewer
approving a new export wants to see what was added, not just a bare count
folded in with unrelated quality notes.

A severity-config category set to ``error`` (e.g. ``severity-addition:
error``) moves its findings into the Breaking bucket too, since that is what
turns the check red — but a COMPATIBLE addition/quality finding promoted this
way is still not an ABI/API break (ADR-042: compatibility and gate decisions
are separate axes). The headline and the Breaking section title reflect this:
they only say "ABI BREAKING" when the bucket actually holds a genuine
``abi_breaking``/``potential_breaking`` finding, and say "blocked by policy"
instead when every member is a gate-promoted COMPATIBLE finding.

**A fourth, orthogonal bucket — "analysis incomplete" — never joins the three
compatibility buckets above.** ``layer_coverage_asymmetric``,
``evidence_required_missing``, ``source_fact_coverage_incomplete``, and
``dwarf_info_missing`` (see :data:`_EVIDENCE_KIND_VALUES`) are not claims
about the API/ABI at all: they say the *comparison itself* ran with degraded
or missing evidence, so some real changes may be undetectable. The severity
field the checker stamps on them (``risk``/``api_break``/``compatible``, so
the underlying finding still sorts correctly everywhere else that reads
``severity``) is an artifact of reusing the ``Verdict`` enum for gating, not a
compatibility verdict — folding them into "Needs review" is exactly the bug
this split fixes: a clean, implementation-only PR with zero API changes but
one coverage gap used to render the same generic "⚠️ Review recommended"
headline as a PR with a genuine, risky source-API change, giving a reviewer no
way to tell "something in this diff might be unsafe" apart from "we couldn't
fully check this diff." They render in their own "🛑 Analysis incomplete"
section with a headline that says so explicitly, and never contribute to the
Breaking/Needs review/Safe counts. Whether that headline reads as *blocking*
(🛑) or merely *advisory* (⚠️) mirrors the Action's own red/green gate exactly
(see :func:`_incomplete_is_blocking`) — none of these four kinds is
intrinsically always-blocking.

The body carries a hidden HTML :data:`MARKER` so the action can find and
update the same comment across runs, and surfaces the scanned commit SHA in
both the header and the footer.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone

from .demangle import demangle_batch
from .pr_comment_base import (
    _ADDITION_KIND_VALUES,
    _EVIDENCE_KIND_VALUES,
    _SEVERITY_BUCKET,
    CommentModel,
    Finding,
    _breaking_categories,
    _breaking_severities,
    _contract_coverage_findings,
    _demangle_symbol,
    _esc,
    _evidence_symbol_label,
    _finding_category,
    _normalize_location,
    _severity_levels,
)
from .pr_comment_scan import from_scan, scan_note

# `_EVIDENCE_KIND_VALUES`/`_evidence_symbol_label` (the "analysis incomplete"
# bucket's kind classification -- see the module docstring above) now live
# in `pr_comment_base.py`, shared with `pr_comment_scan.py`'s own finding
# classification (Codex review, follow-up: a `scan --against` diff can carry
# the identical evidence-quality kinds through `diff.findings`, and routing
# them through the generic compatibility-severity buckets there produced a
# misleading "Compatibility risk" headline for what is really a
# missing-evidence signal).

# Hidden marker used to find-and-update the sticky comment across runs.
MARKER = "<!-- abicheck-sticky-report -->"

DETAIL_LEVELS = ("summary", "standard", "full")
POST_MODES = ("always", "changes", "never")


# Verdict strings → reviewer bucket, for release-global findings (bundle /
# probe-matrix) that carry no per-item severity, only a section verdict.
_VERDICT_BUCKET = {
    "BREAKING": "breaking",
    "API_BREAK": "review",
    "COMPATIBLE_WITH_RISK": "review",
    "COMPATIBLE": "safe",
    "NO_CHANGE": "safe",
}

# Per-detail row caps for the "standard" level (full = uncapped).
_STANDARD_ROW_CAP = 25
_SAFE_SYMBOLS_PER_KIND = 12
# Member symbols listed inline in an aggregated (API-grouped) Breaking/Review row.
_GROUP_MEMBERS_INLINE = 8

# GitHub rejects issue/PR comment bodies longer than 65,536 characters. Render
# within a budget below that; if the body overflows, downgrade the detail level
# (full → standard → summary) and finally hard-truncate so we never exceed it.
GITHUB_COMMENT_LIMIT = 65536
_BODY_BUDGET = 64000
_DETAIL_DOWNGRADE = {
    "full": ("full", "standard", "summary"),
    "standard": ("standard", "summary"),
    "summary": ("summary",),
}

_VERDICT_EMOJI = {
    "BREAKING": "❌",
    "API_BREAK": "⚠️",
    "COMPATIBLE_WITH_RISK": "⚠️",
    "COMPATIBLE": "✅",
    "NO_CHANGE": "✅",
    "ERROR": "🛑",
}

# ---------------------------------------------------------------------------
# Parsing — JSON report → CommentModel
# ---------------------------------------------------------------------------


def _basename(path: object) -> str:
    s = str(path or "").rstrip("/")
    return s.rsplit("/", 1)[-1] or str(path or "")


def _detail_text(change: dict[str, object]) -> str:
    desc = str(change.get("description", "") or "").strip()
    old, new = change.get("old_value"), change.get("new_value")
    if old not in (None, "") and new not in (None, ""):
        delta = f"{old} → {new}"
        return f"{desc} ({delta})" if desc else delta
    return desc


def _bucket_changes(
    changes: object,
    gate_api_break: bool = False,
    levels: dict[str, str] | None = None,
) -> tuple[list[Finding], list[Finding], list[Finding], list[Finding]]:
    breaking: list[Finding] = []
    review: list[Finding] = []
    safe: list[Finding] = []
    incomplete: list[Finding] = []
    target = {"breaking": breaking, "review": review, "safe": safe}
    levels = levels or {}
    changes_list = changes if isinstance(changes, list) else []
    # Demangle every symbol up front in one batch (`demangle_batch` forks at
    # most one `c++filt` process for the whole report, memoised across
    # symbols already seen elsewhere in this process) rather than once per
    # finding.
    demangled_map = demangle_batch(
        [str(c.get("symbol", "")) for c in changes_list if isinstance(c, dict)]
    )
    if isinstance(changes, list):
        for c in changes:
            if not isinstance(c, dict):
                continue
            sev = str(c.get("severity", "unknown"))
            kind = str(c.get("kind", ""))
            # Evidence-quality findings never enter the compatibility buckets
            # (see `_EVIDENCE_KIND_VALUES`/module docstring) — pulled out
            # ahead of the severity-bucket/gate logic below, which is about
            # compatibility gating and does not apply to them.
            if kind in _EVIDENCE_KIND_VALUES:
                loc = c.get("source_location")
                symbol = _evidence_symbol_label(kind, str(c.get("symbol", "")))
                incomplete.append(
                    Finding(
                        kind=kind,
                        symbol=symbol,
                        detail=_detail_text(c),
                        location=_normalize_location(str(loc)) if loc else None,
                        severity=sev,
                        impact=str(c.get("impact", "") or ""),
                    )
                )
                continue
            bucket = _SEVERITY_BUCKET.get(sev, "review")
            # fail-on-api-break turns the check red on API breaks (only) …
            if gate_api_break and sev == "api_break":
                bucket = "breaking"
            # … and a severity category set to error turns it red too.
            category = _finding_category(sev, kind)
            if levels.get(category) == "error":
                bucket = "breaking"
            loc = c.get("source_location")
            display_symbol, mangled_evidence = _demangle_symbol(
                str(c.get("symbol", "")), demangled_map
            )
            target[bucket].append(
                Finding(
                    kind=kind,
                    symbol=display_symbol,
                    detail=_detail_text(c),
                    location=_normalize_location(str(loc)) if loc else None,
                    category=category,
                    severity=sev,
                    impact=str(c.get("impact", "") or ""),
                    mangled=mangled_evidence,
                )
            )
    return breaking, review, safe, incomplete


def _incomplete_is_blocking(
    findings: list[Finding],
    gate_api_break: bool,
    gate_breaking: bool = True,
    levels: dict[str, str] | None = None,
) -> bool:
    """Whether the analysis-incomplete bucket represents a hard failure —
    i.e. whether it actually turns the *Action's* check red, mirroring
    ``action/run.sh``'s own ``compare``-mode gate exactly. Four rounds of
    Codex review corrected four different overclaims here; read together,
    they say the same thing about every finding-severity/exit-code tier
    ``compare`` can produce, so each is listed once below rather than as a
    change log.

    The fourth round's fix is the one worth spelling out precisely: earlier
    rounds treated ``api_break``/``risk``/``breaking`` severities alike
    regardless of *which exit-code scheme actually produced the report* —
    but the two schemes disagree on what a bare ``risk``-severity finding
    contributes. *legacy* scheme (``report["severity"]`` absent — no
    a severity setting; *levels* is ``{}``) maps every verdict to a
    **fixed** exit code via ``severity.legacy_exit_code`` regardless of any
    per-category config: ``BREAKING`` → 4, ``API_BREAK`` → 2, and
    critically ``COMPATIBLE_WITH_RISK`` (a bare ``risk`` finding) → **0** —
    it never gates at all under legacy, no matter ``fail-on-api-break``.
    *severity-aware* scheme (*levels* non-empty) instead computes the exit
    code via ``severity.compute_exit_code``, which folds in a category's
    exit contribution **only when that category's configured level is
    ``error``** — so under this scheme ``api_break``/``risk`` (both
    ``potential_breaking``) and ``breaking`` (``abi_breaking``) need their
    matching category actually gated to ``error``, not just present, before
    the matching ``fail-on-*`` flag means anything. Treating every
    ``risk``/``api_break``/``breaking`` finding as blocking under its
    ``fail-on-*`` flag alone — as an earlier revision did — is right for
    ``api_break``/``breaking`` under legacy scheme (where the mapping truly
    is unconditional), but wrong for ``risk`` under legacy (never gates)
    and wrong for all three under severity-aware scheme with the matching
    category left at its default, non-``error`` level.

    - ``severity == "breaking"``:
        - legacy scheme: blocks under ``gate_breaking`` alone (the
          ``BREAKING`` verdict's fixed exit-4 mapping is unconditional).
        - severity-aware scheme: blocks only when *both* ``abi_breaking``
          is configured ``error`` *and* ``gate_breaking``.
    - ``severity in ("api_break", "risk")``:
        - legacy scheme: ``api_break`` blocks under ``gate_api_break``
          alone (fixed exit-2 mapping); ``risk`` never blocks (fixed exit-0
          mapping — ``COMPATIBLE_WITH_RISK`` is exit 0 under legacy).
        - severity-aware scheme: either blocks only when *both*
          ``potential_breaking`` is configured ``error`` *and*
          ``gate_api_break`` — the two severities share one category, so
          they share one condition.
    - The finding's resolved severity-config *category*
      (``_finding_category`` — ``addition``/``quality_issues`` for a
      ``compatible``-severity finding like the default for
      ``dwarf_info_missing``) blocks **unconditionally** when *levels*
      marks that category ``error`` — this is exit code 1, compare's own
      ``SEVERITY_ERROR`` tier, which ``action/run.sh`` gates with no
      ``fail-on-*`` condition at all (unlike the two tiers above, under
      either scheme). Without this check, a ``quality_issues: error``
      config that already fails the real Action step would still render
      the merely-advisory "⚠️ Analysis coverage reduced" headline right
      next to a red check.
    - Otherwise never blocks.
    """
    levels = levels or {}
    severity_aware = bool(levels)
    for f in findings:
        category = _finding_category(f.severity, f.kind)
        if category in ("addition", "quality_issues"):
            if levels.get(category) == "error":
                return True
            continue
        if severity_aware:
            if (
                category == "abi_breaking"
                and levels.get("abi_breaking") == "error"
                and gate_breaking
            ):
                return True
            if (
                category == "potential_breaking"
                and levels.get("potential_breaking") == "error"
                and gate_api_break
            ):
                return True
        else:
            if f.severity == "breaking" and gate_breaking:
                return True
            if f.severity == "api_break" and gate_api_break:
                return True
            # f.severity == "risk" never gates under the legacy scheme —
            # COMPATIBLE_WITH_RISK's fixed legacy exit code is 0.
    return False


def _suppressed_count(report: dict[str, object]) -> int:
    """The number of findings a ``--suppress`` rule removed from ``changes``.

    Reads ``reporter._add_suppression``'s ``suppression`` block
    (``suppression.suppressed_count``) -- ``0`` (not an error) for a report
    without it, which is every run that didn't pass ``--suppress``.
    """
    suppression = report.get("suppression")
    if isinstance(suppression, dict):
        count = suppression.get("suppressed_count")
        if isinstance(count, int):
            return count
    return 0


def _reclassified_count(report: dict[str, object]) -> int:
    """The number of ``changes`` whose verdict was moved to a different
    bucket by a ``--policy-file`` reclassification -- either a kind-global
    ``overrides:`` entry (``reporter._add_policy_overrides``'s
    ``policy_overrides`` map: ``ChangeKind`` value -> overridden ``Verdict``)
    or a selector-scoped ``reclassify:`` rule (``reporter._change_to_dict``'s
    per-change ``reclassified_by`` marker).

    Counts each matching change once even if both mechanisms could apply --
    a change carrying ``reclassified_by`` was, by construction, decided by
    the ``reclassify:`` rule (:func:`abicheck.severity.
    reclassify_rule_for_change` is consulted ahead of ``overrides:``, same
    precedence as the run's own verdict), so it isn't double-counted against
    ``policy_overrides`` too even when its kind also appears there (Codex
    review: a selector-scoped rule silently bypassed this count and the "🔀
    N findings reclassified" PR-comment notice entirely, since only the
    kind-keyed ``overrides:`` map was recognized here -- a `func_removed`
    downgraded to `ignore` by a `reclassify:` rule read as an unremarked
    "safe" change).

    ``0`` for a report with neither, which is every run that didn't pass
    ``--policy-file``.
    """
    overrides = report.get("policy_overrides")
    changes = report.get("changes")
    if not isinstance(changes, list):
        return 0
    overrides_dict = overrides if isinstance(overrides, dict) else {}
    count = 0
    for c in changes:
        if not isinstance(c, dict):
            continue
        if c.get("reclassified_by"):
            count += 1
        elif overrides_dict and str(c.get("kind", "")) in overrides_dict:
            count += 1
    return count


def _from_compare(
    report: dict[str, object],
    gate_api_break: bool = False,
    gate_breaking: bool = True,
) -> CommentModel:
    levels = _severity_levels(report)
    breaking, review, safe, incomplete = _bucket_changes(
        report.get("changes"), gate_api_break, levels
    )
    # Blocking-ness for the ordinary evidence-kind findings above is
    # computed BEFORE folding in the contract-coverage ledger below — a
    # contract_coverage_failure Finding carries no real `severity`, so
    # running it through _incomplete_is_blocking's severity/category checks
    # would risk a false match against an unrelated quality_issues: error
    # config. Its own blocking-ness is exactly
    # contract_coverage_exit_contribution (see below), nothing else.
    incomplete_blocking = _incomplete_is_blocking(
        incomplete, gate_api_break, gate_breaking, levels
    )
    # ADR-049 Phase 5's sibling coverage-failure ledger (Codex review) — see
    # `_contract_coverage_findings`'s own docstring for why `changes` alone
    # misses this entirely. `contract_coverage_exit_contribution` folds via
    # `max` into the real exit code with no fail-on-* gate of its own
    # (AGENTS.md: "no fail-on-* condition at all"), so a nonzero value is
    # unconditionally blocking, independent of gate_api_break/gate_breaking.
    incomplete = incomplete + _contract_coverage_findings(report)
    contract_exit = report.get("contract_coverage_exit_contribution")
    contract_coverage_blocking = isinstance(contract_exit, int) and contract_exit >= 1
    if contract_coverage_blocking:
        incomplete_blocking = True
    # ADR-043 `compare --used-by`/`--required-symbol(s)`: the JSON report
    # overwrites `verdict` with the scoped result and adds `full_verdict` plus
    # `used_by`/`required_symbol_contract` (see cli_compare_helpers's
    # _fold_scoped_compat_into_text) — carry those through so the comment's
    # headline can follow the scoped verdict instead of the raw bucket counts.
    used_by = report.get("used_by")
    required_symbol_contract = report.get("required_symbol_contract")
    scoped_verdict: str | None = None
    if isinstance(used_by, list) or isinstance(required_symbol_contract, dict):
        verdict = report.get("verdict")
        scoped_verdict = str(verdict) if verdict is not None else None
    return CommentModel(
        mode="compare",
        subject=str(report.get("library", "library")),
        old_label=str(report.get("old_version", "old")),
        new_label=str(report.get("new_version", "new")),
        policy=str(report.get("policy", "strict_abi")),
        breaking=breaking,
        review=review,
        safe=safe,
        incomplete=incomplete,
        incomplete_blocking=incomplete_blocking,
        contract_coverage_blocking=contract_coverage_blocking,
        breaking_categories=_breaking_categories(breaking),
        breaking_severities=_breaking_severities(breaking),
        scoped_verdict=scoped_verdict,
        full_verdict=(
            str(report["full_verdict"]) if "full_verdict" in report else None
        ),
        used_by_summaries=[a for a in used_by if isinstance(a, dict)]
        if isinstance(used_by, list)
        else [],
        required_symbol_summary=(
            required_symbol_contract
            if isinstance(required_symbol_contract, dict)
            else None
        ),
        suppressed_count=_suppressed_count(report),
        reclassified_count=_reclassified_count(report),
    )


def _from_appcompat(
    report: dict[str, object],
    gate_api_break: bool = False,
    gate_breaking: bool = True,
) -> CommentModel:
    levels = _severity_levels(report)
    breaking, review, safe, incomplete = _bucket_changes(
        report.get("relevant_changes"), gate_api_break, levels
    )
    missing = report.get("missing_symbols")
    if isinstance(missing, list):
        missing_demangled = demangle_batch([str(sym) for sym in missing])
        for sym in missing:
            display_symbol, mangled_evidence = _demangle_symbol(
                str(sym), missing_demangled
            )
            breaking.append(
                Finding(
                    kind="symbol_missing",
                    symbol=display_symbol,
                    detail="required symbol not provided by new library",
                    category="abi_breaking",
                    severity="breaking",
                    mangled=mangled_evidence,
                )
            )
    # A missing required version tag is breaking for the app too (appcompat
    # treats it the same as a missing symbol), so it must register as a change.
    missing_versions = report.get("missing_versions")
    if isinstance(missing_versions, list):
        for ver in missing_versions:
            breaking.append(
                Finding(
                    kind="version_missing",
                    symbol=str(ver),
                    detail="required symbol version not provided by new library",
                    category="abi_breaking",
                    severity="breaking",
                )
            )
    return CommentModel(
        mode="appcompat",
        subject=_basename(report.get("application", "application")),
        old_label=_basename(report.get("old_library", "old")),
        new_label=_basename(report.get("new_library", "new")),
        policy="strict_abi",
        breaking=breaking,
        review=review,
        safe=safe,
        incomplete=incomplete,
        incomplete_blocking=_incomplete_is_blocking(
            incomplete, gate_api_break, gate_breaking, levels
        ),
        breaking_categories=_breaking_categories(breaking),
        breaking_severities=_breaking_severities(breaking),
    )


def _append_release_global_row(
    rows: list[tuple[str, str, int, int, int]],
    name: str,
    verdict: object,
    findings: object,
    gate_api_break: bool,
    levels: dict[str, str],
    categories: set[str],
    severities: set[str],
) -> None:
    """Fold a release-global check (bundle / probe-matrix) into the rows.

    These findings live at the top level of the report and fold into the
    release verdict; without this a clean per-library release that breaks only
    at the bundle/matrix level would report zero changes and skip the comment.
    Findings are bucketed by the section's own verdict, but each carries its
    ``kind`` — so when a compatible section is gated, the additions and quality
    issues are classified per finding and only the gated category is promoted to
    Breaking, matching what ``_fold_release_global_severity`` actually computes.

    *categories* accumulates the severity-config category responsible each
    time this call pushes a count into the Breaking column, so the headline
    can tell a genuine break from a policy-gated COMPATIBLE section (see
    ``CommentModel.breaking_categories``). *severities* similarly accumulates
    "breaking"/"api_break"/"risk" — needed because "api_break" and "risk"
    share the same "potential_breaking" category but need different headline
    wording (a risk is not a "source API break"). Both are caller-owned
    accumulators (the sole caller, ``_from_release``, shares one set across
    all rows) rather than defaulted here, since an empty-per-call set would
    silently lose that aggregation.
    """
    if not isinstance(findings, list) or not findings:
        return
    verdict_map = (
        {**_VERDICT_BUCKET, "API_BREAK": "breaking"}
        if gate_api_break
        else _VERDICT_BUCKET
    )
    bucket = verdict_map.get(str(verdict or ""), "review")
    levels = levels or {}
    n = len(findings)
    # Classify *why* the section is Breaking exactly once: either it started
    # there (an intrinsically BREAKING verdict — a genuine break — or, under
    # fail-on-api-break, a gated API_BREAK promotion, a source break), or it
    # got promoted from "review" by potential_breaking=error (elif, not a
    # second independent `if`: reusing `if bucket == "breaking"` here would
    # re-fire on the just-promoted bucket and mis-tag a risk as "breaking").
    if bucket == "breaking":
        if str(verdict) == "API_BREAK":
            categories.add("potential_breaking")
            severities.add("api_break")
        else:
            categories.add("abi_breaking")
            severities.add("breaking")
    elif bucket == "review" and levels.get("potential_breaking") == "error":
        # A risk (or, without fail-on-api-break, a plain API_BREAK) section
        # promoted this way — COMPATIBLE_WITH_RISK is a risk, API_BREAK is a
        # real source break; only the latter is a "source API break".
        bucket = "breaking"
        categories.add("potential_breaking")
        severities.add("api_break" if str(verdict) == "API_BREAK" else "risk")
    # A compatible section is additions + quality; classify each finding by its
    # kind and promote only the gated category to Breaking (addition and quality
    # gates are not interchangeable).
    if bucket == "safe":
        add_err = levels.get("addition") == "error"
        qual_err = levels.get("quality_issues") == "error"
        if add_err or qual_err:
            n_add = sum(
                1
                for f in findings
                if isinstance(f, dict) and str(f.get("kind", "")) in _ADDITION_KIND_VALUES
            )
            n_qual = n - n_add
            nb = (n_add if add_err else 0) + (n_qual if qual_err else 0)
            if add_err and n_add:
                categories.add("addition")
            if qual_err and n_qual:
                categories.add("quality_issues")
            rows.append((name, str(verdict or "?"), nb, 0, n - nb))
            return
    rows.append(
        (
            name,
            str(verdict or "?"),
            n if bucket == "breaking" else 0,
            n if bucket == "review" else 0,
            n if bucket == "safe" else 0,
        )
    )


def _release_lib_row(
    lib: dict[str, object],
    gate_api_break: bool,
    levels: dict[str, str],
    categories: set[str],
    severities: set[str],
) -> tuple[str, str, int, int, int]:
    """Per-library (name, verdict, breaking, review, safe) counts.

    Source breaks count as breaking when fail-on-api-break is set or
    potential_breaking is gated to error; risk only when potential_breaking is
    error; additions and quality issues only when their own category is gated to
    error. Otherwise source breaks + risk are review and additions + quality are
    safe. A library whose comparison errored carries no count fields, so it is
    counted as one breaking finding to reflect the failed comparison.

    *categories* accumulates the severity-config category responsible each
    time a count is pushed into ``nb`` (see ``CommentModel.breaking_categories``).
    *severities* similarly accumulates "breaking"/"api_break"/"risk" — needed
    because source_breaks (api_break) and risk_changes (risk) both feed the
    same "potential_breaking" category but need different headline wording.
    """
    name = str(lib.get("library", "?"))
    verdict = str(lib.get("verdict", "?"))
    if verdict == "ERROR":
        categories.add("abi_breaking")
        severities.add("breaking")
        return name, verdict, 1, 0, 0
    src = _as_int(lib.get("source_breaks"))
    risk = _as_int(lib.get("risk_changes"))
    # compatible_additions is the *total* compatible count; quality_issues is the
    # subset that is not an addition. Fall back to treating all as additions when
    # the (older) report omits quality_issues.
    quality = _as_int(lib.get("quality_issues"))
    additions = max(_as_int(lib.get("compatible_additions")) - quality, 0)
    pot_err = levels.get("potential_breaking") == "error"
    add_err = levels.get("addition") == "error"
    qual_err = levels.get("quality_issues") == "error"

    nb = _as_int(lib.get("breaking"))
    if nb:
        categories.add("abi_breaking")
        severities.add("breaking")
    nr = 0
    ns = 0
    if gate_api_break or pot_err:
        if src:
            categories.add("potential_breaking")
            severities.add("api_break")
        nb += src
    else:
        nr += src
    if pot_err:
        if risk:
            categories.add("potential_breaking")
            severities.add("risk")
        nb += risk
    else:
        nr += risk
    if add_err:
        if additions:
            categories.add("addition")
        nb += additions
    else:
        ns += additions
    if qual_err:
        if quality:
            categories.add("quality_issues")
        nb += quality
    else:
        ns += quality
    return name, verdict, nb, nr, ns


def _release_contract_coverage_findings(
    report: dict[str, object],
) -> tuple[list[Finding], bool]:
    """Coarse analysis-incomplete finding(s) for a release (directory/
    package) report, from the release-level ``contract_coverage_failure_
    count``/``contract_coverage_exit_contribution`` ints (``cli_compare_
    release_helpers.py``'s aggregated ledger fold across every library) —
    the release schema's counterpart to :func:`_contract_coverage_findings`'s
    per-provider ``contract_coverage_failures`` list (Codex review,
    CLI-audit P2).

    Returns ``(findings, blocking)`` rather than a bare list: **the two ints
    answer different questions and must not be conflated** (Codex review,
    CLI-audit P2 follow-up). ``contract_coverage_failure_count`` (a plain sum,
    never zeroed) says whether a coverage gap exists at all — the signal a
    finding's *presence* is built from. ``contract_coverage_exit_contribution``
    (the ``max()``-folded 0/1 that actually reached the real exit code) says
    whether it was accepted by ``contract.unresolved: warn`` — a run so
    configured deliberately zeroes the contribution while the failures stay
    real and unsuppressible (AGENTS.md's contract_coverage_exit.py entry: "an
    acceptance of incomplete assurance, not a way to hide it"). Gating finding
    creation on the contribution alone (an earlier revision of this function)
    made a warn-accepted release-level gap invisible everywhere this report
    is read from — the same failure mode :func:`_contract_coverage_findings`
    never has, since it renders unconditionally on the single-pair report's
    always-present ``contract_coverage_failures`` array.

    Coarser than the single-pair version by necessity: the release JSON has
    no aggregated ``contract_coverage_failures`` array (only each library's
    own int fields), so this can name *which libraries* were affected but not
    *which provider* fell short within them — a caller wanting that detail
    still needs ``--format json``'s per-library section.

    Always ``([], False)`` when the run never passed ``--contract``
    — both keys are entirely absent then, mirroring the single-pair report's
    own "no key" (not "empty list") convention.
    """
    failure_count = report.get("contract_coverage_failure_count")
    if not isinstance(failure_count, int) or failure_count == 0:
        return [], False
    contribution = report.get("contract_coverage_exit_contribution")
    blocking = isinstance(contribution, int) and contribution != 0
    affected: list[str] = []
    libraries = report.get("libraries")
    if isinstance(libraries, list):
        for lib in libraries:
            if not isinstance(lib, dict):
                continue
            lib_count = lib.get("contract_coverage_failure_count")
            if isinstance(lib_count, int) and lib_count:
                affected.append(str(lib.get("library", "?")))
    detail = (
        f"Incomplete for: {', '.join(affected)}"
        if affected
        else "See --format json's per-library contract_coverage_failure_count for detail"
    )
    if not blocking:
        detail += " (accepted by contract.unresolved: warn)"
    return [
        Finding(
            kind="contract_coverage_failure",
            symbol="Contract evidence: release contract-coverage ledger",
            detail=detail,
        )
    ], blocking


def _from_release(
    report: dict[str, object], gate_api_break: bool = False
) -> CommentModel:
    """Build a release-mode (directory/package operand) :class:`CommentModel`.

    **Known gap, deliberately not closed here (Codex review):** unlike
    :func:`_from_compare`/:func:`_from_appcompat`, this never routes an
    evidence-coverage kind (``_EVIDENCE_KIND_VALUES``) into
    ``CommentModel.incomplete`` — a release whose only finding is e.g.
    ``layer_coverage_asymmetric`` still renders under the ordinary Needs
    review bucket. Closing this properly needs a data source this function
    doesn't have: the per-library aggregate counts below (``nb``/``nr``/
    ``ns``) come from authoritative integer fields
    (``breaking``/``source_breaks``/``risk_changes``/…), but the only
    itemized, kind-level view of a library's findings is
    ``cli_compare_release.py``'s capped, truncatable ``findings`` list (at
    most 10 total per library, no ``severity`` field) — building an
    incomplete-bucket count from that would be sample-based and could
    silently undercount for a library with many findings, unlike the
    exhaustive walk the other two modes do over the full, uncapped
    ``changes``/``relevant_changes`` list. A correct fix adds an
    authoritative per-category evidence-finding count to the release JSON
    schema itself — a `cli_compare_release.py` change, not a rendering-only
    fix confined to this module.

    **The orthogonal contract-coverage ledger (ADR-049 Phase 7) is not the
    same gap and is closed here** (Codex review, CLI-audit P2): unlike the
    per-library kind-level findings above, the release JSON *does* already
    carry authoritative ``contract_coverage_failure_count``/``contract_
    coverage_exit_contribution`` ints (both release-wide and per-library) —
    see :func:`_release_contract_coverage_findings`. Without this, a release
    whose only problem was incomplete contract coverage had ``incomplete ==
    []`` and zero changes, so the default ``--on=changes`` policy silently
    skipped (or deleted) the sticky comment, and ``--on=always`` rendered "No
    ABI changes" beside a release that had already failed its exit code —
    and, in a follow-up review round, an advisory (``contract.unresolved:
    warn``-accepted) coverage gap stayed invisible even after that fix, since
    ``warn`` deliberately zeroes the exit contribution the first fix gated
    finding-creation on. See that function's own docstring for why the two
    ints must be read separately.
    """
    rows: list[tuple[str, str, int, int, int]] = []
    levels = _severity_levels(report)
    categories: set[str] = set()
    severities: set[str] = set()
    libraries = report.get("libraries")
    if isinstance(libraries, list):
        for lib in libraries:
            if not isinstance(lib, dict):
                continue
            rows.append(
                _release_lib_row(lib, gate_api_break, levels, categories, severities)
            )
    n_libs = len(rows)
    _append_release_global_row(
        rows,
        "(bundle checks)",
        report.get("bundle_verdict"),
        report.get("bundle_findings"),
        gate_api_break,
        levels,
        categories,
        severities,
    )
    _append_release_global_row(
        rows,
        "(build-config matrix)",
        report.get("matrix_verdict"),
        report.get("matrix_findings"),
        gate_api_break,
        levels,
        categories,
        severities,
    )
    removed = report.get("unmatched_old")
    added = report.get("unmatched_new")
    incomplete, incomplete_blocking = _release_contract_coverage_findings(report)
    return CommentModel(
        mode="release",
        subject=f"{n_libs} librar{'y' if n_libs == 1 else 'ies'}",
        old_label=_basename(report.get("old_dir", "old")),
        new_label=_basename(report.get("new_dir", "new")),
        policy="strict_abi",
        library_rows=rows,
        incomplete=incomplete,
        # Blocking follows the release-level exit-code contribution, not
        # merely whether a finding exists — a `contract.unresolved: warn`
        # run still creates the finding (real, unsuppressible signal) but
        # deliberately zeroed the contribution, so it must render advisory,
        # not blocking (see `_release_contract_coverage_findings`).
        incomplete_blocking=incomplete_blocking,
        breaking_categories=frozenset(categories),
        breaking_severities=frozenset(severities),
        removed_libraries=[str(x) for x in removed]
        if isinstance(removed, list)
        else [],
        added_libraries=[str(x) for x in added] if isinstance(added, list) else [],
    )


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def build_model(
    report: dict[str, object],
    gate_api_break: bool = False,
    gate_breaking: bool = True,
) -> CommentModel:
    """Detect the report shape and normalise it into a :class:`CommentModel`.

    When *gate_api_break* is set (the action's ``fail-on-api-break``), API/source
    breaks are filed under Breaking so the comment matches the now-red check.
    *gate_breaking* mirrors ``fail-on-breaking`` (default ``True``, matching
    that Action input's own default) — only used today by the
    analysis-incomplete bucket's blocking derivation (see
    ``_incomplete_is_blocking``); the ordinary Breaking bucket's
    classification is a compatibility judgement, not a gate one (ADR-042),
    so it is unaffected by either flag.
    """
    if isinstance(report.get("libraries"), list):
        return _from_release(report, gate_api_break)
    if "application" in report or isinstance(report.get("relevant_changes"), list):
        return _from_appcompat(report, gate_api_break, gate_breaking)
    if "scan_schema_version" in report:
        return from_scan(report, gate_api_break, gate_breaking)
    return _from_compare(report, gate_api_break, gate_breaking)


def should_post(model: CommentModel, on: str) -> bool:
    """Whether a comment should be posted given the ``--on`` policy."""
    if on == "never":
        return False
    if on == "always":
        return True
    # on == "changes"
    return (
        model.total_changes > 0
        or bool(model.removed_libraries)
        or bool(model.added_libraries)
    )


# ---------------------------------------------------------------------------
# Rendering — CommentModel → markdown
# ---------------------------------------------------------------------------


def _md_url(url: str) -> str:
    """Percent-encode characters that would break a markdown ``(url)`` target."""
    return url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")


#: Scoped-verdict (--used-by/--required-symbol) headline per verdict string —
#: this is what the actual exit code reflects, so it takes priority over the
#: raw (unscoped) bucket counts, which stay informational context below.
_SCOPED_HEADER: dict[str, tuple[str, str]] = {
    "BREAKING": ("❌", "ABI BREAKING (scoped)"),
    "API_BREAK": ("⚠️", "API break (scoped)"),
    "COMPATIBLE": ("✅", "Compatible (scoped)"),
}


#: Reviewer-facing headline for a Breaking bucket whose members are *only*
#: policy-gated COMPATIBLE findings (a severity-config category promoted to
#: ``error``) — not a genuine ABI/API incompatibility. Keyed by the exact
#: frozenset of categories responsible; a bucket that also holds a genuine
#: ``abi_breaking``/``potential_breaking`` finding never reaches this table
#: (see ``_header``), since that finding *is* accurately a break.
_POLICY_ONLY_HEADER: dict[frozenset[str], tuple[str, str]] = {
    frozenset({"addition"}): ("⛔", "Public API expansion requires approval"),
    frozenset({"quality_issues"}): ("⛔", "Quality policy violation"),
    frozenset({"addition", "quality_issues"}): ("⛔", "Policy violation blocks this PR"),
}


def _header(model: CommentModel) -> tuple[str, str]:
    if (
        model.mode == "scan"
        and model.scan_audit_only
        and not model.breaking
        and not model.review
        and not model.has_incomplete
    ):
        # An audit-only `scan` (no `--against` baseline at all) ran no
        # comparison, so every compatibility bucket is necessarily empty —
        # the generic "✅ No ABI changes" wording below would misreport that
        # as "we compared and found nothing" rather than "there was nothing
        # to compare".
        return "✅", "Scan audit — no baseline to compare"
    if model.scoped_verdict is not None:
        # `contract_coverage_blocking` is checked FIRST, ahead of the scoped
        # header itself (Codex review, two rounds): the contract-coverage
        # axis folds into the real exit code unconditionally, on top of
        # *any* other verdict including a scoped one — a
        # --used-by/--required-symbol run reporting a scoped COMPATIBLE
        # verdict does not silence an orthogonal coverage failure that
        # already turned the real exit code non-zero. This must return
        # directly here, not merely skip the scoped-header return and fall
        # through to the rest of the function: under scoping, the *raw*
        # `b`/`r`/`s` bucket counts below are the full, unscoped library
        # diff (kept only as informational context, per this module's own
        # design) and are not part of the actual gate — falling through
        # would let an unrelated full-library break the scoped consumer
        # never even sees win the headline instead of correctly naming the
        # orthogonal coverage axis that is what's actually failing.
        if model.contract_coverage_blocking:
            return "🛑", "Source analysis incomplete"
        header = _SCOPED_HEADER.get(model.scoped_verdict)
        if header is not None:
            return header
    b, r, s = model.counts
    if model.removed_libraries:
        return "❌", "LIBRARY REMOVED"
    if b:
        # A finding is only accurately reported as "ABI BREAKING" when the
        # Breaking bucket holds a genuine abi_breaking/potential_breaking
        # finding — never infer that wording from bucket membership alone.
        # Severity-config promotion (ADR-042: compatibility and gate
        # decisions are separate axes) can populate Breaking with a
        # COMPATIBLE addition/quality finding that policy chose to block;
        # that is a policy violation, not an ABI/API break.
        cats = model.breaking_categories
        if "abi_breaking" in cats:
            return "❌", "ABI BREAKING"
        if "potential_breaking" in cats:
            # "potential_breaking" covers both "api_break" (a real source
            # break) and "risk" (a risk promoted to blocking) — they share a
            # severity-config knob but are not the same claim, so resolve
            # via the raw severities rather than wording every gated risk as
            # a "source API break" (Codex review, PR #595).
            sevs = model.breaking_severities
            if "api_break" in sevs:
                return "⛔", "Source API break blocks this PR"
            if "risk" in sevs:
                return "⛔", "Compatibility risk blocks this PR"
            return "⛔", "Source API break blocks this PR"
        policy_header = _POLICY_ONLY_HEADER.get(cats)
        if policy_header is not None:
            return policy_header
        # No per-category tracking for this bucket (shouldn't normally
        # happen — every mode populates breaking_categories) — fall back to
        # the conservative default rather than under-stating a red check.
        return "❌", "ABI BREAKING"
    if model.has_incomplete and model.incomplete_blocking:
        # A hard evidence-policy failure fails the run the same way a
        # genuine break does (ADR-033 D7 / a gated coverage risk), so it
        # takes the same headline priority as `b` above — ahead of a
        # separate, merely-advisory review finding. Say so explicitly rather
        # than folding it into the generic "Review recommended" wording,
        # which would read as "this PR changed the API," not "we couldn't
        # check this PR."
        return "🛑", "Source analysis incomplete"
    if r:
        # A real compatibility finding always keeps headline priority over a
        # merely-advisory coverage gap (Codex review) — an ungated
        # `layer_coverage_asymmetric` must not bury a genuine source-API
        # change behind "Analysis coverage reduced". `_incomplete_note`
        # still calls the coverage gap out separately.
        #
        # Name the actual reason instead of the generic "Review recommended"
        # whenever every review-bucket finding agrees on one severity — a
        # source-level API change (binary ABI unaffected) reads very
        # differently from a risk finding, and a reviewer shouldn't have to
        # open the section to tell which one this is.
        review_sevs = frozenset(f.severity for f in model.review if f.severity)
        if review_sevs == {"api_break"}:
            return "⚠️", "Source API changed; binary ABI unchanged"
        if review_sevs == {"risk"}:
            return "⚠️", "Compatibility risk — review recommended"
        return "⚠️", "Review recommended"
    if model.has_incomplete:
        return "⚠️", "Analysis coverage reduced"
    if s:
        return "✅", "No compatibility impact detected"
    return "✅", "No ABI changes"


def _strip_templates(s: str) -> str:
    """Drop balanced ``<...>`` template arguments (best-effort, for grouping)."""
    out: list[str] = []
    depth = 0
    for ch in s:
        if ch == "<":
            depth += 1
            continue
        if ch == ">":
            if depth > 0:
                depth -= 1
            continue
        if depth == 0:
            out.append(ch)
    return "".join(out)


def _api_group(symbol: str) -> str:
    """Enclosing API (namespace/type or free-function family) of a symbol.

    Strips template arguments and the parameter list, then drops the trailing
    ``::name`` so overloads, template instantiations and members of the same
    type/namespace collapse to one key. Free functions collapse their overloads
    to the bare name; distinct names stay distinct.
    """
    s = _strip_templates(symbol).strip()
    paren = s.find("(")
    if paren != -1:
        s = s[:paren].strip()
    if "::" in s:
        s = s.rsplit("::", 1)[0].strip()
    return s or symbol.strip()


def _group_by_api(findings: list[Finding]) -> OrderedDict[str, list[Finding]]:
    """Group findings by their enclosing API, preserving first-seen order."""
    groups: OrderedDict[str, list[Finding]] = OrderedDict()
    for f in findings:
        groups.setdefault(_api_group(f.symbol), []).append(f)
    return groups


def _flat_row(f: Finding) -> str:
    """Render one finding as a per-symbol table row.

    The Symbol column shows the demangled signature (`_demangle_symbol`)
    when one was recovered, with the raw mangled linker symbol kept as
    evidence in the Detail column — a maintainer thinks in the signature,
    not the mangled name, but the mangled form is still the real linker
    fact behind a binary-level finding. Full-detail rows also carry the
    report's own `impact` field (`change_registry.py`'s per-kind `impact=`
    template) when present, labelled **Impact:** — deliberately not
    "**Fix:**" (Codex review): an `impact=` entry is a free-form
    explanation of consequences, not a guaranteed repair step — e.g.
    `symbol_version_defined_removed`'s entry only says old binaries get a
    link error, `struct_size_changed`'s only confirms the layout break is
    visible at binary level — so labelling every one of them as a "fix"
    would misrepresent entries that carry no remediation at all.
    """
    loc = f" · `{_esc(f.location)}`" if f.location else ""
    cell = (_esc(f.detail) + loc) if f.detail else _esc(f.location or "—")
    if f.mangled:
        cell += f"<br>linker: `{_esc(f.mangled)}`"
    if f.impact:
        cell += f"<br>**Impact:** {_esc(f.impact)}"
    return f"| `{_esc(f.kind)}` | `{_esc(f.symbol)}` | {cell} |"


def _group_row(key: str, members: list[Finding]) -> str:
    """Render an API family as a single aggregated row (kinds, key, members)."""
    kinds = ", ".join(f"`{_esc(k)}`" for k in dict.fromkeys(m.kind for m in members))
    syms = [m.symbol for m in members]
    shown = syms[:_GROUP_MEMBERS_INLINE]
    more = f" +{len(syms) - _GROUP_MEMBERS_INLINE} more" if len(syms) > _GROUP_MEMBERS_INLINE else ""
    members_cell = ", ".join(f"`{_esc(x)}`" for x in shown) + more
    return f"| {kinds} | `{_esc(key)}` ({len(members)}) | {members_cell} |"


def _findings_table(
    title: str,
    findings: list[Finding],
    detail: str,
    *,
    open_default: bool,
    count: int | None = None,
) -> list[str]:
    # `count` overrides the header's displayed number when it can diverge
    # from `len(findings)` -- currently only the analysis-incomplete bucket,
    # whose exact total (`model.incomplete_total`) can exceed the itemized
    # list when the report cap truncated some or all of it (Codex review).
    # Every other caller leaves this `None` and gets `len(findings)`, same
    # as before.
    n = count if count is not None else len(findings)
    if n == 0:
        return []
    is_open = " open" if (detail == "full" or open_default) else ""
    out = [
        f"<details{is_open}><summary>{title} ({n})</summary>",
        "",
        "| Change | Symbol | Detail |",
        "|---|---|---|",
    ]
    if detail == "full":
        # Full detail keeps every change as its own per-symbol row (no rollup).
        out += [_flat_row(f) for f in findings]
        out += ["</details>", ""]
        return out
    # Standard: roll up by enclosing API so mass changes stay scannable —
    # singletons render as a normal per-symbol row, families aggregate.
    groups = _group_by_api(findings)
    keys = list(groups)
    for key in keys[:_STANDARD_ROW_CAP]:
        members = groups[key]
        out.append(_flat_row(members[0]) if len(members) == 1 else _group_row(key, members))
    if len(keys) > _STANDARD_ROW_CAP:
        out.append(f"| … | … | _{len(keys) - _STANDARD_ROW_CAP} more_ |")
    out += ["</details>", ""]
    return out


def _safe_section(findings: list[Finding], detail: str) -> list[str]:
    if not findings:
        return []
    is_open = " open" if detail == "full" else ""
    # "Safe" reads as an absolute guarantee it isn't — these are compatible
    # quality/behavioral findings (COMPATIBLE_KINDS minus additions, which
    # get their own "➕ Public API additions" section above), not a claim
    # that nothing here is worth a look.
    out = [
        f"<details{is_open}><summary>ℹ️ Informational findings ({len(findings)})</summary>",
        "",
    ]
    if detail == "full":
        out += ["| Change | Symbol | Detail |", "|---|---|---|"]
        for f in findings:
            out.append(f"| `{_esc(f.kind)}` | `{_esc(f.symbol)}` | {_esc(f.detail)} |")
    else:
        groups: OrderedDict[str, list[str]] = OrderedDict()
        for f in findings:
            groups.setdefault(f.kind, []).append(f.symbol)
        parts: list[str] = []
        for kind, syms in groups.items():
            shown = syms[:_SAFE_SYMBOLS_PER_KIND]
            more = (
                f" _(+{len(syms) - _SAFE_SYMBOLS_PER_KIND})_"
                if len(syms) > _SAFE_SYMBOLS_PER_KIND
                else ""
            )
            joined = ", ".join(f"`{_esc(x)}`" for x in shown)
            parts.append(f"`{_esc(kind)}`: {joined}{more}")
        out.append(" · ".join(parts))
    out += ["", "</details>", ""]
    return out


def _release_table(model: CommentModel, detail: str) -> list[str]:
    rows = model.library_rows
    if not rows:
        return []
    is_open = " open" if detail == "full" else ""
    ordered = sorted(rows, key=lambda r: (-r[2], -r[3], -r[4], r[0]))
    cap = None if detail == "full" else _STANDARD_ROW_CAP
    shown = ordered if cap is None else ordered[:cap]
    out = [
        f"<details{is_open}><summary>Per-library results ({len(rows)})</summary>",
        "",
        "| Library | Verdict | Breaking | Review | Safe |",
        "|---|---|---|---|---|",
    ]
    for name, verdict, nb, nr, ns in shown:
        em = _VERDICT_EMOJI.get(verdict, "•")
        out.append(f"| `{_esc(name)}` | {em} {_esc(verdict)} | {nb} | {nr} | {ns} |")
    if cap is not None and len(ordered) > cap:
        out.append(f"| … | … | | | _{len(ordered) - cap} more_ |")
    out += ["</details>", ""]
    return out


def _header_block(model: CommentModel, short_sha: str) -> list[str]:
    emoji, title = _header(model)
    b, r, s = model.counts
    head_ref = f"**Head `{short_sha}`**" if short_sha else "**Head**"
    # Codex review: `model.subject` can come straight from an untrusted
    # scanned-artifact basename (the Action's `run.sh` passes it through
    # `--subject`) -- a crafted filename containing a backtick or newline
    # could terminate this code span and inject arbitrary Markdown into the
    # sticky comment otherwise. `_esc` (used everywhere else a value is
    # rendered inside a code span) neutralizes both.
    context = (
        f"{head_ref} vs `{_esc(model.old_label)}` · `{_esc(model.policy)}` · "
        f"`{_esc(model.subject)}`"
    )
    counts_line = f"**{b} breaking** · {r} needs review · {s} safe"
    # The incomplete count is a distinct axis (analysis quality, not
    # compatibility — see module docstring) and only shown when non-zero, so
    # every existing report's summary line is unchanged.
    if model.has_incomplete:
        counts_line += f" · {model.incomplete_total} analysis incomplete"
    return [
        MARKER,
        "",
        f"## {emoji} abicheck — {title}",
        "",
        context,
        "",
        counts_line,
        "",
    ]


def _library_notes(model: CommentModel) -> list[str]:
    out: list[str] = []
    if model.removed_libraries:
        listed = ", ".join(f"`{_esc(x)}`" for x in model.removed_libraries)
        out += [f"> ⛔ Libraries removed: {listed}", ""]
    if model.added_libraries:
        listed = ", ".join(f"`{_esc(x)}`" for x in model.added_libraries)
        out += [f"> ➕ New libraries: {listed}", ""]
    return out


def _suppression_note(model: CommentModel) -> list[str]:
    """"Reporting must survive suppression": a reviewer must see *that*
    findings were withheld/reclassified, not just the post-suppression
    buckets above (which, for a fully-suppressed diff, could otherwise read
    as "no ABI changes at all")."""
    parts: list[str] = []
    if model.suppressed_count:
        n = model.suppressed_count
        parts.append(f"🔇 {n} finding{'s' if n != 1 else ''} suppressed by `--suppress`")
    if model.reclassified_count:
        n = model.reclassified_count
        parts.append(
            f"🔀 {n} finding{'s' if n != 1 else ''} reclassified by `--policy-file`"
        )
    if not parts:
        return []
    return [f"> ℹ️ {' · '.join(parts)} — see the full JSON report for details.", ""]


def _scoped_notes(model: CommentModel) -> list[str]:
    """`compare --used-by`/`--required-symbol(s)` scoping banner + summary.

    States which verdict the exit code actually reflects whenever it disagrees
    with the full-library breaking/review/safe buckets rendered below, then
    lists each app's/contract's own scoped result (Codex review) — otherwise a
    reviewer sees only the alarming full-library findings with no indication
    the gate is scoped and currently passing (or vice versa).
    """
    if model.scoped_verdict is None:
        return []
    out: list[str] = []
    if model.full_verdict is not None and model.full_verdict != model.scoped_verdict:
        out += [
            f"> ℹ️ **Scoped verdict: {model.scoped_verdict}** — this is what the "
            f"exit code reflects. The full library (all changes below) is "
            f"`{model.full_verdict}`.",
            "",
        ]
    for app in model.used_by_summaries:
        missing_symbols = app.get("missing_symbols")
        n_missing = len(missing_symbols) if isinstance(missing_symbols, list) else 0
        out.append(
            f"- `--used-by {_esc(app.get('app'))}`: **{_esc(app.get('verdict'))}** "
            f"(missing {n_missing} symbol(s), "
            f"{app.get('relevant_change_count', 0)} relevant change(s))"
        )
    if model.required_symbol_summary is not None:
        rs = model.required_symbol_summary
        missing_entrypoints = rs.get("missing_entrypoints")
        n_missing_ep = (
            len(missing_entrypoints) if isinstance(missing_entrypoints, list) else 0
        )
        out.append(
            f"- `--required-symbol` contract: **{_esc(rs.get('verdict'))}** "
            f"(missing {n_missing_ep} "
            f"entrypoint(s), {rs.get('relevant_change_count', 0)} relevant change(s))"
        )
    if out:
        out.append("")
    return out


#: Human-readable label for a severity-config category, used in policy-block
#: messaging ("addition"/"quality_issues" are the only categories that can
#: populate a *policy-only* Breaking bucket — see `_POLICY_ONLY_HEADER`).
_CATEGORY_LABEL = {"addition": "addition", "quality_issues": "quality"}


def _gate_note(model: CommentModel) -> list[str]:
    """Explain a policy-only block (ADR-042): compatibility and gate
    decisions are separate axes, so a COMPATIBLE addition/quality finding
    can still fail the check under a strict severity config. Rendered only
    when the Breaking bucket holds no genuine incompatibility, so a
    reviewer isn't left thinking ABI/API compatibility itself is broken.
    """
    if model.removed_libraries or model.scoped_verdict is not None:
        return []
    b, r, _ = model.counts
    cats = model.breaking_categories
    if not b or "abi_breaking" in cats or "potential_breaking" in cats or not cats:
        return []
    names = ", ".join(f"`{_CATEGORY_LABEL.get(c, c)}`" for c in sorted(cats))
    if r:
        # A separate, ungated api_break/risk finding sits in "Needs review" —
        # asserting whole-report "Compatibility: COMPATIBLE" here would
        # overstate it (Codex review, PR #595), so scope the claim to just
        # the Breaking bucket's own entries and point at the other section.
        return [
            f"> ℹ️ **Gate: BLOCKED** by severity policy — {names} is "
            f"configured as `error`. These entries are themselves COMPATIBLE "
            f"(not an ABI/API break) — see \"Needs review\" below for other "
            f"findings that may affect compatibility.",
            "",
        ]
    return [
        f"> ℹ️ **Compatibility: COMPATIBLE** — existing binaries/consumers are "
        f"unaffected; this is not an ABI/API break. **Gate: BLOCKED** by "
        f"severity policy — {names} is configured as `error`.",
        "",
    ]


def _incomplete_findings_for_table(model: CommentModel) -> list[Finding]:
    """`model.incomplete`, or -- when the report cap truncated *every*
    analysis-incomplete finding, leaving the itemized list empty even though
    `model.incomplete_total` is exact and positive (Codex review) -- one
    synthetic placeholder row, so `_findings_table` (called with
    ``count=model.incomplete_total``) still renders a section instead of
    silently vanishing next to a truncation note claiming the counts above
    are exact.
    """
    if model.incomplete or model.incomplete_total <= 0:
        return model.incomplete
    n = model.incomplete_total
    word = "finding" if n == 1 else "findings"
    return [
        Finding(
            kind="",
            symbol="(truncated)",
            detail=(
                f"{n} analysis-incomplete {word} were cut by the report cap "
                "before any could be itemized; see the full JSON report for "
                "detail."
            ),
        )
    ]


def _incomplete_note(model: CommentModel) -> list[str]:
    """Explain the analysis-incomplete bucket when it did *not* win the
    headline — a genuine breaking finding, or (for a merely-advisory
    coverage gap) a real review finding, took priority instead (see
    `_header`) — so a reviewer looking at an "ABI BREAKING" or "Review
    recommended" headline still learns the analysis itself was also
    degraded, rather than discovering it only in the collapsed details
    section below.
    """
    if not model.has_incomplete:
        return []
    if not model.breaking and not (model.review and not model.incomplete_blocking):
        return []
    n = model.incomplete_total
    word = "finding" if n == 1 else "findings"
    return [
        f"> 🛑 {n} analysis-coverage {word} below — some real changes may not "
        f"be detectable with the evidence this comparison had available.",
        "",
    ]


def _body_sections(model: CommentModel, detail: str) -> list[str]:
    if model.mode == "release":
        # Codex review (CLI-audit P2 follow-up): the release path's early
        # return used to skip `model.incomplete` entirely — the headline and
        # `_header_block`'s "· N analysis incomplete" count already surfaced
        # a release-level contract-coverage gap (see `_release_contract_
        # coverage_findings`), but the actual `Finding.detail` naming which
        # libraries were affected was unreachable in the rendered body, full
        # detail included. Same table compare mode uses for its own
        # incomplete bucket, appended after the per-library results table.
        return _release_table(model, detail) + _findings_table(
            "🛑 Analysis incomplete",
            _incomplete_findings_for_table(model),
            detail,
            open_default=model.has_incomplete,
            count=model.incomplete_total,
        )
    cats = model.breaking_categories
    breaking_title = (
        "❌ Breaking"
        if (not cats or "abi_breaking" in cats or "potential_breaking" in cats)
        else "⛔ Blocked by policy (compatible)"
    )
    out: list[str] = []
    if model.mode == "scan":
        out += scan_note(model)
    out += _findings_table(
        breaking_title, model.breaking, detail, open_default=bool(model.breaking)
    )
    out += _findings_table(
        "🛑 Analysis incomplete",
        _incomplete_findings_for_table(model),
        detail,
        open_default=(not model.breaking and model.has_incomplete),
        count=model.incomplete_total,
    )
    out += _findings_table(
        "⚠️ Needs review",
        model.review,
        detail,
        open_default=(not model.breaking and bool(model.review)),
    )
    # New public-API surface gets its own section — a per-symbol table with
    # kind/detail/location, the same treatment Breaking/Needs review get —
    # rather than being folded anonymously into the generic quality-findings
    # "Safe" list below. A reviewer approving new exports wants to see what
    # was added, not just a bare symbol count.
    additions = [f for f in model.safe if f.category == "addition"]
    quality = [f for f in model.safe if f.category != "addition"]
    out += _findings_table(
        "➕ Public API additions", additions, detail, open_default=False
    )
    out += _safe_section(quality, detail)
    return out


def _footer_block(
    ts: datetime, run_label: str | None, short_sha: str, report_url: str | None = None
) -> list[str]:
    footer = f"<sub>Updated {ts.strftime('%Y-%m-%d %H:%M UTC')}"
    if run_label:
        footer += f" · {run_label}"
    if short_sha:
        footer += f" · commit {short_sha}"
    if report_url:
        footer += f" · [full report]({_md_url(report_url)})"
    footer += "</sub>"
    return [footer, ""]


def _render_body(
    model: CommentModel,
    short_sha: str,
    ts: datetime,
    detail: str,
    run_label: str | None,
    report_url: str | None,
    *,
    condensed: bool,
) -> str:
    """Render the comment body at one detail level (optionally condensed)."""
    lines = _header_block(model, short_sha)
    if condensed:
        note = "> ℹ️ _Condensed to fit GitHub's comment size limit"
        note += f" — see the [full report]({_md_url(report_url)})._" if report_url else "._"
        lines += [note, ""]
    lines += _library_notes(model)
    lines += _gate_note(model)
    lines += _incomplete_note(model)
    lines += _scoped_notes(model)
    lines += _suppression_note(model)
    if detail != "summary":
        lines += _body_sections(model, detail)
    lines += _footer_block(ts, run_label, short_sha, report_url)
    return "\n".join(lines)


def _truncate_to_budget(body: str, report_url: str | None) -> str:
    """Hard-cut an over-budget body, appending a truncation note + report link."""
    suffix = "\n\n<sub>… comment truncated to fit GitHub's size limit"
    suffix += (
        f" — see the [full report]({_md_url(report_url)}).</sub>"
        if report_url
        else ".</sub>"
    )
    return body[: max(_BODY_BUDGET - len(suffix), 0)] + suffix


def render_comment(
    model: CommentModel,
    *,
    sha: str = "",
    detail: str = "standard",
    run_label: str | None = None,
    timestamp: datetime | None = None,
    report_url: str | None = None,
) -> str:
    """Render the full sticky-comment markdown body (including :data:`MARKER`).

    The body is kept under GitHub's 65,536-character comment limit: if the
    requested detail overflows, the detail level is downgraded
    (full → standard → summary) and, as a last resort, the body is truncated —
    always pointing at the full report when *report_url* is supplied.
    """
    if detail not in DETAIL_LEVELS:
        detail = "standard"
    ts = timestamp or datetime.now(timezone.utc)
    short_sha = (sha or "")[:7]
    body = ""
    for i, level in enumerate(_DETAIL_DOWNGRADE[detail]):
        body = _render_body(
            model, short_sha, ts, level, run_label, report_url, condensed=(i > 0)
        )
        if len(body) <= _BODY_BUDGET:
            return body
    return _truncate_to_budget(body, report_url)
