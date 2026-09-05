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

"""Leaf plumbing shared by :mod:`abicheck.pr_comment` and
:mod:`abicheck.pr_comment_scan` (split out of ``pr_comment.py`` to stay under
its 2000-line hard cap once scan-report support was added).

Dependency-free of both sibling modules -- the same "leaf module" pattern
``buildsource/crosscheck_base.py`` already uses for the identical reason
(see its own module docstring): either sibling can import from here without
forming an import cycle, since nothing here imports either of them back.

Holds the mode-agnostic data model (:class:`Finding`, :class:`CommentModel`)
and the small, pure classification/formatting primitives both report shapes
(``compare``/``appcompat``/``release`` in ``pr_comment.py``, ``scan`` in
``pr_comment_scan.py``) need identically: severity/category classification,
symbol demangling, location normalisation, the contract-coverage-ledger
finding builder, and markdown cell escaping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .checker_policy import (
    ADDITION_KINDS,
    API_BREAK_KINDS,
    BREAKING_KINDS,
    RISK_KINDS,
    ChangeKind,
)

# Kind value strings that constitute new public-API surface (the severity
# "addition" category). Sourced from the authoritative ADDITION_KINDS so kinds
# that don't end in "_added" (e.g. type_field_added_compatible,
# experimental_graduated) are classified correctly.
_ADDITION_KIND_VALUES = frozenset(k.value for k in ADDITION_KINDS)


# Severity tokens emitted in the JSON report (`reporter._effective_severity_label`)
# routed into the three reviewer-facing buckets.
_SEVERITY_BUCKET = {
    "breaking": "breaking",
    "api_break": "review",
    "risk": "review",
    "compatible": "safe",
    "unknown": "review",
}


@dataclass
class Finding:
    """A single change, normalised for the comment."""

    kind: str
    symbol: str
    detail: str = ""
    location: str | None = None
    # Severity-config category this finding belongs to ("abi_breaking" /
    # "potential_breaking" / "addition" / "quality_issues"), or "" when not
    # tracked (e.g. release-mode global rows, which stay aggregate counts).
    # Drives the Breaking-bucket headline (see module docstring): it tells
    # the renderer *why* a finding landed in Breaking, so a policy-gated
    # COMPATIBLE addition doesn't get reported as "ABI BREAKING".
    category: str = ""
    # Raw severity label ("breaking" / "api_break" / "risk" / "compatible" /
    # "unknown"), or "" when not tracked. `category` alone conflates
    # "api_break" and "risk" into one "potential_breaking" bucket (they share
    # a severity-config knob), which isn't enough to word the headline
    # correctly: a risk finding promoted by `potential_breaking: error` is
    # not a "source API break" (Codex review, PR #595).
    severity: str = ""
    # Free-form consequence text, sourced verbatim from the report's own
    # `impact` field (`change_registry.py`'s `impact=` on the matching
    # `ChangeKindMeta` entry) when present. Rendered under the finding's own
    # row as "**Impact:** ..." (`_flat_row`) whenever non-empty — not every
    # entry is an actionable remediation step, so it is never labelled "Fix".
    impact: str = ""
    # The raw Itanium-mangled linker symbol, set only when `symbol` above was
    # swapped for its demangled form (`_demangle_symbol`) — i.e. `symbol` is
    # human-readable and this is the "linker evidence" backing it. Empty for
    # a non-C++ symbol (nothing to demangle) or when demangling was
    # unavailable, in which case `symbol` is already the raw mangled/plain
    # name and there is nothing distinct left to show here.
    mangled: str = ""


@dataclass
class CommentModel:
    """Mode-agnostic view of a report, ready to render.

    A plain data container aggregating the report's header fields, the three
    reviewer buckets, and the release-mode rollup.
    """

    mode: str  # "compare" | "release" | "appcompat"
    subject: str
    old_label: str
    new_label: str
    policy: str
    breaking: list[Finding] = field(default_factory=list)
    review: list[Finding] = field(default_factory=list)
    safe: list[Finding] = field(default_factory=list)
    # Findings whose kind is in `_EVIDENCE_KIND_VALUES` — degraded/missing
    # comparison evidence, never a compatibility claim (see module docstring).
    # Deliberately excluded from `breaking`/`review`/`safe` and from
    # `counts`, so it can never masquerade as a compatibility bucket; folded
    # into `total_changes` separately so `should_post("changes")` still fires
    # on a report that carries only this.
    incomplete: list[Finding] = field(default_factory=list)
    # Whether the incomplete bucket actually turns the Action's check red —
    # i.e. whether `_incomplete_is_blocking` found a finding whose severity
    # is gated to blocking (see that function's own docstring for exactly
    # which severity/gate-flag combinations qualify), OR the contract-
    # coverage ledger contributed (see `contract_coverage_blocking` below).
    # Drives the headline's emoji/wording (see `_header`).
    incomplete_blocking: bool = False
    # Specifically whether `contract_coverage_exit_contribution` (ADR-049
    # Phase 5's ledger) is what's blocking — tracked separately from the
    # general `incomplete_blocking` because this one axis is documented as
    # *always* additive to the real exit code (AGENTS.md: "no fail-on-*
    # condition... folded with max... on compare and scan --against alike"),
    # even ahead of a `--used-by`/`--required-symbol` scoped verdict — a
    # scoped-COMPATIBLE run whose contract coverage also failed must not
    # render "✅ Compatible (scoped)" as if that were the whole story
    # (Codex review; see `_header`'s scoped_verdict branch).
    contract_coverage_blocking: bool = False
    # Severity-config categories ("abi_breaking" / "potential_breaking" /
    # "addition" / "quality_issues") responsible for a non-empty Breaking
    # bucket. Populated alongside `breaking` in every mode (see
    # `_breaking_categories` and the release-mode `categories` accumulator in
    # `_release_lib_row`/`_append_release_global_row`) so the headline can
    # tell a genuine ABI/API break apart from a policy-gated COMPATIBLE
    # finding without re-deriving it from bucket membership alone.
    breaking_categories: frozenset[str] = field(default_factory=frozenset)
    # Raw severities ("breaking" / "api_break" / "risk") behind a non-empty
    # Breaking bucket — same purpose as `breaking_categories`, but resolves
    # the "potential_breaking" category into which severity actually caused
    # it, since "api_break" (a real source break) and "risk" (a risk
    # promoted to blocking) need different headline wording.
    breaking_severities: frozenset[str] = field(default_factory=frozenset)
    # release mode only: (library, verdict, n_breaking, n_review, n_safe)
    library_rows: list[tuple[str, str, int, int, int]] = field(default_factory=list)
    removed_libraries: list[str] = field(default_factory=list)
    added_libraries: list[str] = field(default_factory=list)
    # compare --used-by/--required-symbol(s) scoping (ADR-043): the headline
    # emoji/title and check gate follow *this* verdict when set, not the raw
    # bucket counts below (which stay the full, unscoped library diff, kept as
    # informational context per this module's own "content channel" design) —
    # otherwise a scoped-compatible run could render an alarming "ABI BREAKING"
    # headline that disagrees with the actual (scoped) exit code (Codex review).
    scoped_verdict: str | None = None
    full_verdict: str | None = None
    used_by_summaries: list[dict[str, object]] = field(default_factory=list)
    required_symbol_summary: dict[str, object] | None = None
    # "Reporting must survive suppression": how many findings a `--suppress`
    # rule removed from `changes` before this comment ever saw them, and how
    # many carry a `policy_overrides`-reclassified verdict (a custom
    # `--policy-file` re-classification moving a kind to a different verdict
    # bucket than its built-in default) — both silent otherwise, since
    # neither shows up in the three buckets above by construction.
    suppressed_count: int = 0
    reclassified_count: int = 0
    # ADR-067 D3: the JSON report's `disposition_audit` block verbatim (raw
    # versus effective totals, per-disposition counts, rule provenance), or
    # None for a report that predates schema 2.50. Carried as the wire dict
    # rather than a parsed struct for the same reason every other field here
    # is: this model is built from an already-serialized report.
    disposition_audit: dict[str, object] | None = None
    # scan mode only (see `pr_comment_scan.from_scan`): the raw
    # `scan_engine.ScanOutcome` verdict string
    # ("COMPATIBLE"/"API_BREAK"/"BREAKING"/"NOT_COMPARABLE"/…), the
    # risk-score dict (`RiskScore.to_dict()`), and a short per-layer
    # coverage summary line list -- rendered in their own "🔎 Scan"/
    # "📊 Coverage" lines (`pr_comment_scan.scan_note`) since none of
    # compare's model has an equivalent. `None` for every other mode.
    scan_verdict: str | None = None
    scan_risk: dict[str, object] | None = None
    scan_coverage_lines: list[str] = field(default_factory=list)
    # True when `scan --against` ran with no baseline at all (an audit-only
    # run, `diff` is `None`) -- the three compatibility buckets are
    # necessarily empty then, which would otherwise render the generic
    # "✅ No ABI changes" headline as if a baseline comparison had run and
    # found nothing.
    scan_audit_only: bool = False
    # scan mode only: the exact (breaking, needs-review) totals from
    # `diff`'s own scalar `breaking`/`api_break`/`risk` counts (already
    # gate/severity-promotion-adjusted -- see `pr_comment_scan._scan_true_
    # counts`), independent of whether `diff["findings"]` was truncated
    # below the report cap. `None` when `diff` carries no scalar counts to
    # read (audit-only / NOT_COMPARABLE) -- `counts` then falls back to the
    # classified-list lengths, same as every other mode (Codex review: a
    # scan `findings` list capped at the default 20 previously undercounted
    # the header for a diff with more real findings than that).
    scan_breaking_total: int | None = None
    scan_review_total: int | None = None
    # The exact "safe" (compatible) total -- `diff["additions"]`'s/
    # `diff["quality"]`'s own caps (Codex review, follow-up to the two
    # fields above) make `len(safe)` under-report a truncated list exactly
    # the same way the raw classified-list lengths did for breaking/review;
    # unlike those two, `pr_comment_scan.from_scan` derives this from the
    # exact `diff["compatible"]` scalar minus the severity-promoted
    # addition/quality category counts, rather than from either itemized
    # list's own length. An earlier revision derived this from
    # `diff["additions_total"]` (schema 1.13) alone, which missed every
    # compatible-but-non-addition ("quality") finding entirely. `None`
    # falls back to `len(self.safe)`, same convention as the two fields
    # above.
    scan_safe_total: int | None = None
    # Whether `diff["findings"]`/`diff["additions"]`/`diff["quality"]` were
    # themselves truncated below the report cap -- surfaced as an explicit
    # note (`pr_comment_scan.scan_note`) so "showing N of M" is never
    # silent, even though `scan_breaking_total`/`scan_review_total`/
    # `scan_safe_total` above keep the header counts exact regardless.
    scan_findings_truncated: bool = False
    scan_additions_truncated: bool = False
    scan_quality_truncated: bool = False
    # The exact analysis-incomplete total (Codex review, follow-up to the
    # three totals above): `diff["findings_truncated_kinds"]` can cut an
    # evidence-quality occurrence (e.g. 25 `source_fact_coverage_incomplete`
    # findings capped at 20) the same way it cuts an ordinary breaking/review
    # finding, but `model.incomplete` itself only ever holds what fit under
    # that same cap -- so `len(self.incomplete)` alone silently under-reports
    # while the truncation note next to it claims the counts above are exact.
    # `pr_comment_scan.from_scan` derives this from `model.incomplete`'s own
    # length plus the truncated ledger's evidence-kind cut count. `None`
    # falls back to `len(self.incomplete)`, same convention as the three
    # fields above (and the only value `compare`'s non-scan modes ever set).
    scan_incomplete_total: int | None = None

    @property
    def incomplete_total(self) -> int:
        """Exact analysis-incomplete count -- see `scan_incomplete_total`."""
        return (
            self.scan_incomplete_total
            if self.scan_incomplete_total is not None
            else len(self.incomplete)
        )

    @property
    def has_incomplete(self) -> bool:
        """Whether the analysis-incomplete bucket has anything to report --
        `bool(self.incomplete)` alone misses the case where the report cap
        truncated *every* analysis-incomplete finding, leaving the itemized
        list empty even though `incomplete_total` is exact and positive
        (Codex review: a scan with 20 breaking findings followed by 5
        cap-cut `source_fact_coverage_incomplete` findings has
        `incomplete_total == 5` but `incomplete == []`, so every headline/
        note/section check keyed on `bool(model.incomplete)` alone silently
        omitted the whole bucket -- next to a truncation note claiming the
        counts above were exact). Every such check should use this property
        instead of testing `model.incomplete` for truthiness.
        """
        return bool(self.incomplete) or self.incomplete_total > 0

    @property
    def counts(self) -> tuple[int, int, int]:
        """(breaking, needs-review, safe) totals across the report."""
        if self.mode == "release":
            return (
                sum(r[2] for r in self.library_rows),
                sum(r[3] for r in self.library_rows),
                sum(r[4] for r in self.library_rows),
            )
        if self.mode == "scan" and self.scan_breaking_total is not None:
            return (
                self.scan_breaking_total,
                self.scan_review_total or 0,
                self.scan_safe_total if self.scan_safe_total is not None else len(self.safe),
            )
        return len(self.breaking), len(self.review), len(self.safe)

    @property
    def total_changes(self) -> int:
        """Total number of changes across all three compatibility buckets,
        plus the analysis-incomplete bucket (so `should_post("changes")`
        still fires on a report that carries only a coverage gap)."""
        b, r, s = self.counts
        return b + r + s + self.incomplete_total


#: GitHub Actions' own checkout convention doubles the repo name as its own
#: last path component under a literal `work/` directory
#: (`/home/runner/work/<repo>/<repo>/…`). Anchored on the literal `work/`
#: segment (CodeRabbit review: an earlier revision matched *any* doubled
#: adjacent directory anywhere in an absolute path — `^/.*?/([^/]+)/\1/` —
#: which strips a genuine repository path like
#: `/srv/vendor/vendor/include/a.h` down to `include/a.h` too, discarding
#: real path components that just happen to repeat a directory name; the
#: comment above this constant claimed the un-anchored pattern "only ever
#: matches the runner's own checkout root", which was not actually true of
#: the regex as written). Recognizing this one, specific, unambiguous shape
#: is deliberately the *only* normalization applied — an earlier revision
#: also tried stripping whatever leading path segments happened to be
#: common across every finding in one report, which over-strips whenever
#: several findings legitimately share a real subdirectory (e.g. two
#: changes in the same `include/` tree): every row's `path:line` would
#: silently lose that shared directory context too, not just the
#: CI-specific root.
_CI_WORKDIR_RE = re.compile(r"^/.*?/work/([^/]+)/\1/")


def _normalize_location(raw: str) -> str:
    """Normalize a ``path[:line]`` location string for display.

    Strips a CI-runner-specific absolute checkout prefix so the comment
    shows a repo-relative path (``include/foo.h:10``) instead of e.g.
    ``/home/runner/work/abicheck/abicheck/include/foo.h:10`` — noise that
    tells a reviewer nothing they don't already know from the PR itself, and
    just makes every row harder to scan. A location not matching this one
    recognized CI-checkout shape is left exactly as the report gave it,
    rather than guessed at.
    """
    path, sep, rest = raw.rpartition(":")
    if not sep:
        path, rest = raw, ""
    match = _CI_WORKDIR_RE.match(path)
    if match:
        path = path[match.end() :]
    return f"{path}:{rest}" if rest else path


def _severity_levels(report: dict[str, object]) -> dict[str, str]:
    """Resolved per-category severity levels from the report, or ``{}``.

    Present when the comparison ran with a severity config (a ``severity:`` config /
    preset). A category set to ``error`` turns the check red, so the comment
    must file that category's findings under Breaking to match — this covers
    ``severity-addition: error`` and any preset/extra-arg path uniformly.
    """
    sev = report.get("severity")
    if isinstance(sev, dict):
        cfg = sev.get("config")
        if isinstance(cfg, dict):
            return {str(k): str(v) for k, v in cfg.items()}
    return {}


# Kind value strings that mean "this comparison's own evidence was degraded
# or incomplete" rather than "this is a compatibility finding" -- these
# describe the *comparison's* own evidence coverage, never a change to the
# library's own API/ABI, so none of them belong in the Breaking/Needs
# review/Safe compatibility buckets (Codex review: `source_fact_coverage_
# incomplete` and `dwarf_info_missing` are the same class of signal as the
# two kinds this set originally shipped with, and were previously left in
# Needs review/Safe -- exactly the confusion this bucket exists to remove).
# Shared here (not defined in ``pr_comment.py`` alone) because ``scan``'s own
# finding classification (``pr_comment_scan._scan_findings_to_buckets``)
# needs the identical exclusion -- a `scan --against` diff can carry these
# same kinds through its `diff.findings`/`diff.compatible` (they're ordinary
# ``Change`` objects from the same detector pipeline `compare` uses), and
# routing them through the generic severity-bucket logic there produced a
# misleading "Compatibility risk blocks this PR" headline for what is really
# a *missing-evidence* signal, not a detected API/ABI change (Codex review,
# follow-up).
#
# - `layer_coverage_asymmetric` — advisory RISK: the baseline was scanned
#   with an evidence layer the candidate lacks.
# - `evidence_required_missing` — a policy-declared-mandatory evidence layer
#   is absent (ADR-033 D7); one finding per missing layer.
# - `source_fact_coverage_incomplete` — advisory RISK: L4 source-fact
#   evidence used an incomplete or incompatible fact-set (ADR-038 C.8).
# - `dwarf_info_missing` — COMPATIBLE by default severity: struct/enum
#   layout comparison was skipped for lack of DWARF debug info.
_EVIDENCE_KIND_VALUES = frozenset(
    {
        "layer_coverage_asymmetric",
        "evidence_required_missing",
        "source_fact_coverage_incomplete",
        "dwarf_info_missing",
    }
)

# Friendlier default labels for the evidence-incomplete kinds above, used
# whenever the finding's own `symbol` is an internal marker rather than
# something naming a change in the library's surface (an empty string, a
# bracketed sentinel like "<dwarf>", or an "evidence:<layer>" key) — showing
# one of those verbatim in the Symbol column reads as internal-tool noise to
# a maintainer.
_EVIDENCE_KIND_DEFAULT_LABEL = {
    "layer_coverage_asymmetric": "Evidence coverage",
    "evidence_required_missing": "Required evidence",
    "source_fact_coverage_incomplete": "Source-fact coverage",
    "dwarf_info_missing": "Debug info coverage",
}

# `evidence_required_missing` emits one finding *per missing layer*
# (`evidence:build_context` / `evidence:source_abi` / `evidence:graph_summary`
# — see `buildsource/evidence_policy.py`'s `_REQUIRE_EVIDENCE_LAYERS`), so
# collapsing all of them to one flat "Required evidence" label would make the
# standard-detail renderer's own by-symbol grouping (`_group_by_api`) fold
# distinct layer requirements into a single row, hiding which layer(s) are
# actually missing (Codex review).
_EVIDENCE_LAYER_LABEL = {
    "build_context": "build context",
    "source_abi": "source ABI",
    "graph_summary": "source graph",
}


def _evidence_symbol_label(kind: str, raw_symbol: str) -> str:
    """Friendly, per-layer-distinct display label for an analysis-incomplete
    finding's ``symbol`` — see the two constants above."""
    if kind == "evidence_required_missing" and raw_symbol.startswith("evidence:"):
        layer_key = raw_symbol.split(":", 1)[1]
        layer_label = _EVIDENCE_LAYER_LABEL.get(layer_key, layer_key.replace("_", " "))
        return f"Required evidence: {layer_label}"
    return _EVIDENCE_KIND_DEFAULT_LABEL.get(kind, raw_symbol or kind)


#: Each evidence-quality kind's fixed default severity bucket, derived from
#: the same registry membership `BREAKING_KINDS`/`API_BREAK_KINDS`/
#: `RISK_KINDS`/`COMPATIBLE_KINDS` encode (this module already depends on
#: `checker_policy` for `ADDITION_KINDS` above) -- used by
#: `pr_comment_scan._scan_findings_evidence_kind_counts` to attribute a
#: *truncated* (cap-cut) evidence-kind occurrence to the right raw scalar
#: when no per-instance `bucket` field survived the cut. `dwarf_info_missing`
#: resolves to `"compatible"`, since it never reaches `diff["findings"]` at
#: all (only `diff["compatible"]`/`quality`).
_EVIDENCE_KIND_DEFAULT_BUCKET = {
    kind: (
        "breaking" if ChangeKind(kind) in BREAKING_KINDS
        else "api_break" if ChangeKind(kind) in API_BREAK_KINDS
        else "risk" if ChangeKind(kind) in RISK_KINDS
        else "compatible"
    )
    for kind in _EVIDENCE_KIND_VALUES
}


def _finding_category(severity: str, kind: str) -> str:
    """Map a finding's severity label + kind to a severity-config category."""
    if severity == "breaking":
        return "abi_breaking"
    if severity in ("api_break", "risk"):
        return "potential_breaking"
    if kind in _ADDITION_KIND_VALUES:
        return "addition"
    return "quality_issues"


def _demangle_symbol(raw: str, demangled_map: dict[str, str]) -> tuple[str, str]:
    """(display symbol, linker-evidence mangled symbol or "") for one
    change's raw ``symbol`` field.

    A function/variable change's ``Change.symbol`` is the raw Itanium-
    mangled linker name (``diff_symbols.py``), unreadable to a maintainer —
    the demangled signature is what they actually think in. The demangled
    form becomes the primary display value; the raw mangled name survives on
    ``Finding.mangled``, rendered as linker evidence (see ``_flat_row``). A
    non-C++ symbol, or an environment with no demangler available
    (``demangle_batch`` degrades to an empty map in that case, never
    raises), leaves *raw* as both the display value and yields no mangled
    evidence — there's nothing recovered to show separately.
    """
    demangled = demangled_map.get(raw)
    if demangled and demangled != raw:
        return demangled, raw
    return raw, ""


#: Human-readable labels for a CoverageFailure dict's "provider" field
#: (`contract_coverage_ledger.py`'s recorded provider names) — used only as
#: a fallback when a provider name isn't already self-explanatory; every
#: unrecognized provider still renders (just its raw string), never dropped.
_CONTRACT_PROVIDER_LABEL = {
    "public_header": "public header",
    "export_table": "export table",
    "post_manifest": "post-build manifest",
    "forced_public_symbols": "forced-public-symbols overlay",
}


def _contract_coverage_findings(report: dict[str, object]) -> list[Finding]:
    """Build analysis-incomplete findings from ``report["contract_coverage_
    failures"]`` (ADR-049 Phase 5's unsuppressible sibling ledger, emitted
    only under ``compare --contract``).

    Codex review: this ledger is deliberately kept *outside*
    ``DiffResult.changes`` — see ``AGENTS.md``'s own contract_coverage_
    ledger.py entry ("a `CoverageFailure` is not a `Change`... so
    `checker._filter_suppressed_changes`... cannot see one") — so a report
    whose *only* problem is incomplete contract-evidence coverage carries
    zero entries in ``changes`` even though ``contract_coverage_exit_
    contribution`` already folded a real gate failure into the exit code.
    Reading only ``changes`` (this module's original design, before this
    ledger existed) therefore missed it entirely: under the default
    ``--on=changes`` policy, `should_post` sees `total_changes == 0` and
    posts no comment at all — deleting a prior sticky comment if one existed
    — while under ``--on=always`` it renders the actively misleading "✅ No
    ABI changes" next to a check that already failed.

    Always ``[]`` for a report that never ran ``--contract``
    (the key is entirely absent then, never an empty list emitted for a
    different reason — ``reporter.py`` only emits this key when
    ``result.contract_context`` is not ``None``).
    """
    raw = report.get("contract_coverage_failures")
    if not isinstance(raw, list):
        return []
    findings: list[Finding] = []
    for f in raw:
        if not isinstance(f, dict):
            continue
        provider = str(f.get("provider", "?"))
        provider_label = _CONTRACT_PROVIDER_LABEL.get(provider, provider)
        side = str(f.get("side", "?"))
        mode = str(f.get("mode", "") or "")
        reason = str(f.get("reason", "") or "")
        status = str(f.get("status", "") or "")
        completeness = str(f.get("completeness", "") or "")
        detail_parts = [p for p in (reason, status, completeness) if p]
        findings.append(
            Finding(
                kind="contract_coverage_failure",
                symbol=f"Contract evidence: {provider_label} ({side})",
                detail=" — ".join(detail_parts) if detail_parts else "",
                location=f"[{mode}]" if mode else None,
            )
        )
    return findings


def _breaking_categories(findings: list[Finding]) -> frozenset[str]:
    """The set of severity-config categories present in a Breaking bucket."""
    return frozenset(f.category for f in findings if f.category)


def _breaking_severities(findings: list[Finding]) -> frozenset[str]:
    """The set of raw severities present in a Breaking bucket.

    Resolves the "potential_breaking" category (which "api_break" and "risk"
    both map to) back to which severity is actually responsible, so the
    headline can tell a real source break apart from a risk promoted to
    blocking.
    """
    return frozenset(f.severity for f in findings if f.severity)



def _esc(value: object) -> str:
    # Sanitise for a single markdown table cell: escape pipes, neutralise
    # backticks (which would break the surrounding code span) and flatten
    # newlines. C/C++ symbols never contain backticks, so this is defensive.
    #
    # A lone `\r` is flattened too (Codex review, follow-up to the
    # backtick/`\n` injection fix): CommonMark treats a bare carriage return
    # as a line ending exactly like `\n`, so an untrusted value containing
    # one (e.g. the Action's `--subject`, a scanned-artifact basename) could
    # still terminate the header's code-span line and inject Markdown even
    # after `\n` alone was neutralized.
    return (
        str(value)
        .replace("|", "\\|")
        .replace("`", "ˋ")
        .replace("\r\n", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )

