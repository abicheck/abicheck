# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""``deps compare``/``deps tree``'s JSON projection (ADR-068 D6,
``one-comparison-product.md`` Phase 8).

Moved out of ``abicheck/stack_report.py`` (a formatting module, not a report
one) so the JSON shape is built once here as a plain, JSON-safe mapping and
then wrapped in the canonical :class:`~abicheck.report.document.ReportDocument`
-- the same construction/rendering split every other command's JSON output
already goes through (``abicheck/report/AGENTS.md``'s "Canonical entry
points"). ``stack_report.stack_to_json`` is now a thin
``render_json(compute_stack_report_document(result))`` wrapper; its Markdown
renderer imports the shared dict-building helpers below rather than keeping
its own copy.

``deps``'s report predates ADR-061's compute/render split and has no
per-section ``compute_*`` struct family of its own (unlike
``html_report.py``/``reporter_markdown.py``) -- this module gives its JSON
shape a single owner without attempting a wholesale Markdown/HTML rewrite,
which is out of this phase's scope (ADR-068 D6: internal convergence only,
no user-visible change).
"""

from __future__ import annotations

from ..binder import SymbolBinding
from ..resolver import DependencyGraph
from ..stack_checker import StackCheckResult
from .document import ReportDocument

#: Cap on embedded per-library findings in stack JSON -- mirrors
#: `cli_scan_baseline._MAX_BASELINE_FINDINGS`'s rationale: a large diff must
#: not blow up the always-on stack-check output, but a bare count
#: (`abi_breaking: 3`) leaves no way to tell *which* symbols broke without a
#: separate `compare` run.
MAX_STACK_FINDINGS_PER_LIBRARY = 10


def stack_finding_dicts(diff: object) -> list[dict[str, object]]:
    """Project a library's gating findings (breaking/api_break/risk) into
    small, capped dicts -- same shape as `cli_scan_baseline._baseline_finding_dicts`.

    Counts (not already-built dicts) decide the cap so a large diff never
    builds more dicts than the cap can ever keep.
    """
    findings: list[dict[str, object]] = []
    for bucket_name, bucket_changes in (
        ("breaking", getattr(diff, "breaking", [])),
        ("api_break", getattr(diff, "source_breaks", [])),
        ("risk", getattr(diff, "risk", [])),
    ):
        remaining = MAX_STACK_FINDINGS_PER_LIBRARY - len(findings)
        if remaining <= 0:
            break
        for c in bucket_changes[:remaining]:
            kind = getattr(c, "kind", None)
            findings.append(
                {
                    "bucket": bucket_name,
                    "kind": getattr(kind, "value", str(kind)),
                    "symbol": getattr(c, "symbol", None),
                    "description": getattr(c, "description", None),
                    "source_location": getattr(c, "source_location", None),
                }
            )
    return findings


def graph_to_dict(graph: DependencyGraph) -> dict[str, object]:
    """Convert a DependencyGraph to a JSON-serializable dict."""
    return {
        "root": graph.root,
        "node_count": graph.node_count,
        "nodes": [
            {
                "path": str(node.path),
                "soname": node.soname,
                "needed": node.needed,
                "depth": node.depth,
                "resolution_reason": node.resolution_reason,
            }
            for node in sorted(graph.nodes.values(), key=lambda n: (n.depth, n.soname))
        ],
        "edges": [
            {"consumer": consumer, "provider": provider}
            for consumer, provider in graph.edges
        ],
        "unresolved": [
            {"consumer": consumer, "soname": soname}
            for consumer, soname in graph.unresolved
        ],
    }


def bindings_summary(bindings: list[SymbolBinding]) -> dict[str, int]:
    """Count bindings by status."""
    summary: dict[str, int] = {}
    for b in bindings:
        key = b.status.value
        summary[key] = summary.get(key, 0) + 1
    return summary


#: Stated wherever a `deps` projection prints an environment root that the
#: caller never named (`one-comparison-product.md` Phase 7l). `/` on its own
#: is indistinguishable from a deliberately chosen deployment root, which is
#: the whole defect: the analysis is unchanged, what it was run *against*
#: was a fallback, and the report has to say so.
DEFAULTED_ROOT_NOTE = (
    "defaulted -- the current host filesystem, not a chosen deployment "
    "environment"
)


def environment_root_label(path: str, defaulted: bool) -> str:
    """*path* as a projection should show it, annotated when it was
    defaulted rather than chosen. One helper so the JSON, Markdown and HTML
    projections cannot disagree about when the note applies."""
    return f"{path} ({DEFAULTED_ROOT_NOTE})" if defaulted else path


def compute_stack_report_mapping(result: StackCheckResult) -> dict[str, object]:
    """The plain, JSON-safe mapping `deps compare`/`deps tree` report as
    JSON -- unchanged from `stack_report.stack_to_json`'s pre-convergence
    dict-building, just relocated so it has one owner.
    """
    d: dict[str, object] = {
        "root_binary": result.root_binary,
        "baseline_env": result.baseline_env,
        "candidate_env": result.candidate_env,
        # A consumer reading `baseline_env: "/"` cannot otherwise tell a
        # deliberately chosen host-root comparison from one that simply had
        # no root named. Always emitted (not only when true), so its absence
        # can never be read as "not defaulted" by a report this tool did not
        # produce.
        "baseline_env_defaulted": result.baseline_env_defaulted,
        "candidate_env_defaulted": result.candidate_env_defaulted,
        "verdict": {
            "loadability": result.loadability.value,
            "abi_risk": result.abi_risk.value,
            "risk_score": result.risk_score,
        },
    }

    # Dependency graph nodes.
    d["baseline_graph"] = graph_to_dict(result.baseline_graph)
    if result.baseline_graph is not result.candidate_graph:
        d["candidate_graph"] = graph_to_dict(result.candidate_graph)

    # Binding summary.
    d["bindings_summary"] = bindings_summary(result.bindings_candidate)

    # Missing symbols.
    if result.missing_symbols:
        d["missing_symbols"] = [
            {
                "consumer": b.consumer,
                "symbol": b.symbol,
                "version": b.version,
                "explanation": b.explanation,
            }
            for b in result.missing_symbols
        ]

    # Unresolved DSOs.
    if result.candidate_graph.unresolved:
        d["unresolved_libraries"] = [
            {"consumer": consumer, "soname": soname}
            for consumer, soname in result.candidate_graph.unresolved
        ]

    # Stack changes (two-env mode).
    if result.stack_changes:
        sc_list = []
        for sc in result.stack_changes:
            sc_dict: dict[str, object] = {
                "library": sc.library,
                "change_type": sc.change_type,
                "abi_verdict": sc.abi_diff.verdict.value if sc.abi_diff else None,
                "abi_breaking": len(sc.abi_diff.breaking) if sc.abi_diff else 0,
                "abi_changes": len(sc.abi_diff.changes) if sc.abi_diff else 0,
            }
            # ADR-050 D2 -- distinguishes "the gate rejected this pair" from
            # every other abi_diff=None cause (unreadable file, diff error).
            if sc.not_comparable_reason:
                sc_dict["not_comparable_reason"] = sc.not_comparable_reason
            # Per-library confidence and evidence tiers
            if sc.abi_diff:
                diff = sc.abi_diff
                # Preserve the actual findings (kind/symbol/description/
                # location), not just their counts -- a stack check used to
                # report e.g. "abi_breaking: 3" for a library with no way to
                # tell which symbols broke without a separate `compare` run.
                total_gating = (
                    len(diff.breaking) + len(diff.source_breaks) + len(diff.risk)
                )
                stack_findings = stack_finding_dicts(diff)
                if stack_findings:
                    sc_dict["findings"] = stack_findings
                    if total_gating > MAX_STACK_FINDINGS_PER_LIBRARY:
                        sc_dict["findings_truncated"] = True
                conf = getattr(diff, "confidence", None)
                if conf is not None:
                    sc_dict["confidence"] = (
                        conf.value if hasattr(conf, "value") else str(conf)
                    )
                tiers = getattr(diff, "evidence_tiers", []) or []
                if tiers:
                    sc_dict["evidence_tiers"] = list(tiers)
                cov_warns = getattr(diff, "coverage_warnings", []) or []
                if cov_warns:
                    sc_dict["coverage_warnings"] = list(cov_warns)
                # File metadata for traceability
                old_meta = getattr(diff, "old_metadata", None)
                new_meta = getattr(diff, "new_metadata", None)
                if old_meta:
                    sc_dict["old_file"] = {
                        "path": getattr(old_meta, "path", ""),
                        "sha256": getattr(old_meta, "sha256", ""),
                        "size_bytes": getattr(old_meta, "size_bytes", 0),
                    }
                if new_meta:
                    sc_dict["new_file"] = {
                        "path": getattr(new_meta, "path", ""),
                        "sha256": getattr(new_meta, "sha256", ""),
                        "size_bytes": getattr(new_meta, "size_bytes", 0),
                    }
            sc_list.append(sc_dict)
        d["stack_changes"] = sc_list

    # Runtime binding-provider changes (cross-environment rebound).
    if result.binding_changes:
        d["binding_changes"] = [
            {
                "kind": bc.kind.value,
                "symbol": bc.symbol,
                "description": bc.description,
                "old_value": bc.old_value,
                "new_value": bc.new_value,
            }
            for bc in result.binding_changes
        ]

    return d


def compute_stack_report_document(result: StackCheckResult) -> ReportDocument:
    """`deps compare`/`deps tree`'s JSON report as a canonical
    :class:`~abicheck.report.document.ReportDocument` -- the compute half of
    the compute/render pair; `stack_report.stack_to_json` is the render half
    (via `report.render_json.render_json`)."""
    return ReportDocument.from_mapping(compute_stack_report_mapping(result))
