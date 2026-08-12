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

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .checker_policy import ADDITION_KINDS
from .demangle import demangle_batch

# Kind value strings that constitute new public-API surface (the severity
# "addition" category). Sourced from the authoritative ADDITION_KINDS so kinds
# that don't end in "_added" (e.g. type_field_added_compatible,
# experimental_graduated) are classified correctly.
_ADDITION_KIND_VALUES = frozenset(k.value for k in ADDITION_KINDS)

# Kind value strings that mean "this comparison's own evidence was degraded
# or incomplete" rather than "this is a compatibility finding" — see the
# module docstring's "analysis incomplete" bucket. Every kind here describes
# the *comparison's* own evidence coverage, never a change to the library's
# own API/ABI, so none of them belong in the Breaking/Needs review/Safe
# compatibility buckets (Codex review: `source_fact_coverage_incomplete` and
# `dwarf_info_missing` are the same class of signal as the two kinds this set
# originally shipped with, and were previously left in Needs review/Safe —
# exactly the confusion this bucket exists to remove):
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

# Hidden marker used to find-and-update the sticky comment across runs.
MARKER = "<!-- abicheck-sticky-report -->"

DETAIL_LEVELS = ("summary", "standard", "full")
POST_MODES = ("always", "changes", "never")

# Severity tokens emitted in the JSON report (`reporter._effective_severity_label`)
# routed into the three reviewer-facing buckets.
_SEVERITY_BUCKET = {
    "breaking": "breaking",
    "api_break": "review",
    "risk": "review",
    "compatible": "safe",
    "unknown": "review",
}

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

    @property
    def counts(self) -> tuple[int, int, int]:
        """(breaking, needs-review, safe) totals across the report."""
        if self.mode == "release":
            return (
                sum(r[2] for r in self.library_rows),
                sum(r[3] for r in self.library_rows),
                sum(r[4] for r in self.library_rows),
            )
        return len(self.breaking), len(self.review), len(self.safe)

    @property
    def total_changes(self) -> int:
        """Total number of changes across all three compatibility buckets,
        plus the analysis-incomplete bucket (so `should_post("changes")`
        still fires on a report that carries only a coverage gap)."""
        b, r, s = self.counts
        return b + r + s + len(self.incomplete)


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
    ``--severity-*`` flags; *levels* is ``{}``) maps every verdict to a
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
    return CommentModel(
        mode="release",
        subject=f"{n_libs} librar{'y' if n_libs == 1 else 'ies'}",
        old_label=_basename(report.get("old_dir", "old")),
        new_label=_basename(report.get("new_dir", "new")),
        policy="strict_abi",
        library_rows=rows,
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
    if model.scoped_verdict is not None and not model.contract_coverage_blocking:
        # `contract_coverage_blocking` is deliberately checked here, not
        # just later at the ordinary `incomplete_blocking` branch (Codex
        # review): the contract-coverage axis folds into the real exit code
        # unconditionally, on top of *any* other verdict including a scoped
        # one — a --used-by/--required-symbol run reporting a scoped
        # COMPATIBLE verdict does not silence an orthogonal coverage
        # failure that already turned the real exit code non-zero. When
        # both are present, fall through to the rest of this function
        # instead of returning the scoped header early, so a genuine
        # full-library break (checked next) still wins if present, and the
        # incomplete-blocking branch below is reached otherwise.
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
    if model.incomplete and model.incomplete_blocking:
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
    if model.incomplete:
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
) -> list[str]:
    if not findings:
        return []
    is_open = " open" if (detail == "full" or open_default) else ""
    out = [
        f"<details{is_open}><summary>{title} ({len(findings)})</summary>",
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
    context = (
        f"{head_ref} vs `{model.old_label}` · `{model.policy}` · `{model.subject}`"
    )
    counts_line = f"**{b} breaking** · {r} needs review · {s} safe"
    # The incomplete count is a distinct axis (analysis quality, not
    # compatibility — see module docstring) and only shown when non-zero, so
    # every existing report's summary line is unchanged.
    if model.incomplete:
        counts_line += f" · {len(model.incomplete)} analysis incomplete"
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


def _incomplete_note(model: CommentModel) -> list[str]:
    """Explain the analysis-incomplete bucket when it did *not* win the
    headline — a genuine breaking finding, or (for a merely-advisory
    coverage gap) a real review finding, took priority instead (see
    `_header`) — so a reviewer looking at an "ABI BREAKING" or "Review
    recommended" headline still learns the analysis itself was also
    degraded, rather than discovering it only in the collapsed details
    section below.
    """
    if not model.incomplete:
        return []
    if not model.breaking and not (model.review and not model.incomplete_blocking):
        return []
    n = len(model.incomplete)
    word = "finding" if n == 1 else "findings"
    return [
        f"> 🛑 {n} analysis-coverage {word} below — some real changes may not "
        f"be detectable with the evidence this comparison had available.",
        "",
    ]


def _body_sections(model: CommentModel, detail: str) -> list[str]:
    if model.mode == "release":
        return _release_table(model, detail)
    cats = model.breaking_categories
    breaking_title = (
        "❌ Breaking"
        if (not cats or "abi_breaking" in cats or "potential_breaking" in cats)
        else "⛔ Blocked by policy (compatible)"
    )
    out = _findings_table(
        breaking_title, model.breaking, detail, open_default=bool(model.breaking)
    )
    out += _findings_table(
        "🛑 Analysis incomplete",
        model.incomplete,
        detail,
        open_default=(not model.breaking and bool(model.incomplete)),
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
