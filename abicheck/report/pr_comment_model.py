# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Mode-agnostic PR-comment model: the one owner of :class:`Finding` and
:class:`CommentModel`.

Moved here from the root ``pr_comment_base`` so the ``report`` package (the
aggregate builder and its member/headline/group projections) imports its
model from inside its own layer instead of reaching up into the renderer
package. ``pr_comment_base`` re-exports both names, so existing imports keep
resolving. :func:`escape_cell` is the markdown table-cell sanitiser the group
summary needs from the same leaf; it is a pure string function with no model
dependency, kept here only so ``report`` does not import the renderer for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .change_summary import ChangeSummary
from .evidence_summary import EvidenceSummary


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
    # `impact` field (`model/change_catalog/assembled.py`'s `impact=` on the matching
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
    # Which component/target this finding came from, for a model folded out
    # of several reports (`mode="aggregate"`, see
    # `report/pr_comment_aggregate.py`). Empty for every single-report mode,
    # where the whole comment already names one subject.
    #
    # Load-bearing for grouping, not only for display: the renderer keys its
    # API rollup on (component, api) rather than api alone, so the *same*
    # symbol reported by three targets stays three attributable rows instead
    # of collapsing into one row that lists the name three times and says
    # nothing about which target each came from.
    component: str = ""
    # ADR-068 D3's `cross_source_evolution` state, carried verbatim from the
    # report: "introduced" | "resolved" | "persistent" | "not_evaluated",
    # or "" for a finding the comparison layer did not stamp (every ordinary
    # two-sided diff finding). Read, never re-derived -- the comparison
    # layer is the only thing that can establish whether a one-sided hygiene
    # check's finding is new, and this renderer may not form a second
    # opinion about it (ADR-072 D1).
    evolution: str = ""


@dataclass
class CommentModel:
    """Mode-agnostic view of a report, ready to render.

    A plain data container aggregating the report's header fields, the three
    reviewer buckets, and the release-mode rollup.
    """

    mode: str  # "compare" | "release" | "appcompat" | "aggregate"
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
    # ADR-068 D3: cross-source hygiene findings this comparison established
    # were **not introduced by it** -- present identically on both sides
    # (`persistent`) or present only on the baseline (`resolved`). They are
    # a standing property of the library, not something a pull request did,
    # so they are kept out of every compatibility bucket and out of
    # `counts`/`total_changes` entirely: a byte-identical rebuild whose
    # library carries 336 template-instantiation guard variables must
    # publish *nothing* under `--on=changes`, and when there is a real
    # change beside them they must read as background rather than as this
    # change's own modifications.
    #
    # `not_evaluated` deliberately does NOT land here: "one side's evidence
    # could not confirm or deny this" is an analysis limitation, and it goes
    # to `incomplete` where limitations belong.
    background: list[Finding] = field(default_factory=list)
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
    # even ahead of a `--used-by`/`consumers.required_symbols` scoped verdict — a
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
    # ADR-065 S2 (release mode). `removed_libraries`/`added_libraries` above
    # hold the *inventory-proven* sets when the report carries a
    # `comparison_scope` block (D2: unmatched is not removed); these two are
    # the raw `unmatched_old`/`unmatched_new` lists, reported as such.
    unmatched_old: list[str] = field(default_factory=list)
    unmatched_new: list[str] = field(default_factory=list)
    # `report.comparison_scope.comparison_scope_notice`'s one-line summary
    # of an incompletely checked scope (`None` when fully checked), whether
    # the scope axis actually contributed to the exit code (`block`, or a
    # zero-comparison run), and D7's "no comparison completed" itself.
    scope_notice: str | None = None
    scope_blocking: bool = False
    no_comparison_completed: bool = False
    # compare --used-by/consumers.required_symbols (ADR-043; workstream D-S1
    # reverted the earlier "headline follows the scoped verdict" design): a
    # supplied consumer's own confirmed/potential/unresolved assessment
    # (`scoped_verdict`) is purely informational, rendered beside the
    # headline (`_scoped_notes`), never in place of it -- the headline
    # emoji/title and this comment's check gate always follow the full-library
    # result (`full_verdict`, and the raw bucket counts below).
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
    review_groups: list[dict[str, object]] = field(default_factory=list)
    result_counts: dict[str, int] | None = None
    # scan mode only (see `pr_comment_scan.from_scan`): the raw
    # (retired) `scan_engine.ScanOutcome` verdict string
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
    # True for a `compare --no-baseline` audit report
    # (`pr_comment._from_no_baseline`) -- unlike `scan_audit_only` above
    # (legacy `scan`'s own now-Action-unreachable audit shape), a
    # no-baseline audit can carry real candidate-side findings bucketed by
    # `_bucket_changes` the ordinary way, so the empty-bucket check
    # `scan_audit_only` relies on isn't enough to gate this special-case
    # headline on its own (Codex review, PR #1210, round 3): reusing the
    # two-sided "ABI BREAKING"/"Source API changed; binary ABI unchanged"
    # wording for a run that never compared two versions falsely implies a
    # before/after result. `_header` checks this flag first and renders a
    # dedicated audit headline instead, for every finding count including
    # zero, driven by `no_baseline_audit_blocking` below rather than bucket
    # membership.
    no_baseline_audit: bool = False
    # Whether THIS run's overall CLI exit code was nonzero -- read directly
    # from the report's own top-level `exit_code` (the already-max-folded
    # result across every orthogonal axis: audit_gate, contract_coverage,
    # analysis_assurance, evidence_contract, ...), never re-derived from one
    # axis or from bucket/severity membership here (Codex review, PR #1210,
    # round 4: an earlier revision checked only `exit_axes.audit_gate`,
    # which missed a run blocked by, e.g., `contract_coverage` alone --
    # "read, don't re-derive" per this repository's own known-gaps
    # convention). A finding can land in the Review bucket (an
    # api_break-severity finding, `_SEVERITY_BUCKET`) without this being
    # `True` at all (no severity-preset given, or an explicit `info-only`)
    # -- the PR comment must not imply the step failed when it didn't, and
    # must not imply it merely reviewed a case when the run actually failed.
    no_baseline_audit_blocking: bool = False
    # Whether `policy/audit_gate_exit.py`'s own axis specifically is what
    # fired (read from `exit_axes.audit_gate`) -- distinct from the general
    # `no_baseline_audit_blocking` above, which can be `True` for a
    # different reason (e.g. `--contract`'s coverage axis). Used only to
    # pick the more specific "Audit gate: ..." wording when it applies;
    # `no_baseline_audit_blocking` alone still governs whether the headline
    # is blocking at all.
    no_baseline_audit_gate_fired: bool = False
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

    # ADR-065 S2 (release mode): each `comparison_scope.members` row's
    # acquisition state by display name, so the unmatched note can say why
    # a member has no counterpart (`not_supplied`, `failed`, ...) instead of
    # asserting one reason for all of them (Codex review). Declared last so
    # a positional caller keeps binding the older tail.
    unmatched_states: dict[str, str] = field(default_factory=dict)

    # The report's own evidence/coverage facts, read (never recomputed) by
    # `report/evidence_summary.py`. `None` only for a shape that carries
    # none at all; an *empty* summary is a real answer ("this report stated
    # no evidence facts") and stays distinguishable from a rich one. Never
    # defaulted to a reassuring value -- see that module's own docstring.
    evidence: EvidenceSummary | None = None
    # The report's entity-by-operation rollup (`report/change_summary.py`),
    # computed from the *authoritative* finding list before any grouping or
    # display cap this renderer applies, and carrying its own `exact` flag
    # for a shape whose list was already truncated upstream (a release
    # report's capped per-library `findings` sample). `None` when no such
    # list exists to summarize.
    change_summary: ChangeSummary | None = None

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
        if self.mode in ("release", "aggregate"):
            # `aggregate` shares release mode's rollup source deliberately:
            # its per-target rows are built from each member model's own
            # `counts`, and a member that is itself a *release* report has
            # populated rows but empty `breaking`/`review`/`safe` lists --
            # so summing the folded lists would report zero for exactly the
            # member shape that already reports only in aggregate.
            return (
                sum(r[2] for r in self.library_rows),
                sum(r[3] for r in self.library_rows),
                sum(r[4] for r in self.library_rows),
            )
        if self.mode == "scan" and self.scan_breaking_total is not None:
            return (
                self.scan_breaking_total,
                self.scan_review_total or 0,
                self.scan_safe_total
                if self.scan_safe_total is not None
                else len(self.safe),
            )
        return len(self.breaking), len(self.review), len(self.safe)

    @property
    def background_counts(self) -> tuple[int, int]:
        """``(persistent, resolved)`` over the pre-existing-hygiene bucket.

        Reported separately because the two say different things: one is
        standing debt both sides carry, the other is debt the baseline had
        and the candidate no longer does. Neither is a change this
        comparison's operands introduced, which is why neither reaches
        :attr:`counts`.
        """
        return (
            sum(1 for f in self.background if f.evolution == "persistent"),
            sum(1 for f in self.background if f.evolution == "resolved"),
        )

    @property
    def total_changes(self) -> int:
        """Total number of changes across all three compatibility buckets,
        plus the analysis-incomplete bucket (so `should_post("changes")`
        still fires on a report that carries only a coverage gap)."""
        b, r, s = self.counts
        return b + r + s + self.incomplete_total


def escape_cell(value: object) -> str:
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
