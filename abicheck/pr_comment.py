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
    _evidence_symbol_label,
    _finding_category,
    _normalize_location,
    _severity_levels,
)

# `_EVIDENCE_KIND_VALUES`/`_evidence_symbol_label` (the "analysis incomplete"
# bucket's kind classification -- see the module docstring above) now live
# in `pr_comment_base.py`, shared with `pr_comment_scan.py`'s own finding
# classification (Codex review, follow-up: a `scan --against` diff can carry
# the identical evidence-quality kinds through `diff.findings`, and routing
# them through the generic compatibility-severity buckets there produced a
# misleading "Compatibility risk" headline for what is really a
# missing-evidence signal).
# `MARKER`/`DETAIL_LEVELS`/`GITHUB_COMMENT_LIMIT`/`render_comment`/`_header`
# and their siblings now live in `pr_comment_render.py` (the "Rendering --
# CommentModel -> markdown" half this module's own former section divider
# already marked) -- re-exported below for this module's existing external
# callers (`cli_pr_comment.py`, several `tests/test_pr_comment*.py` modules).
# A static, one-directional import: `pr_comment_render.py` needs nothing
# from this module, so re-exporting here creates no cycle.
from .pr_comment_render import (
    DETAIL_LEVELS as DETAIL_LEVELS,
    GITHUB_COMMENT_LIMIT as GITHUB_COMMENT_LIMIT,
    MARKER as MARKER,
    _header as _header,
    render_comment as render_comment,
)
from .pr_comment_scan import from_scan

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


def _disposition_audit(report: dict[str, object]) -> dict[str, object] | None:
    """ADR-067 D3's ``disposition_audit`` block, or ``None`` when absent.

    Absent means the report predates schema 2.50 (or is a shape that carries
    no comparison at all) -- not that nothing was disposed of, which is why
    the renderer omits the row entirely rather than printing zeros.
    """
    audit = report.get("disposition_audit")
    return audit if isinstance(audit, dict) else None


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
        disposition_audit=_disposition_audit(report),
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
                if isinstance(f, dict)
                and str(f.get("kind", "")) in _ADDITION_KIND_VALUES
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
