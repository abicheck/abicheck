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

from .checker_policy import ADDITION_KINDS

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
    # Whether `diff["findings"]`/`diff["additions"]` were themselves
    # truncated below the report cap -- surfaced as an explicit note
    # (`pr_comment_scan.scan_note`) so "showing N of M" is never silent,
    # even though `scan_breaking_total`/`scan_review_total` above keep the
    # header counts exact regardless.
    scan_findings_truncated: bool = False
    scan_additions_truncated: bool = False

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
                len(self.safe),
            )
        return len(self.breaking), len(self.review), len(self.safe)

    @property
    def total_changes(self) -> int:
        """Total number of changes across all three compatibility buckets,
        plus the analysis-incomplete bucket (so `should_post("changes")`
        still fires on a report that carries only a coverage gap)."""
        b, r, s = self.counts
        return b + r + s + len(self.incomplete)


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

    Present when the comparison ran with a severity config (``--severity-*`` /
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
    only under ``compare --contract-evaluation``).

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

    Always ``[]`` for a report that never ran ``--contract-evaluation``
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
    return (
        str(value)
        .replace("|", "\\|")
        .replace("`", "ˋ")
        .replace("\n", " ")
        .strip()
    )

