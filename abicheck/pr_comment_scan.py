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

"""``scan``-report support for :mod:`abicheck.pr_comment` (split out to stay
under that module's 2000-line hard cap, the same "sibling `_<name>.py`"
pattern ``cli_scan.py``/``cli_scan_baseline.py`` already use).

``scan``'s own JSON (``scan_engine.ScanOutcome.to_dict``) has a materially
different top-level shape than ``compare``'s: when a baseline was given
(``scan --against``), the comparison findings live under
``report["diff"]["findings"]``/``report["diff"]["additions"]`` (a ``bucket``
field instead of ``severity``) rather than a flat ``changes`` list. This
module adapts that shape into the same :class:`~abicheck.pr_comment.CommentModel`
`compare`/`appcompat`/`release` reports build, reusing
:mod:`abicheck.pr_comment`'s severity/category/gate classification
(``_SEVERITY_BUCKET``/``_finding_category``) rather than duplicating it, so a
finding renders identically whichever command produced it.

Imports only from the dependency-free leaf modules ``pr_comment_base`` and ``pr_comment_scan_abort`` (never from ``pr_comment`` itself, which would form a real import cycle -- see ``pr_comment_base``'s own module docstring). ``pr_comment`` imports ``from_scan``/``scan_note`` from here inside function bodies (``build_model``/``_body_sections``) purely for readability at the call site, not to break a cycle -- there is none, since the dependency runs one way (``pr_comment`` -> ``pr_comment_scan`` -> ``pr_comment_base``).
"""

from __future__ import annotations

from .demangle import demangle_batch
from .pr_comment_base import (
    _EVIDENCE_KIND_DEFAULT_BUCKET,
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
from .pr_comment_scan_abort import scan_abort_incomplete_reason

#: `scan`'s finding dicts (`cli_scan_baseline._baseline_finding_dicts`) carry
#: a `bucket` field ("breaking"/"api_break"/"risk"/"compatible") instead of
#: `compare`'s `severity` label -- this maps one to the other so the rest of
#: the classification pipeline (`_SEVERITY_BUCKET`/`_finding_category`) is
#: shared between the two report shapes rather than duplicated.
_SCAN_BUCKET_TO_SEVERITY = {
    "breaking": "breaking",
    "api_break": "api_break",
    "risk": "risk",
    "compatible": "compatible",
}


def _scan_findings_to_buckets(
    findings_raw: object,
    demangled_map: dict[str, str],
    gate_api_break: bool,
    levels: dict[str, str],
) -> tuple[list[Finding], list[Finding], list[Finding]]:
    """Classify ``diff["findings"]`` (the gating buckets) into (breaking,
    review, incomplete).

    Mirrors ``_bucket_changes``'s severity/category/gate logic, adapted to
    scan's ``bucket``-keyed finding dicts. ``not_evaluated`` entries (ADR-049
    D9: contract relevance excluded them from compatibility scoring
    entirely) and ``suppressed`` entries (reported separately, never inside
    ``findings``) are skipped -- neither is a compatibility claim, so
    neither belongs in any bucket.

    An evidence-quality kind (``_EVIDENCE_KIND_VALUES``, shared with
    ``compare``'s own ``_bucket_changes``) is pulled out into ``incomplete``
    before the severity/category/gate logic below even runs -- exactly
    mirroring ``compare``'s own ordering (Codex review, follow-up): these
    kinds describe the *comparison's own evidence coverage*, not a detected
    API/ABI change, so routing them through the generic compatibility
    buckets produced a misleading "Compatibility risk blocks this PR"
    headline for what is really a missing-evidence signal. This doesn't
    change either exact header total (``scan_breaking_total``/
    ``scan_review_total`` are derived from ``diff``'s own scalar counts, not
    from these classified lists' lengths -- see ``_scan_true_counts``), only
    which section a reader sees the finding rendered under.
    """
    breaking: list[Finding] = []
    review: list[Finding] = []
    incomplete: list[Finding] = []
    target = {"breaking": breaking, "review": review}
    if not isinstance(findings_raw, list):
        return breaking, review, incomplete
    for c in findings_raw:
        if not isinstance(c, dict):
            continue
        raw_bucket = str(c.get("bucket", ""))
        sev = _SCAN_BUCKET_TO_SEVERITY.get(raw_bucket)
        if sev is None:
            # "not_evaluated" / "suppressed" / anything unrecognized.
            continue
        kind = str(c.get("kind", ""))
        if kind in _EVIDENCE_KIND_VALUES:
            loc = c.get("source_location")
            symbol = _evidence_symbol_label(kind, str(c.get("symbol", "")))
            incomplete.append(
                Finding(
                    kind=kind,
                    symbol=symbol,
                    detail=str(c.get("description", "") or ""),
                    location=_normalize_location(str(loc)) if loc else None,
                    severity=sev,
                )
            )
            continue
        bucket = _SEVERITY_BUCKET.get(sev, "review")
        # `fail-on-api-break`'s fixed api_break -> breaking mapping only
        # holds under the *legacy* exit-code scheme (`levels == {}`) -- under
        # the severity-aware scheme, `action/run.sh`'s ADVISORY_BREAK keeps
        # the run's real exit at 0 whenever `potential_breaking` is left at
        # its non-error default, regardless of `fail-on-api-break`, so
        # promoting the finding here unconditionally rendered a "Source API
        # break blocks this PR" headline beside an actually-green check
        # (Codex review). The category check right below already covers the
        # severity-aware case correctly (unconditional on the gate flag,
        # exactly mirroring `compare`'s own `_bucket_changes`).
        if gate_api_break and not levels and sev == "api_break":
            bucket = "breaking"
        category = _finding_category(sev, kind)
        if levels.get(category) == "error":
            bucket = "breaking"
        if bucket == "safe":
            # `sev == "compatible"` only ever reaches `findings` (as opposed
            # to the always-on `additions` list below) when severity policy
            # already promoted it to blocking -- see
            # `cli_scan_baseline._add_severity_blocking_compatible_findings`.
            # An unpromoted compatible entry can't appear here at all, so
            # this branch is unreachable in practice; skip defensively
            # rather than silently dropping a real bucket-target mismatch.
            continue
        loc = c.get("source_location")
        display_symbol, mangled_evidence = _demangle_symbol(
            str(c.get("symbol", "")), demangled_map
        )
        target[bucket].append(
            Finding(
                kind=kind,
                symbol=display_symbol,
                detail=str(c.get("description", "") or ""),
                location=_normalize_location(str(loc)) if loc else None,
                category=category,
                severity=sev,
                mangled=mangled_evidence,
            )
        )
    return breaking, review, incomplete


def _scan_compatible_list_to_safe(
    raw: object,
    demangled_map: dict[str, str],
    *,
    category: str,
    promoted: bool = False,
) -> tuple[list[Finding], list[Finding]]:
    """Classify one of the two always-on compatible-finding lists (schema
    1.13+'s ``diff["additions"]`` or ``diff["quality"]``) into
    ``(safe, incomplete)`` -- Safe-bucket ``Finding``s tagged with the given
    *category*, and evidence-quality entries (``_EVIDENCE_KIND_VALUES``,
    most notably ``dwarf_info_missing`` -- COMPATIBLE by default severity,
    so it lands in ``diff.quality``, never ``diff.findings``) pulled out
    into ``incomplete`` instead (Codex review, follow-up: an unconditional
    append here rendered a scan with missing DWARF info as a green "No
    compatibility impact detected" comment rather than surfacing the
    coverage gap). Shared by :func:`_scan_additions_to_safe` and
    :func:`_scan_quality_to_safe` (the two lists have an identical shape,
    differing only in which ``diff.compatible`` subset they itemize and
    which category the safe result renders under).

    ``promoted`` (``levels.get(category-equivalent) == "error"``, the
    severity-config promotion that moves *every* finding in this category to
    Breaking) short-circuits to ``([], [])``: once that promotion is in
    effect, every entry in *raw* is inherently blocking, so none belongs in
    the green section at all (Codex review, follow-up to an earlier
    ID-based exclusion: ``diff["findings"]`` is independently capped from
    this list, so a promoted entry dropped by the findings-list cap was
    never in the exclusion set and still rendered green even though the
    exact header total already correctly counted it as blocking).
    """
    if promoted:
        return [], []
    safe: list[Finding] = []
    incomplete: list[Finding] = []
    if not isinstance(raw, list):
        return safe, incomplete
    for c in raw:
        if not isinstance(c, dict):
            continue
        kind = str(c.get("kind", ""))
        loc = c.get("source_location")
        if kind in _EVIDENCE_KIND_VALUES:
            symbol = _evidence_symbol_label(kind, str(c.get("symbol", "")))
            incomplete.append(
                Finding(
                    kind=kind,
                    symbol=symbol,
                    detail=str(c.get("description", "") or ""),
                    location=_normalize_location(str(loc)) if loc else None,
                    severity="compatible",
                )
            )
            continue
        display_symbol, mangled_evidence = _demangle_symbol(
            str(c.get("symbol", "")), demangled_map
        )
        safe.append(
            Finding(
                kind=kind,
                symbol=display_symbol,
                detail=str(c.get("description", "") or ""),
                location=_normalize_location(str(loc)) if loc else None,
                category=category,
                severity="compatible",
                mangled=mangled_evidence,
            )
        )
    return safe, incomplete


def _scan_additions_to_safe(
    additions_raw: object,
    demangled_map: dict[str, str],
    addition_promoted: bool = False,
) -> tuple[list[Finding], list[Finding]]:
    """Classify the always-on ``diff["additions"]`` list (schema 1.13+) into
    ``(safe, incomplete)`` -- the Safe-bucket ``Finding``s (each tagged
    ``category="addition"`` so they render in the same "➕ Public API
    additions" section `compare` uses) and any evidence-quality entries
    pulled out instead (see :func:`_scan_compatible_list_to_safe`).
    """
    return _scan_compatible_list_to_safe(
        additions_raw, demangled_map, category="addition", promoted=addition_promoted
    )


def _scan_quality_to_safe(
    quality_raw: object,
    demangled_map: dict[str, str],
    quality_promoted: bool = False,
) -> tuple[list[Finding], list[Finding]]:
    """Classify the always-on ``diff["quality"]`` list (schema 1.13+) --
    :func:`~abicheck.cli_scan_baseline._quality_finding_dicts`'s
    complementary itemization of the compatible-but-non-addition subset of
    ``diff.compatible`` -- into ``(safe, incomplete)``: Safe-bucket
    ``Finding``s tagged ``category="quality_issues"``, and evidence-quality
    entries (most notably ``dwarf_info_missing``) pulled out instead (see
    :func:`_scan_compatible_list_to_safe`).

    Codex review, follow-up to the exact-safe-total fix: that fix corrected
    ``scan_safe_total`` to read the full ``diff.compatible`` scalar, but a
    scan whose only compatible findings were quality-shaped (e.g. a
    ``func_noexcept_added`` compatibility improvement, or a policy-demoted
    removal) still had nothing itemized for the reader to actually review --
    the header could say "3 safe" next to an empty green section.
    """
    return _scan_compatible_list_to_safe(
        quality_raw, demangled_map, category="quality_issues", promoted=quality_promoted
    )


def _scan_coverage_lines(report: dict[str, object]) -> list[str]:
    """Short per-layer coverage summary lines from ``report["coverage"]``
    (a list of ``LayerCoverage.to_dict()`` rows) -- e.g.
    ``"`pattern_scan`: present"``. Only layers that actually ran or were
    explicitly skipped are worth naming; an entry missing ``layer`` is
    dropped rather than guessed at.
    """
    coverage = report.get("coverage")
    lines: list[str] = []
    if not isinstance(coverage, list):
        return lines
    for entry in coverage:
        if not isinstance(entry, dict):
            continue
        layer = entry.get("layer")
        if not layer:
            continue
        status = str(entry.get("status", "") or "")
        lines.append(f"`{_esc(layer)}`: {_esc(status)}" if status else f"`{_esc(layer)}`")
    return lines


def _suppressed_count_scan(diff: dict[str, object] | None) -> int:
    """``scan``'s own suppression count (``diff["suppressed_count"]``) --
    mirrors ``pr_comment._suppressed_count``, adapted to scan's flat ``diff``
    block rather than ``compare``'s nested ``suppression`` object."""
    if not isinstance(diff, dict):
        return 0
    count = diff.get("suppressed_count")
    return count if isinstance(count, int) else 0


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _scan_findings_evidence_kind_counts(
    findings_raw: object, diff: dict[str, object] | None
) -> dict[str, int]:
    """Exact count of evidence-quality findings (``_EVIDENCE_KIND_VALUES``)
    within ``diff["findings"]``'s full (itemized + cap-truncated) scope,
    bucketed by ``"breaking"``/``"api_break"``/``"risk"`` (Codex review,
    follow-up to the evidence-routing fix: pulling these findings out of the
    classified ``breaking``/``review`` *lists* left the *exact scalar*
    totals -- which read ``diff["breaking"]``/``diff["api_break"]``/
    ``diff["risk"]`` directly -- still counting them, so a diff whose only
    finding was, say, a lone ``source_fact_coverage_incomplete`` produced a
    "0 breaking · 1 needs review" header with an empty itemized Review
    section).

    The itemized portion uses each finding dict's own real ``bucket`` field.
    ``diff["findings_truncated_kinds"]`` (``cli_scan_baseline.
    _accumulate_kind_counts``) is a kind -> cut-count ledger with no bucket
    of its own, so the truncated portion is attributed to that kind's fixed
    default bucket (``_EVIDENCE_KIND_DEFAULT_BUCKET``).

    Known gap, not fixed here (Codex review, follow-up): if a maintainer's
    policy file ``overrides:`` one of these four specific kinds to a
    *different* bucket than its registry default, a *truncated* (cap-cut)
    occurrence is still attributed to the old default bucket here, since
    ``findings_truncated_kinds`` carries no per-instance resolved-bucket
    information to read instead -- subtracting from the wrong scalar in
    that narrow, compounding edge case (truncation *and* an unusual
    evidence-kind override both present at once). A correct fix needs
    ``cli_scan_baseline.py`` to record the truncated ledger keyed by
    resolved bucket, not kind alone -- a schema addition (new field, version
    bump, doc/test updates), not a same-round extension of this function.
    The itemized portion is unaffected (it already reads each finding's own
    real, already-resolved ``bucket``).
    """
    counts = {"breaking": 0, "api_break": 0, "risk": 0}
    if isinstance(findings_raw, list):
        for c in findings_raw:
            if not isinstance(c, dict):
                continue
            kind = str(c.get("kind", ""))
            if kind not in _EVIDENCE_KIND_VALUES:
                continue
            bucket = str(c.get("bucket", ""))
            if bucket in counts:
                counts[bucket] += 1
    truncated_kinds = diff.get("findings_truncated_kinds") if diff else None
    if isinstance(truncated_kinds, dict):
        for kind, cut in truncated_kinds.items():
            if kind not in _EVIDENCE_KIND_VALUES or not isinstance(cut, int):
                continue
            default_bucket = _EVIDENCE_KIND_DEFAULT_BUCKET.get(kind, "")
            if default_bucket in counts:
                counts[default_bucket] += cut
    return counts


def _scan_findings_evidence_truncated_extra(diff: dict[str, object] | None) -> int:
    """Evidence-quality occurrences cut from ``diff["findings"]`` by the
    report cap (``diff["findings_truncated_kinds"]``), summed across every
    bucket -- the exact delta between ``model.incomplete``'s own length
    (itemized, capped, like every other classified list) and the true count
    of evidence-quality findings the comparison actually produced (Codex
    review: a diff with 25 `source_fact_coverage_incomplete` findings capped
    at 20 rendered "20 analysis incomplete" next to a truncation note
    claiming the header counts were exact). Reuses the same ledger
    ``_scan_findings_evidence_kind_counts`` already reads for the breaking/
    review scalar exclusion, just without attributing it to a bucket.
    """
    truncated_kinds = diff.get("findings_truncated_kinds") if diff else None
    if not isinstance(truncated_kinds, dict):
        return 0
    return sum(
        cut
        for kind, cut in truncated_kinds.items()
        if kind in _EVIDENCE_KIND_VALUES and isinstance(cut, int)
    )


def _scan_evidence_incomplete_blocking(
    evidence_counts: dict[str, int],
    gate_api_break: bool,
    gate_breaking: bool,
    levels: dict[str, str],
) -> bool:
    """Whether the evidence-quality findings pulled into ``incomplete``
    (:func:`_scan_findings_evidence_kind_counts`) actually gated this run's
    exit code -- mirroring ``compare``'s own ``_incomplete_is_blocking``
    folding rules (Codex review, follow-up), scoped to scan's simpler
    raw-scalar promotion model (the same one :func:`_scan_true_counts`
    already applies):

    Unlike ``compare``, scan's ``action/run.sh`` gate has an *extra*
    unconditional block for the ``BREAKING``/``API_BREAK`` tiers
    (``_severity_gate_categories``/``_sev_cats_real``, "an explicitly
    configured severity block is not subject to a compatibility flag"): once
    *any* category is genuinely configured ``error`` and that's what
    produced the exit, the scan step fails regardless of
    ``fail-on-breaking``/``fail-on-api-break`` -- compare has no such block,
    which is why its own ``_incomplete_is_blocking`` correctly requires
    *both* the category and the matching ``fail-on-*`` flag. So, under the
    severity-aware scheme, each branch below blocks on the resolved category
    level *alone*:

    - a ``"breaking"``-bucket occurrence (none of the four evidence kinds
      default here today, but a policy override could reclassify one)
      blocks under ``gate_breaking`` alone under the *legacy* exit-code
      scheme; under the severity-aware scheme it blocks whenever
      ``abi_breaking`` is configured ``error``, independent of
      ``gate_breaking`` (Codex review, follow-up: an earlier revision
      applied ``gate_breaking`` unconditionally of scheme/level, which
      claimed a demoted ``abi_breaking: warning`` finding was blocking an
      advisory exit-0 run, and separately failed to claim an
      ``abi_breaking: error`` + ``fail-on-breaking: false`` finding that
      scan's own unconditional severity-category block does still fail the
      step on);
    - an ``"api_break"``/``"risk"``-bucket occurrence blocks under a
      ``potential_breaking: error`` severity promotion (folds both,
      exactly like :func:`_scan_true_counts`'s own promotion, and likewise
      independent of ``gate_api_break`` for the same reason above), or,
      under the *legacy* exit-code scheme only, api_break alone under
      ``--gate-api-break``/``fail-on-api-break`` (Codex review, follow-up:
      the severity-aware scheme's ``action/run.sh`` ADVISORY_BREAK keeps the
      real exit at 0 whenever ``potential_breaking`` is left at its
      non-error default, regardless of ``fail-on-api-break`` -- an earlier
      revision applied the legacy fixed api_break->exit-2 mapping
      unconditionally, which claimed a promoted evidence-quality finding
      was blocking a run that was, in truth, exiting 0).
    """
    if levels:
        if levels.get("abi_breaking") == "error" and evidence_counts.get("breaking", 0) > 0:
            return True
    elif gate_breaking and evidence_counts.get("breaking", 0) > 0:
        return True
    if levels.get("potential_breaking") == "error":
        return (evidence_counts.get("api_break", 0) + evidence_counts.get("risk", 0)) > 0
    if gate_api_break and not levels:
        return evidence_counts.get("api_break", 0) > 0
    return False


def _scan_true_counts(
    diff: dict[str, object] | None,
    gate_api_break: bool,
    levels: dict[str, str],
    evidence_counts: dict[str, int] | None = None,
) -> tuple[int, int] | None:
    """Exact (breaking, review) totals from ``diff``'s own scalar counts --
    unlike the classified ``Finding`` lists built from ``diff["findings"]``,
    unaffected by that list's report cap (default 20, ``scan
    --max-findings``). ``None`` when ``diff`` carries no scalar counts to
    read (audit-only / ``NOT_COMPARABLE``) -- callers fall back to the
    classified-list lengths in that case, same as every other mode.

    Codex review: a scan diff with more real breaking/api_break/risk
    findings than the report cap previously rendered a comment header
    undercounting them (e.g. "20 breaking" for 25 real breaks), since the
    header was built purely from ``len()`` of the (possibly-truncated)
    classified lists.

    ``evidence_counts`` (Codex review, follow-up --
    :func:`_scan_findings_evidence_kind_counts`) is subtracted from the raw
    scalars *before* the promotion logic below runs, so an evidence-quality
    finding (which the classifier routes to the ``incomplete`` bucket, never
    ``breaking``/``review``) is excluded from these totals regardless of
    which bucket a promotion would otherwise have moved its raw count into.

    Only the two promotions expressible purely from the scalar totals are
    applied: ``--gate-api-break`` (api_break -> breaking, *legacy exit-code
    scheme only* -- see below) and a ``potential_breaking: error`` severity
    config (api_break + risk -> breaking, since that category IS exactly
    those two raw severities). An ``addition``/``quality_issues`` promotion
    (a *compatible*-bucket finding promoted to blocking) is deliberately not
    folded in here -- the raw ``compatible`` scalar mixes additions and
    quality findings with no way to tell them apart -- see
    :func:`_scan_promoted_compatible_counts` instead, which reads the exact
    per-category counts the ``severity`` block itself already carries.

    ``gate_api_break``'s fixed api_break -> breaking mapping only applies
    when ``levels`` is empty (the legacy exit-code scheme, where that
    mapping really is unconditional) (Codex review): under the
    severity-aware scheme, ``action/run.sh``'s ADVISORY_BREAK keeps the
    run's real exit at 0 whenever ``potential_breaking`` is left at its
    non-error default, regardless of ``fail-on-api-break`` -- applying the
    legacy mapping there folded an advisory-only api_break finding into the
    exact Breaking total, rendering a "Source API break blocks this PR"
    headline next to an actually-green Action check. The severity-aware case
    is handled entirely by the ``potential_breaking: error`` branch above,
    unconditional on ``gate_api_break`` -- exactly mirroring ``compare``'s
    own ``_bucket_changes``.
    """
    if not isinstance(diff, dict):
        return None
    ev = evidence_counts or {}
    # Floored at 0 (CodeRabbit review): the truncated portion of
    # `evidence_counts` is attributed to each kind's *default* bucket, not
    # its real resolved one, when a maintainer's policy `overrides:` moved
    # that kind to a different bucket (the known gap
    # `_scan_findings_evidence_kind_counts`'s own docstring documents) --
    # under that narrow, compounding combination (truncation + an unusual
    # evidence-kind override) the subtraction can exceed the raw scalar for
    # that bucket. Un-floored, that rendered a nonsensical "-1 breaking" in
    # the comment header; `scan_safe_total` below already guards the
    # identical class of subtraction the same way.
    raw_breaking = max(0, _as_int(diff.get("breaking")) - ev.get("breaking", 0))
    raw_api_break = max(0, _as_int(diff.get("api_break")) - ev.get("api_break", 0))
    raw_risk = max(0, _as_int(diff.get("risk")) - ev.get("risk", 0))
    breaking = raw_breaking
    if levels.get("potential_breaking") == "error":
        breaking += raw_api_break + raw_risk
        raw_api_break = 0
        raw_risk = 0
    elif gate_api_break and not levels:
        breaking += raw_api_break
        raw_api_break = 0
    review = raw_api_break + raw_risk
    return breaking, review


def _scan_gate_eligible_compatible_count(
    raw: object, diff: dict[str, object] | None, total_key: str, truncated_key: str
) -> int:
    """Exact count of gate-eligible compatible findings behind one of
    ``diff["additions"]``/``diff["quality"]`` (*raw*, *total_key*/
    *truncated_key* name which pair).

    "Gate-eligible" here means what ``diff["compatible"]`` itself already
    means: ``checker_types.DiffResult.compatible`` is built from
    ``self._evaluated_changes()``, which excludes every ``--contract-
    evaluation`` ``NOT_EVALUATED`` finding *before* the addition/quality
    split ever runs (``cli_scan_baseline._addition_finding_dicts``/
    ``_quality_finding_dicts`` both draw from ``diff.compatible``, never
    ``diff.changes``) -- so every entry *raw* itemizes, and every occurrence
    ``total_key`` counts, is already gate-eligible by construction, with no
    further NOT_EVALUATED filtering needed here (Codex review, follow-up: an
    earlier revision tried to subtract a NOT_EVALUATED count from these same
    itemized dicts, which can never fire -- a NOT_EVALUATED finding can
    never reach `additions_raw`/`quality_raw` in the first place, so that
    subtraction was always zero in practice).

    ``diff[total_key]`` (e.g. ``"additions_total"``) is only ever serialized
    when the itemized list was truncated below the report cap
    (``cli_scan_baseline._baseline_summary``) -- when it wasn't, the
    itemized list's own length already *is* the exact total.
    """
    if isinstance(diff, dict) and diff.get(truncated_key):
        return _as_int(diff.get(total_key))
    return len(raw) if isinstance(raw, list) else 0


def _scan_promoted_compatible_counts(
    diff: dict[str, object] | None,
    levels: dict[str, str],
    additions_raw: object = None,
    quality_raw: object = None,
) -> tuple[int, int]:
    """Exact ``(promoted_addition_count, promoted_quality_count)`` for a
    severity-config-promoted ``compatible`` category -- immune both to
    ``diff["findings"]``'s report cap (like the classified ``breaking`` list
    built from it) and to ``--contract``'s ``NOT_EVALUATED``
    findings (Codex review, two rounds):

    Round 1 read ``diff["severity"]["categories"]["addition"]["count"]``
    (``reporter._build_severity_json``'s ``categorize_changes(diff.changes,
    ...)`` -- classifies purely by kind over the *unfiltered* change set,
    the same reasoning ``_scan_true_counts``'s own docstring already
    documents for the Breaking-total case: a diff with more promoted
    entries than fit in the shared findings budget undercounted this if
    read from the classified list instead). That count is exact against
    truncation, but not against contract evaluation: ADR-049 D1 leaves a
    finding proven outside the declared contract (or unresolved for want of
    evidence) ``NOT_EVALUATED``, which ``compute_gate_decision`` (the real
    exit code) correctly excludes via ``gate_eligible_changes`` but
    ``categorize_changes`` does not -- so a category whose only findings
    were all ``NOT_EVALUATED`` still read ``count > 0`` here, folding a
    nonzero Breaking total (and a "blocked by policy" headline) into a run
    whose real exit was clean.

    Round 2's attempted fix (subtracting a NOT_EVALUATED count read back off
    ``additions_raw``/``quality_raw``) could never actually fire: those
    lists are built from ``diff.compatible``, which already excludes every
    NOT_EVALUATED finding *before* the addition/quality split runs -- so a
    NOT_EVALUATED finding can never appear there to subtract. The correct
    fix reads :func:`_scan_gate_eligible_compatible_count` instead --
    ``additions_total``/``quality_total`` (or, unmarked, the itemized
    list's own length) is *already* gate-eligible-only by construction,
    with nothing left to filter.
    """
    if not isinstance(diff, dict):
        return 0, 0
    promoted_addition = (
        _scan_gate_eligible_compatible_count(
            additions_raw, diff, "additions_total", "additions_truncated"
        )
        if levels.get("addition") == "error"
        else 0
    )
    promoted_quality = (
        _scan_gate_eligible_compatible_count(
            quality_raw, diff, "quality_total", "quality_truncated"
        )
        if levels.get("quality_issues") == "error"
        else 0
    )
    return promoted_addition, promoted_quality


def _scan_exact_breaking_categories(
    diff: dict[str, object] | None,
    gate_api_break: bool,
    levels: dict[str, str],
    evidence_counts: dict[str, int],
    promoted_addition_count: int,
    promoted_quality_count: int,
    promoted_evidence_addition_count: int,
    promoted_evidence_quality_count: int,
) -> tuple[frozenset[str], frozenset[str]]:
    """``(categories, severities)`` genuinely contributing to the exact
    Breaking total (mirrors ``_scan_true_counts``'s own promotion logic and
    the promoted-compatible folds in :func:`from_scan`), independent of
    ``diff["findings"]``'s report cap (Codex review).

    ``model.breaking_categories``/``model.breaking_severities`` (via the
    shared ``_breaking_categories``/``_breaking_severities`` helpers) used to
    be derived purely from the itemized, possibly-capped ``breaking``
    ``Finding`` list -- correct for ``compare``, whose ``changes`` list is
    never capped, but not for ``scan``:
    ``cli_scan_baseline._add_severity_blocking_compatible_findings``'s
    reserved-floor design only *guarantees a minimum* representation for a
    promoted compatible category inside the shared, capped ``findings``
    list, not completeness -- with a small enough cap (e.g. ``--max-findings
    1``), that reservation can consume the entire budget, pushing a real ABI
    break out of the itemized list entirely. Deriving the headline purely
    from what survived the cap then mislabeled a genuine ``BREAKING``
    verdict as a policy-only block ("Public API expansion requires
    approval"), even rendering "Compatibility: COMPATIBLE" text next to it.

    The result is meant to be unioned with the itemized-list-derived sets,
    not replace them -- this covers only what the four exact scalar/
    per-category-count sources already used elsewhere in :func:`from_scan`
    can prove; cross-check findings (which carry their own severity but no
    severity-config category) are exempt from the same cap and stay
    correctly represented by the itemized list alone.
    """
    categories: set[str] = set()
    severities: set[str] = set()
    if isinstance(diff, dict):
        raw_breaking = _as_int(diff.get("breaking")) - evidence_counts.get("breaking", 0)
        raw_api_break = _as_int(diff.get("api_break")) - evidence_counts.get("api_break", 0)
        raw_risk = _as_int(diff.get("risk")) - evidence_counts.get("risk", 0)
        if raw_breaking > 0:
            categories.add("abi_breaking")
            severities.add("breaking")
        if levels.get("potential_breaking") == "error":
            if raw_api_break > 0:
                categories.add("potential_breaking")
                severities.add("api_break")
            if raw_risk > 0:
                categories.add("potential_breaking")
                severities.add("risk")
        elif gate_api_break and not levels and raw_api_break > 0:
            categories.add("potential_breaking")
            severities.add("api_break")
    if promoted_addition_count - promoted_evidence_addition_count > 0:
        categories.add("addition")
        severities.add("compatible")
    if promoted_quality_count - promoted_evidence_quality_count > 0:
        categories.add("quality_issues")
        severities.add("compatible")
    return frozenset(categories), frozenset(severities)


def _scan_crosscheck_findings(
    report: dict[str, object], audit_only: bool
) -> tuple[list[Finding], int]:
    """Synthetic, count-only ``Finding``s for a scan's cross-check results
    that actually gated the run, plus the exact total occurrence count
    across the included kinds (the second element -- immune to a single
    kind's multiple occurrences collapsing to "one" row, see below).

    Applies to both an audit-only run (no ``--against``) and a
    baseline-comparison run (``scan --against``) alike -- cross-check is a
    separate evidence axis from the baseline diff (ADR-035 D4), so it can
    gate a run whose ``diff`` itself is entirely clean. Which kinds gate
    differs by mode, though (Codex review, fresh evidence):
    ``scan_engine._audit_exit_code`` raises an audit-only run's exit code
    on ANY ``API_BREAK_KINDS`` finding, promoted or not, but
    ``_run_baseline_compare`` only ever folds ``_crosscheck_severity_exit``
    -- an *explicitly promoted* (``--crosscheck KEY=error``) cross-check --
    into a baseline comparison's exit code; an un-promoted
    ``API_BREAK_KINDS`` cross-check stays advisory-only there and must not
    render as a review/breaking finding next to an otherwise-COMPATIBLE
    baseline diff.

    Codex review: an audit-only scan's ``diff`` is always ``None`` (there is
    no baseline to compare), so before this helper existed
    :func:`from_scan` populated every compatibility bucket empty regardless
    of the verdict -- with ``pr-comment-on: changes`` the comment was
    skipped (or a stale sticky one deleted) even when ``fail-on-api-break``
    had just turned the check red, and with ``always`` it rendered the
    green "Scan audit — no baseline to compare" headline right next to it.
    A follow-up review found the same gap on the baseline-comparison side:
    restricting this helper to ``audit_only`` left a clean baseline diff
    next to a silently-dropped cross-check-driven ``API_BREAK`` promotion,
    since ``diff["findings"]``/``diff["additions"]`` never carry cross-check
    results at all -- they're a wholly separate top-level report key.

    ``CrosscheckResult.to_dict()`` (``report["crosscheck"]``) carries no itemized finding detail (symbol/location) -- only ``counts_by_check`` (``ChangeKind`` value -> count) -- so each kind renders as one aggregate row rather than one row per instance, coarser than compare's own per-symbol findings. Still the difference between an accurate "N cross-check finding(s)" review section and total silence next to a red check. A follow-up review found the aggregate-row count itself leaking into the exact summary total (``len(findings)`` counting "1" for a kind with 7 real occurrences) -- callers must use this function's own returned total, not ``len()`` of the itemized list.
    """
    from .checker_policy import API_BREAK_KINDS, RISK_KINDS

    crosscheck = report.get("crosscheck")
    counts = crosscheck.get("counts_by_check") if isinstance(crosscheck, dict) else None
    if not isinstance(counts, dict):
        return [], 0
    severities_raw = report.get("crosscheck_severities")
    severities = severities_raw if isinstance(severities_raw, dict) else {}
    api_break_values = {k.value for k in API_BREAK_KINDS}
    risk_values = {k.value for k in RISK_KINDS}
    findings: list[Finding] = []
    total = 0
    for kind, count in counts.items():
        if not isinstance(count, int) or count <= 0:
            continue
        promoted = severities.get(kind) == "error"
        if promoted:
            pass
        elif audit_only and kind in api_break_values:
            pass
        else:
            # Either advisory-only (RISK-tier, un-promoted) or -- on a
            # baseline-comparison run -- an un-promoted API_BREAK_KINDS
            # cross-check, which stays advisory there (see docstring):
            # never gates this run, so it's not what the reader needs
            # explained next to the verdict/exit code.
            continue
        detail = f"{count} occurrence(s)"
        if promoted:
            detail += " (--crosscheck promoted to error)"
        # Codex review: a RISK-tier check (e.g. `identity_collision_detected`)
        # promoted with `--crosscheck KEY=error` still gates the run
        # (`scan_engine._crosscheck_severity_exit` deliberately allows even a
        # RISK check to reach the source-break exit tier), but that gate
        # promotion doesn't turn the underlying evidence into an actual API
        # break -- hardcoding `severity="api_break"` here made the sticky
        # headline claim "Source API break blocks this PR" for what the
        # scan report itself calls a compatibility risk. Derive the label
        # from the kind's own `API_BREAK_KINDS`/`RISK_KINDS` membership
        # instead, so the promoted-but-not-an-API-break case reads
        # "Compatibility risk blocks this PR" (`pr_comment._header`).
        if kind in api_break_values:
            severity = "api_break"
        elif kind in risk_values:
            severity = "risk"
        else:
            # A cross-check kind outside both sets is unusual (every
            # ChangeKind belongs to exactly one severity tier), but if it
            # ever happens, the pre-existing conservative default keeps a
            # gating finding visible as a break rather than silently
            # dropping its severity.
            severity = "api_break"
        findings.append(
            Finding(
                kind=str(kind),
                symbol=f"Cross-check: {kind}",
                detail=detail,
                category="potential_breaking",
                severity=severity,
            )
        )
        total += count
    return findings, total


def from_scan(
    report: dict[str, object],
    gate_api_break: bool = False,
    gate_breaking: bool = True,
) -> CommentModel:
    """Build a :class:`~abicheck.pr_comment.CommentModel` from a ``scan``
    JSON report.

    Four shapes of ``report["diff"]`` all degrade gracefully rather than
    raising:

    * a normal baseline comparison -- ``dict`` with ``findings``/``additions``;
    * a scope/profile mismatch (``NOT_COMPARABLE``) -- ``{"reason": "..."}``,
      surfaced as a single blocking "analysis incomplete" finding (there is
      nothing to itemize: the two sides couldn't even be compared);
    * a ``scan`` abort (``BUDGET_OVERFLOW``/``EVIDENCE_CONTRACT_ERROR``) --
      the same treatment, via :func:`~abicheck.pr_comment_scan_abort.
      scan_abort_incomplete_reason` (see its own docstring for the shape
      and the bug this closes);
    * an audit-only run (no ``--against`` at all) -- ``None``, so every
      bucket stays empty; ``scan_audit_only`` flags this so the headline
      doesn't misreport "no ABI changes" as if a comparison had actually run.
    """
    diff = report.get("diff")
    diff_dict = diff if isinstance(diff, dict) else None
    findings_raw: object = None
    additions_raw: object = None
    quality_raw: object = None
    not_comparable_reason: str | None = None
    scan_abort_reason: str | None = None
    audit_only = diff is None
    if isinstance(diff, dict):
        if "reason" in diff:
            not_comparable_reason = str(diff.get("reason", "") or "not comparable")
        elif (reason := scan_abort_incomplete_reason(diff, report)) is not None:
            scan_abort_reason = reason
        else:
            findings_raw = diff.get("findings")
            additions_raw = diff.get("additions")
            quality_raw = diff.get("quality")

    # `_run_baseline_compare`'s own severity-gate block and the
    # contract-coverage ledger both nest inside `report["diff"]`, not at the
    # top level the way `compare`'s own report carries them -- reading the
    # top-level `report` here (as an earlier revision did) always saw an
    # absent key (Codex review: a real a `severity.addition: error` config /
    # `--contract` scan silently lost both signals).
    levels = _severity_levels(diff_dict) if diff_dict is not None else {}
    symbols: list[str] = []
    for raw in (findings_raw, additions_raw, quality_raw):
        if isinstance(raw, list):
            symbols.extend(
                str(c.get("symbol", "")) for c in raw if isinstance(c, dict)
            )
    demangled_map = demangle_batch(symbols)

    breaking, review, evidence_incomplete = _scan_findings_to_buckets(
        findings_raw, demangled_map, gate_api_break, levels
    )
    addition_promoted = levels.get("addition") == "error"
    quality_promoted = levels.get("quality_issues") == "error"
    safe_additions, incomplete_additions = _scan_additions_to_safe(
        additions_raw, demangled_map, addition_promoted
    )
    safe_quality, incomplete_quality = _scan_quality_to_safe(
        quality_raw, demangled_map, quality_promoted
    )
    safe = safe_additions + safe_quality
    evidence_incomplete = evidence_incomplete + incomplete_additions + incomplete_quality

    # Cross-check is a separate evidence axis from the baseline diff (ADR-035
    # D4) that never nests inside `diff["findings"]`/`diff["additions"]`, so
    # it needs surfacing regardless of whether this run is audit-only or a
    # baseline comparison (see the helper's own docstring for the coarser,
    # aggregate-count render this produces). --gate-api-break
    # (fail-on-api-break) moves a gating result to Breaking, mirroring how
    # the ordinary baseline-comparison path treats an api_break severity
    # finding under the same flag.
    crosscheck_findings, crosscheck_total = _scan_crosscheck_findings(report, audit_only)
    # NOT_COMPARABLE unconditionally governs this run's exit (scope/profile
    # mismatch, exit 6) -- `scan_engine.run_scan_core` deliberately skips
    # cross-check severity folding entirely when no baseline comparison ran
    # (Codex review), so a promoted cross-check here never actually gated
    # anything. Keep it review-only regardless of `gate_api_break`, rather
    # than letting a promotion move it into Breaking next to the real,
    # unconditional NOT_COMPARABLE block below -- which would headline
    # "Source API break blocks this PR" for what is really a scope mismatch.
    if gate_api_break and not_comparable_reason is None and scan_abort_reason is None:
        breaking = breaking + crosscheck_findings
    else:
        review = review + crosscheck_findings

    incomplete: list[Finding] = list(evidence_incomplete)
    incomplete_blocking = False
    # Mutually exclusive (same `diff`-shape dispatch above); unified since
    # both render as one blocking "analysis incomplete" `Finding`.
    incomplete_reason = not_comparable_reason or scan_abort_reason
    if incomplete_reason is not None:
        incomplete.append(
            Finding(
                kind="scan_not_comparable" if not_comparable_reason else "scan_aborted",
                symbol="Baseline comparison",
                detail=incomplete_reason,
                severity="unknown",
            )
        )
        incomplete_blocking = True
    incomplete = incomplete + (
        _contract_coverage_findings(diff_dict) if diff_dict is not None else []
    )
    contract_exit = diff_dict.get("contract_coverage_exit_contribution") if diff_dict else None
    contract_coverage_blocking = isinstance(contract_exit, int) and contract_exit >= 1
    if contract_coverage_blocking:
        incomplete_blocking = True

    risk = report.get("risk")
    verdict = report.get("verdict")
    evidence_counts = _scan_findings_evidence_kind_counts(findings_raw, diff_dict)
    # A promoted compatible-category evidence-quality finding (most notably
    # `dwarf_info_missing` under `quality_issues: error`) is itself itemized
    # in `diff["additions"]`/`diff["quality"]` regardless of promotion state
    # (Codex review, follow-up) -- so its occurrence(s) can be counted here
    # even though `_scan_additions_to_safe`/`_scan_quality_to_safe` already
    # short-circuit to `([], [])` on `promoted=True` (nothing to add to
    # `incomplete` from *that* call site once promoted; it's `diff.findings`,
    # via `_scan_findings_to_buckets`'s own evidence-kind check, that
    # actually itemizes it under `incomplete` in the promoted case). Without
    # this, `_scan_promoted_compatible_counts`'s exact per-category scalar
    # (which counts every promoted compatible finding in that category,
    # evidence-shaped or not) folded the evidence occurrence into the
    # Breaking total on top of it already being itemized under Incomplete --
    # double-counted across two sections, with a spurious "ABI BREAKING"
    # headline for what is really an evidence-policy failure. Same
    # itemized-only limitation as `_scan_findings_evidence_kind_counts`'s own
    # docstring: exact only for what fit under the report cap.
    promoted_evidence_addition_count = (
        sum(
            1
            for c in additions_raw
            if isinstance(c, dict) and str(c.get("kind", "")) in _EVIDENCE_KIND_VALUES
        )
        if addition_promoted and isinstance(additions_raw, list)
        else 0
    )
    promoted_evidence_quality_count = (
        sum(
            1
            for c in quality_raw
            if isinstance(c, dict) and str(c.get("kind", "")) in _EVIDENCE_KIND_VALUES
        )
        if quality_promoted and isinstance(quality_raw, list)
        else 0
    )
    # Codex review, follow-up: pulling an evidence-quality finding out of
    # `breaking`/`review` into `incomplete` correctly excluded it from the
    # compatibility totals, but left `incomplete_blocking` untouched -- a
    # run whose *only* gating cause was e.g. a promoted
    # `evidence_required_missing` (or, under `--gate-api-break`, any
    # api_break-bucket evidence kind, or a promoted compatible evidence
    # kind like `dwarf_info_missing`) still exited non-zero, but the
    # comment rendered the soft "⚠️ Analysis coverage reduced" headline
    # next to an actually-red Action check.
    if (
        _scan_evidence_incomplete_blocking(evidence_counts, gate_api_break, gate_breaking, levels)
        or promoted_evidence_addition_count > 0
        or promoted_evidence_quality_count > 0
    ):
        incomplete_blocking = True
    true_counts = _scan_true_counts(diff_dict, gate_api_break, levels, evidence_counts)
    promoted_addition_count, promoted_quality_count = _scan_promoted_compatible_counts(
        diff_dict, levels, additions_raw, quality_raw
    )
    if true_counts is not None:
        # `_scan_true_counts` only folds the two promotions expressible from
        # the raw breaking/api_break/risk scalars -- an addition/
        # quality_issues severity-config promotion moves a *compatible*-
        # severity finding into `breaking` too, invisible to those scalars
        # entirely. Uses the exact per-category severity counts (see
        # `_scan_promoted_compatible_counts`'s own docstring for why the
        # classified `breaking` list's own length is not a safe substitute).
        # The evidence-kind portion of each promoted category is excluded
        # (subtracted right back out) since those occurrences render under
        # Incomplete, never Breaking (see the comment above).
        true_counts = (
            true_counts[0]
            + promoted_addition_count
            + promoted_quality_count
            - promoted_evidence_addition_count
            - promoted_evidence_quality_count,
            true_counts[1],
        )
        # Cross-check findings are folded into the exact scalar totals too
        # (not just the classified `breaking`/`review` lists above) --
        # `crosscheck_total` is the exact summed occurrence count across
        # every gating kind, never `len(crosscheck_findings)` (one
        # aggregate row per kind, which undercounts a kind with more than
        # one real occurrence -- Codex review). Same NOT_COMPARABLE/
        # scan-abort exclusion as the itemized-list fold above -- neither's
        # promoted cross-check severity was ever actually folded into this
        # run's real exit code (Codex review, follow-up).
        if gate_api_break and not_comparable_reason is None and scan_abort_reason is None:
            true_counts = (true_counts[0] + crosscheck_total, true_counts[1])
        else:
            true_counts = (true_counts[0], true_counts[1] + crosscheck_total)
    else:
        # Audit-only: there is no `diff` to derive scalar totals from at
        # all, and the only findings an audit-only run can produce are its
        # own cross-check results (see `_scan_crosscheck_findings`'s own
        # docstring) -- so the exact total is exactly `crosscheck_total`,
        # not `len()` of the itemized (per-kind, not per-occurrence) list.
        true_counts = (
            (crosscheck_total, 0) if gate_api_break else (0, crosscheck_total)
        )

    # Exact "safe" total, derived from `diff["compatible"]` (Codex review,
    # follow-up): the earlier revision derived this solely from
    # `diff["additions"]`/`additions_total` -- but those itemize only the
    # addition-shaped subset of `compatible` (`ChangeKind`'s
    # `ADDITION_KINDS`, see `cli_scan_baseline._addition_finding_dicts`), so
    # a compatible-but-non-addition finding (a quality-category change like
    # `func_noexcept_added`, or a policy-demoted removal reclassified
    # compatible) was invisible to this total entirely -- not merely
    # unitemized. A diff whose only findings were of that shape reported
    # zero changes across every bucket, so `pr-comment-on: changes` skipped
    # (or sticky mode deleted) a comment for a real, non-empty scan result.
    # `diff["compatible"]` is the exact, unfiltered scalar `_baseline_summary`
    # always emits (`len(diff.compatible)`, never capped), so subtracting
    # both promoted-category counts here yields the exact non-promoted-safe
    # total regardless of finding shape.
    #
    # This total's itemization is now complete too (Codex review, follow-up):
    # `diff["quality"]` (`cli_scan_baseline._quality_finding_dicts`)
    # itemizes exactly the complement `diff["additions"]` leaves out -- the
    # compatible-but-non-addition subset -- so `model.safe` (built from both
    # lists, see `_scan_additions_to_safe`/`_scan_quality_to_safe` above) can
    # only fall short of `scan_safe_total` the same way `additions` already
    # could: real truncation at the shared report cap, not a whole finding
    # shape with nothing to show for it.
    #
    # An evidence-quality kind (most notably `dwarf_info_missing`, COMPATIBLE
    # by default severity) reaches `diff["compatible"]`'s raw scalar too, but
    # `_scan_additions_to_safe`/`_scan_quality_to_safe` now pull it into
    # `incomplete` rather than `safe` -- so it must also be excluded here
    # (Codex review, follow-up), or a scan with missing DWARF info would
    # still count as "safe" in the exact header total even though it's no
    # longer itemized under the green section. Guarded on `not
    # addition_promoted`/`not quality_promoted`: when a category is
    # promoted, its evidence-kind occurrences are already folded into
    # `promoted_addition_count`/`promoted_quality_count` (the exact
    # per-category severity scalar counts every compatible entry in that
    # category, evidence-shaped or not) -- counting them again here would
    # double-subtract.
    #
    # Known gap, not fixed here (Codex review, follow-up): this itemized
    # count only ever sees what `additions_raw`/`quality_raw` actually
    # carry -- if `diff["quality"]` was itself truncated at the shared cap
    # (`quality_truncated: True`) and a `dwarf_info_missing`-shaped entry
    # fell past it, that occurrence stays silently counted as "safe" in
    # `scan_safe_total` and never appears in `incomplete`. Unlike
    # `diff["findings"]`, `diff["quality"]`/`diff["additions"]` carry no
    # per-kind truncation ledger (only the aggregate `quality_total`/
    # `additions_total` scalars) for this function to recover an exact
    # count from -- the same schema addition
    # `_scan_findings_evidence_kind_counts`'s own docstring describes would
    # be needed here too, not a same-round patch.
    evidence_compatible_count = 0
    if not addition_promoted and isinstance(additions_raw, list):
        evidence_compatible_count += sum(
            1
            for c in additions_raw
            if isinstance(c, dict) and str(c.get("kind", "")) in _EVIDENCE_KIND_VALUES
        )
    if not quality_promoted and isinstance(quality_raw, list):
        evidence_compatible_count += sum(
            1
            for c in quality_raw
            if isinstance(c, dict) and str(c.get("kind", "")) in _EVIDENCE_KIND_VALUES
        )
    scan_safe_total: int | None = None
    if diff_dict is not None:
        raw_compatible = _as_int(diff_dict.get("compatible"))
        scan_safe_total = max(
            0,
            raw_compatible
            - promoted_addition_count
            - promoted_quality_count
            - evidence_compatible_count,
        )

    _exact_breaking_categories, _exact_breaking_severities = _scan_exact_breaking_categories(
        diff_dict,
        gate_api_break,
        levels,
        evidence_counts,
        promoted_addition_count,
        promoted_quality_count,
        promoted_evidence_addition_count,
        promoted_evidence_quality_count,
    )
    return CommentModel(
        mode="scan",
        subject=str(report.get("subject") or "artifact"),
        old_label="baseline",
        new_label="candidate",
        # Codex review: `_run_baseline_compare`'s resolved policy nests
        # inside `report["diff"]["policy"]` (`cli_scan_baseline.
        # _baseline_summary`), the same place its severity-gate block and
        # the contract-coverage ledger already nest -- not at the top level
        # `report.get("policy", ...)` read here before, which always saw
        # the default fallback and silently misreported a non-default
        # `--policy` in the comment footer.
        policy=str(
            (diff_dict.get("policy") if diff_dict is not None else None)
            or "strict_abi"
        ),
        breaking=breaking,
        review=review,
        safe=safe,
        incomplete=incomplete,
        incomplete_blocking=incomplete_blocking,
        contract_coverage_blocking=contract_coverage_blocking,
        breaking_categories=_breaking_categories(breaking) | _exact_breaking_categories,
        breaking_severities=_breaking_severities(breaking) | _exact_breaking_severities,
        suppressed_count=_suppressed_count_scan(diff_dict),
        scan_verdict=str(verdict) if verdict is not None else None,
        scan_risk=risk if isinstance(risk, dict) else None,
        scan_coverage_lines=_scan_coverage_lines(report),
        scan_audit_only=audit_only,
        scan_breaking_total=true_counts[0],
        scan_review_total=true_counts[1],
        scan_safe_total=scan_safe_total,
        scan_findings_truncated=bool(diff_dict and diff_dict.get("findings_truncated")),
        scan_additions_truncated=bool(diff_dict and diff_dict.get("additions_truncated")),
        scan_quality_truncated=bool(diff_dict and diff_dict.get("quality_truncated")),
        scan_incomplete_total=(
            len(incomplete) + _scan_findings_evidence_truncated_extra(diff_dict)
        ),
    )


def scan_note(model: CommentModel) -> list[str]:
    """``scan``-mode-only info line: the raw verdict, risk score, and which
    evidence layers ran — none of ``compare``'s model carries an equivalent,
    and a reviewer skimming a scan comment benefits from seeing what
    evidence the run actually gathered, not just the compatibility buckets
    rendered around it.
    """
    if model.mode != "scan":
        return []
    bits: list[str] = []
    if model.scan_verdict:
        bits.append(f"verdict `{_esc(model.scan_verdict)}`")
    risk = model.scan_risk
    if isinstance(risk, dict):
        total = risk.get("total")
        if isinstance(total, (int, float)):
            risk_bit = (
                f"risk score {total:g}" if isinstance(total, float) else f"risk score {total}"
            )
            method = risk.get("recommended_method")
            if method:
                risk_bit += f" (`{_esc(method)}`)"
            bits.append(risk_bit)
    if model.scan_audit_only:
        bits.append("audit-only (no `--against` baseline)")
    out: list[str] = []
    if bits:
        out += [f"> 🔎 Scan: {' · '.join(bits)}", ""]
    if model.scan_coverage_lines:
        out += [f"> 📊 Coverage: {', '.join(model.scan_coverage_lines)}", ""]
    if (
        model.scan_findings_truncated
        or model.scan_additions_truncated
        or model.scan_quality_truncated
    ):
        truncated = [
            label
            for label, flag in (
                ("gating findings", model.scan_findings_truncated),
                ("additions", model.scan_additions_truncated),
                ("other compatible findings", model.scan_quality_truncated),
            )
            if flag
        ]
        out += [
            f"> ✂️ {' and '.join(truncated)} itemized below were truncated at the "
            f"report cap (`scan --max-findings`) — the counts above are exact "
            f"regardless.",
            "",
        ]
    return out
