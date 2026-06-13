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

"""ADR-033 D7/D9 evidence-policy + metrics helpers.

Split out of ``cli_buildsource.py`` to keep that file under the file-size cap.
Pure helpers (no Click commands): the D7 verdict-modulation knobs
(``source_only_findings`` / ``build_context_drift`` / ``graph_risk_findings``),
the ``require_evidence`` gate, and the D9 metrics formatting. Imported by
``cli_buildsource.diff_embedded_build_source`` / ``attach_evidence_metrics``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from .model import CoverageStatus, DataLayer, LayerCoverage

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..policy_file import PolicyFile
    from .pack import BuildSourcePack


# ── D7 verdict modulation ────────────────────────────────────────────────────

# Build-context findings that reflect a genuinely ABI-relevant change
# (std/visibility/packing flags, export policy, toolchain). The
# ``build_context_drift: fail-on-abi-relevant`` knob only escalates these; the
# rest (generated-file churn, parse-context drift) stay deployment risks.
_ABI_RELEVANT_BUILD_KINDS: frozenset[str] = frozenset({
    "abi_relevant_build_flag_changed",
    "link_export_policy_changed",
    "toolchain_version_changed",
})


def tag_evidence_category(findings: list[Change], bucket: str) -> None:
    """Tag each finding with its D9 metric *bucket* (``build_context`` /
    ``source_only``) so the metrics can count *retained* findings per bucket
    after suppression. Done unconditionally, independent of any policy knob."""
    for change in findings:
        change.evidence_category = bucket


def apply_evidence_policy(
    findings: list[Change], category: str, policy_file: PolicyFile | None
) -> None:
    """Apply the ADR-033 D7 category verdict ceiling to evidence *findings*.

    Sets ``Change.effective_verdict`` so the verdict pipeline, reporter, and both
    exit-code paths honour the demotion/escalation consistently (the same hook
    the ADR-027 pattern pass uses). No-op when no policy file or knob is set, so
    default behaviour and the FP-rate gate are untouched.
    """
    if policy_file is None:
        return
    for change in findings:
        abi_relevant = getattr(change, "kind", None) is not None and (
            change.kind.value in _ABI_RELEVANT_BUILD_KINDS
        )
        verdict = policy_file.evidence_verdict(category, abi_relevant=abi_relevant)
        if verdict is not None:
            change.effective_verdict = verdict


# ── D7 require_evidence gate ─────────────────────────────────────────────────

# require_evidence layer key -> (human label, pack attribute needed on both sides
# to make that layer comparable).
_REQUIRE_EVIDENCE_LAYERS: tuple[tuple[str, str, str], ...] = (
    ("build_context", "L3 build context", "build_evidence"),
    ("source_abi", "L4 source ABI replay", "source_abi"),
    ("graph_summary", "L5 source graph summary", "source_graph"),
)


def require_evidence_findings(
    policy_file: PolicyFile | None,
    old_pack: BuildSourcePack | None,
    new_pack: BuildSourcePack | None,
) -> list[Change]:
    """Emit findings for required evidence layers unavailable to compare.

    A required-but-missing layer fails the run (``EVIDENCE_REQUIRED_MISSING`` is an
    API_BREAK kind) rather than letting a silently-degraded scan pass. Evidence
    diffs are only meaningful when both baseline (old) and target (new) sides
    supply the layer, so the requirement is enforced against comparable evidence
    on both sides. No-op when the policy declares no requirements.
    """
    if policy_file is None or not policy_file.require_evidence:
        return []
    from ..checker_policy import ChangeKind
    from ..checker_types import Change

    findings: list[Change] = []
    for key, label, attr in _REQUIRE_EVIDENCE_LAYERS:
        if not policy_file.require_evidence.get(key):
            continue
        old_present = old_pack is not None and getattr(old_pack, attr, None) is not None
        new_present = new_pack is not None and getattr(new_pack, attr, None) is not None
        if old_present and new_present:
            continue
        missing_sides = []
        if not old_present:
            missing_sides.append("baseline")
        if not new_present:
            missing_sides.append("target")
        missing = " and ".join(missing_sides)
        findings.append(
            Change(
                kind=ChangeKind.EVIDENCE_REQUIRED_MISSING,
                symbol=f"evidence:{key}",
                description=(
                    f"Policy requires comparable {label} evidence, but it is "
                    f"absent from the {missing} side of this compare. "
                    "Supply the missing evidence pack (collect + "
                    "dump --build-info/--sources) or relax evidence_policy."
                    "require_evidence in the policy file."
                ),
                old_value="required",
                new_value="not collected",
            )
        )
    return findings


# ── D9 metrics formatting ────────────────────────────────────────────────────


def _layer_status(coverage: list[LayerCoverage], layer: DataLayer) -> str:
    """Return the recorded ``CoverageStatus`` value for one optional layer."""
    for cov in coverage:
        cov_layer = cov.layer.value if hasattr(cov.layer, "value") else str(cov.layer)
        if cov_layer == layer.value:
            return cov.status.value
    return CoverageStatus.NOT_COLLECTED.value


def evidence_coverage_metrics(coverage: list[LayerCoverage]) -> dict[str, object]:
    """Build the coverage-flag part of the ADR-033 D9 metrics at diff time.

    Which optional layers ran on the target side. The per-bucket finding counts
    are computed later from the *retained* findings in ``attach_evidence_metrics``
    (so they partition the reported findings post-suppression); timing and
    run-wide totals are layered on there too.
    """
    return {
        "coverage.build_context.present": (
            _layer_status(coverage, DataLayer.L3_BUILD) == CoverageStatus.PRESENT.value
        ),
        "coverage.source_abi.mode": _layer_status(coverage, DataLayer.L4_SOURCE_ABI),
        "coverage.graph.mode": _layer_status(coverage, DataLayer.L5_SOURCE_GRAPH),
    }


def finding_bucket_counts(
    changes: list[Change], injected_changes: list[Change]
) -> dict[str, int]:
    """Partition *retained* findings into the ADR-033 D9 count buckets.

    ``evidence_required_missing`` is counted on its own (a require_evidence
    failure is neither artifact-backed nor a drift/source finding, Codex review);
    artifact-backed is everything not externally injected; build-context-drift /
    source-only come from each finding's ``evidence_category`` tag.
    """
    from ..checker_policy import ChangeKind
    injected_ids = {id(c) for c in injected_changes}
    out = {"artifact_backed": 0, "build_context_drift": 0,
           "source_only": 0, "evidence_required_missing": 0}
    for c in changes:
        if c.kind == ChangeKind.EVIDENCE_REQUIRED_MISSING:
            out["evidence_required_missing"] += 1
        elif id(c) not in injected_ids:
            out["artifact_backed"] += 1
        elif getattr(c, "evidence_category", None) == "build_context":
            out["build_context_drift"] += 1
        elif getattr(c, "evidence_category", None) == "source_only":
            out["source_only"] += 1
    return out


def echo_evidence_metrics(metrics: dict[str, object]) -> None:
    """Print the ADR-033 D6 timing / D9 metrics summary to stderr (all formats)."""
    if not metrics:
        return
    duration = metrics.get("extractor.duration_seconds")
    click.echo("Evidence metrics:", err=True)
    if duration is not None:
        click.echo(f"  collection time            {duration}s", err=True)
    click.echo(
        "  findings                   "
        f"artifact-backed={metrics.get('findings.artifact_backed.count', 0)}, "
        f"source-only={metrics.get('findings.source_only.count', 0)}, "
        f"build-context-drift={metrics.get('findings.build_context_drift.count', 0)}",
        err=True,
    )
