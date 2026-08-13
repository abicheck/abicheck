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

Imports only from the dependency-free leaf module ``pr_comment_base`` (never
from ``pr_comment`` itself, which would form a real import cycle — see
``pr_comment_base``'s own module docstring). ``pr_comment`` imports
``from_scan``/``scan_note`` from here inside function bodies
(``build_model``/``_body_sections``) purely for readability at the call
site, not to break a cycle — there is none, since the dependency runs one
way (``pr_comment`` → ``pr_comment_scan`` → ``pr_comment_base``).
"""

from __future__ import annotations

from .demangle import demangle_batch
from .pr_comment_base import (
    _SEVERITY_BUCKET,
    CommentModel,
    Finding,
    _breaking_categories,
    _breaking_severities,
    _contract_coverage_findings,
    _demangle_symbol,
    _esc,
    _finding_category,
    _normalize_location,
    _severity_levels,
)

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
) -> tuple[list[Finding], list[Finding]]:
    """Classify ``diff["findings"]`` (the gating buckets) into (breaking, review).

    Mirrors ``_bucket_changes``'s severity/category/gate logic, adapted to
    scan's ``bucket``-keyed finding dicts. ``not_evaluated`` entries (ADR-049
    D9: contract relevance excluded them from compatibility scoring
    entirely) and ``suppressed`` entries (reported separately, never inside
    ``findings``) are skipped -- neither is a compatibility claim, so
    neither belongs in any of the three buckets.
    """
    breaking: list[Finding] = []
    review: list[Finding] = []
    target = {"breaking": breaking, "review": review}
    if not isinstance(findings_raw, list):
        return breaking, review
    for c in findings_raw:
        if not isinstance(c, dict):
            continue
        raw_bucket = str(c.get("bucket", ""))
        sev = _SCAN_BUCKET_TO_SEVERITY.get(raw_bucket)
        if sev is None:
            # "not_evaluated" / "suppressed" / anything unrecognized.
            continue
        kind = str(c.get("kind", ""))
        bucket = _SEVERITY_BUCKET.get(sev, "review")
        if gate_api_break and sev == "api_break":
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
    return breaking, review


def _scan_additions_to_safe(
    additions_raw: object, demangled_map: dict[str, str]
) -> list[Finding]:
    """Classify the always-on ``diff["additions"]`` list (schema 1.13+) into
    Safe-bucket ``Finding``s, each tagged ``category="addition"`` so they
    render in the same "➕ Public API additions" section `compare` uses.
    """
    safe: list[Finding] = []
    if not isinstance(additions_raw, list):
        return safe
    for c in additions_raw:
        if not isinstance(c, dict):
            continue
        loc = c.get("source_location")
        display_symbol, mangled_evidence = _demangle_symbol(
            str(c.get("symbol", "")), demangled_map
        )
        safe.append(
            Finding(
                kind=str(c.get("kind", "")),
                symbol=display_symbol,
                detail=str(c.get("description", "") or ""),
                location=_normalize_location(str(loc)) if loc else None,
                category="addition",
                severity="compatible",
                mangled=mangled_evidence,
            )
        )
    return safe


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


def from_scan(
    report: dict[str, object],
    gate_api_break: bool = False,
    gate_breaking: bool = True,
) -> CommentModel:
    """Build a :class:`~abicheck.pr_comment.CommentModel` from a ``scan``
    JSON report.

    Three shapes of ``report["diff"]`` all degrade gracefully rather than
    raising:

    * a normal baseline comparison -- ``dict`` with ``findings``/``additions``;
    * a scope/profile mismatch (``NOT_COMPARABLE``) -- ``{"reason": "..."}``,
      surfaced as a single blocking "analysis incomplete" finding (there is
      nothing to itemize: the two sides couldn't even be compared);
    * an audit-only run (no ``--against`` at all) -- ``None``, so every
      bucket stays empty; ``scan_audit_only`` flags this so the headline
      doesn't misreport "no ABI changes" as if a comparison had actually run.
    """
    diff = report.get("diff")
    findings_raw: object = None
    additions_raw: object = None
    not_comparable_reason: str | None = None
    audit_only = diff is None
    if isinstance(diff, dict):
        if "reason" in diff:
            not_comparable_reason = str(diff.get("reason", "") or "not comparable")
        else:
            findings_raw = diff.get("findings")
            additions_raw = diff.get("additions")

    levels = _severity_levels(report)
    symbols: list[str] = []
    for raw in (findings_raw, additions_raw):
        if isinstance(raw, list):
            symbols.extend(
                str(c.get("symbol", "")) for c in raw if isinstance(c, dict)
            )
    demangled_map = demangle_batch(symbols)

    breaking, review = _scan_findings_to_buckets(
        findings_raw, demangled_map, gate_api_break, levels
    )
    safe = _scan_additions_to_safe(additions_raw, demangled_map)

    incomplete: list[Finding] = []
    incomplete_blocking = False
    if not_comparable_reason is not None:
        incomplete.append(
            Finding(
                kind="scan_not_comparable",
                symbol="Baseline comparison",
                detail=not_comparable_reason,
                severity="unknown",
            )
        )
        incomplete_blocking = True
    incomplete = incomplete + _contract_coverage_findings(report)
    contract_exit = report.get("contract_coverage_exit_contribution")
    contract_coverage_blocking = isinstance(contract_exit, int) and contract_exit >= 1
    if contract_coverage_blocking:
        incomplete_blocking = True

    risk = report.get("risk")
    verdict = report.get("verdict")

    return CommentModel(
        mode="scan",
        subject=str(report.get("subject") or "artifact"),
        old_label="baseline",
        new_label="candidate",
        policy=str(report.get("policy", "strict_abi")),
        breaking=breaking,
        review=review,
        safe=safe,
        incomplete=incomplete,
        incomplete_blocking=incomplete_blocking,
        contract_coverage_blocking=contract_coverage_blocking,
        breaking_categories=_breaking_categories(breaking),
        breaking_severities=_breaking_severities(breaking),
        suppressed_count=_suppressed_count_scan(
            diff if isinstance(diff, dict) else None
        ),
        scan_verdict=str(verdict) if verdict is not None else None,
        scan_risk=risk if isinstance(risk, dict) else None,
        scan_coverage_lines=_scan_coverage_lines(report),
        scan_audit_only=audit_only,
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
    return out
