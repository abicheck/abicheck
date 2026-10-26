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
``buildsource/cross_source_checks_base.py`` already uses for the identical reason
(see its own module docstring): either sibling can import from here without
forming an import cycle, since nothing here imports either of them back.

Re-exports the mode-agnostic data model (:class:`Finding`, :class:`CommentModel`,
owned by :mod:`abicheck.report.pr_comment_model`) and holds the small, pure
classification/formatting primitives both report shapes
(``compare``/``appcompat``/``release`` in ``pr_comment.py``, ``scan`` in
``pr_comment_scan.py``) need identically: severity/category classification,
symbol demangling, location normalisation, the contract-coverage-ledger
finding builder. Markdown cell escaping (``escape_cell``) lives with the
model it serves, in :mod:`abicheck.report.pr_comment_model`.
"""

from __future__ import annotations

import re

from .model.change_catalog.assembled import API_BREAK_KINDS, BREAKING_KINDS, RISK_KINDS
from .model.change_catalog.kinds import ChangeKind
from .policy.classification import ADDITION_KINDS, INFORMATIONAL_KINDS
from .report.pr_comment_model import (
    CommentModel as CommentModel,
    Finding as Finding,
    escape_cell as escape_cell,
)

# Kind value strings that constitute new public-API surface (the severity
# "addition" category). Sourced from the authoritative ADDITION_KINDS so kinds
# that don't end in "_added" (e.g. type_field_added_compatible,
# experimental_graduated) are classified correctly.
_ADDITION_KIND_VALUES = frozenset(k.value for k in ADDITION_KINDS)
# The severity "informational" category (ADR-078): never gates.
_INFORMATIONAL_KIND_VALUES = frozenset(k.value for k in INFORMATIONAL_KINDS)


# Severity tokens emitted in the JSON report (`reporter._effective_severity_label`)
# routed into the three reviewer-facing buckets.
_SEVERITY_BUCKET = {
    "breaking": "breaking",
    "api_break": "review",
    "risk": "review",
    "compatible": "safe",
    "unknown": "review",
}


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


def _normalize_location(raw: str, path_prefix: str = "") -> str:
    """Normalize a ``path[:line]`` location string for display.

    Strips *path_prefix* (an explicitly supplied checkout root, e.g. the
    Action's ``$GITHUB_WORKSPACE``) when the location is under it, and
    otherwise a CI-runner-specific absolute checkout prefix, so the comment
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
    # An explicitly supplied checkout root wins over the guessed CI shape:
    # the caller *knows* where the tree is (the Action passes
    # $GITHUB_WORKSPACE), so there is nothing to infer and no way to
    # over-strip. Applied only as a whole leading path component, so a
    # sibling directory sharing the root's name prefix is never cut.
    #
    # Both sides are compared with separators normalized, because on a
    # Windows runner they genuinely differ: `$GITHUB_WORKSPACE` is native
    # (`D:\a\repo\repo`) while a header-AST backend reports
    # `source_location` with forward slashes. Comparing them raw made the
    # prefix never match there, leaving the absolute runner path in the
    # comment -- the exact thing this strip exists to remove (CodeRabbit
    # review). The *returned* remainder is sliced out of the original
    # string, so nothing else about the path's spelling is rewritten.
    if path_prefix:
        root = path_prefix.replace("\\", "/").rstrip("/") + "/"
        if path.replace("\\", "/").startswith(root):
            trimmed = path[len(root) :]
            return f"{trimmed}:{rest}" if rest else trimmed
    match = _CI_WORKDIR_RE.match(path)
    if match:
        path = path[match.end() :]
    return f"{path}:{rest}" if rest else path


def _component_key(f: Finding, key: str) -> str:
    """*key* qualified by the finding's component, when it has one.

    A folded (``mode="aggregate"``) model carries findings from several
    targets in one bucket, and the same symbol legitimately appears once per
    target. Grouping on the bare key would collapse those into one row whose
    member list reads ```foo`, `foo`, `foo``` -- three identical names and no
    way to tell which target each came from -- so the component is part of
    the grouping key, not merely a decoration on the rendered row. Unchanged
    (``key`` itself) for every single-report mode, where ``component`` is
    always empty.
    """
    return f"{f.component} · {key}" if f.component else key


def _component_tag(f: Finding) -> str:
    """Per-target attribution for one rendered row, or ``""``.

    Rendered in the Symbol column rather than appended to the detail text:
    the detail cell is the first thing a row budget shortens, and *which
    target reported this* is identity, not detail -- a row that loses it
    stops being attributable at all.
    """
    return f"<br><sub>{escape_cell(f.component)}</sub>" if f.component else ""


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
        "breaking"
        if ChangeKind(kind) in BREAKING_KINDS
        else "api_break"
        if ChangeKind(kind) in API_BREAK_KINDS
        else "risk"
        if ChangeKind(kind) in RISK_KINDS
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
    if kind in _INFORMATIONAL_KIND_VALUES:
        return "informational"
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
#: (`policy/coverage_ledger.py`'s recorded provider names) — used only as
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
