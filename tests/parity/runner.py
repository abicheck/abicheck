# SPDX-License-Identifier: Apache-2.0
"""CLI-invocation and finding-set helpers shared by the parity test modules.

Diffs happen on structured *finding sets* — kind, resolved identity,
severity, evidence refs — never on rendered report text (Phase 0's own
requirement, plan §6). ``compare``/``scan`` are invoked through their real
public CLI entry point (Click's in-process ``CliRunner``, no subprocess);
``run_crosschecks``/``find_pattern_facts``/``audit_stable_abi_imports`` are invoked
directly for the ``scan``-only side because they are themselves the
production functions ADR-068 §1 named as scan-only by call site — calling
them here *is* exercising "what scan has", not a reimplementation of it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from abicheck.change_registry import REGISTRY
from abicheck.checker_policy import ChangeKind
from abicheck.cli import main as abicheck_main

from .gaps import EXPECTED_GAPS


@dataclass(frozen=True)
class Finding:
    """One finding, projected to the dimensions parity is judged on."""

    kind: str
    identity: str
    severity: str
    evidence_refs: tuple[str, ...] = ()


FindingSet = frozenset[Finding]


def invoke_cli(*args: str) -> Any:
    """Invoke the real ``abicheck`` CLI in-process (the public entry point)."""
    return CliRunner().invoke(abicheck_main, list(args))


def severity_for_kind(kind_value: str) -> str:
    """The kind's registered default verdict — independent of run context.

    Both ``run_crosschecks``' ``Change`` objects and a rendered ``compare``
    JSON report's ``changes[]`` entries carry the same ``ChangeKind``
    vocabulary, so this is a single, side-independent severity axis: it
    doesn't depend on which tool computed the finding, only on what kind it
    is (``abicheck/change_registry.py``'s ``ChangeKindMeta.default_verdict``).
    """
    try:
        meta = REGISTRY.get(ChangeKind(kind_value))
    except ValueError:
        return "UNKNOWN"
    return meta.default_verdict.value if meta else "UNKNOWN"


def crosscheck_finding_set(snapshot: Any, config: Any = None) -> FindingSet:
    """The finding set ``scan``'s cross-source checks produce for *snapshot*.

    Calls :func:`abicheck.buildsource.cross_source_checks.run_crosschecks` directly —
    the same production function ``scan_engine.py`` is the sole caller of.
    """
    from abicheck.buildsource.cross_source_checks import run_crosschecks

    result = run_crosschecks(snapshot, config)
    findings = set()
    for c in result.findings:
        kind = c.kind.value
        findings.add(
            Finding(
                kind=kind,
                identity=c.symbol or "",
                severity=severity_for_kind(kind),
                evidence_refs=tuple(result.providers.get(kind, ())),
            )
        )
    return frozenset(findings)


def compare_json(old: Path | str, new: Path | str, *extra_args: str) -> dict[str, Any]:
    """Invoke ``compare OLD NEW --format json`` and return the parsed report."""
    result = invoke_cli("compare", str(old), str(new), "--format", "json", *extra_args)
    if result.exit_code not in (0, 1, 2, 4, 6):
        raise AssertionError(
            f"compare failed unexpectedly (exit={result.exit_code}):\n{result.output}"
        )
    # `--format json` still writes diagnostics (e.g. a public-surface-scoping
    # warning) to stderr; only stdout is the machine-readable report.
    return json.loads(result.stdout)


def compare_finding_set(
    old: Path | str, new: Path | str, *extra_args: str
) -> FindingSet:
    """The finding set the real ``compare`` CLI produces for OLD vs NEW."""
    data = compare_json(old, new, *extra_args)
    findings = set()
    for c in data.get("changes", []):
        kind = c["kind"]
        findings.add(
            Finding(
                kind=kind,
                identity=c.get("symbol") or "",
                severity=severity_for_kind(kind),
                evidence_refs=tuple(c.get("contract_evidence_refs") or ()),
            )
        )
    return frozenset(findings)


def scan_json(artifact: Path | str, *extra_args: str) -> dict[str, Any]:
    """Invoke ``scan ARTIFACT --format json`` and return the parsed report."""
    result = invoke_cli("scan", str(artifact), "--format", "json", *extra_args)
    if result.exit_code not in (0, 1, 2, 4, 5, 6, 7):
        raise AssertionError(
            f"scan failed unexpectedly (exit={result.exit_code}):\n{result.output}"
        )
    return json.loads(result.stdout)


def scan_finding_set(artifact: Path | str, *extra_args: str) -> FindingSet:
    """The finding set ``scan --against`` produces for ARTIFACT.

    Only populated when the run actually reaches a baseline comparison
    (``scan``'s JSON carries the real per-finding list at ``diff.findings``
    then, keyed the same way as ``compare``'s ``changes[]`` -- ``kind``/
    ``symbol``; without ``--against`` there is nothing to diff and this is
    empty by construction).
    """
    data = scan_json(artifact, *extra_args)
    diff = data.get("diff") or {}
    findings = set()
    for c in diff.get("findings", []):
        kind = c["kind"]
        findings.add(
            Finding(
                kind=kind,
                identity=c.get("symbol") or "",
                severity=severity_for_kind(kind),
                evidence_refs=(),
            )
        )
    return frozenset(findings)


def kinds_of(finding_set: FindingSet) -> frozenset[str]:
    return frozenset(f.kind for f in finding_set)


def write_snapshot(snapshot: Any, path: Path) -> Path:
    """Serialize *snapshot* to *path* for a CLI invocation to consume."""
    from abicheck.serialization import snapshot_to_json

    path.write_text(snapshot_to_json(snapshot), encoding="utf-8")
    return path


@dataclass(frozen=True)
class ParityReport:
    """The outcome of comparing a scan-side and a compare-side finding set.

    Diffs happen over whole :class:`Finding` values (kind + identity +
    severity + evidence), not just kinds — two findings of the same kind
    but different resolved identity (e.g. ``func_removed`` for symbols A
    and B) are distinguishable, so a partial gap closure (compare now
    reaches one of the two, not both) stays visible instead of reading as
    full parity the moment either instance shows up on both sides.
    """

    #: Present under scan, absent under compare, and *not* a recorded gap —
    #: an unexplained capability loss. Must always be empty.
    unexplained_losses: frozenset[Finding]
    #: Present under scan, absent under compare, matching a recorded gap —
    #: the expected red state (may be a subset of that kind's scan findings
    #: when the gap is only partially closed).
    expected_losses: frozenset[Finding]
    #: A gap kind is only "healed" once EVERY scan finding of that kind is
    #: also produced by compare — not merely once any one instance is.
    healed_gaps: frozenset[str]
    #: Present under compare but not under scan — richer, always allowed.
    compare_only: frozenset[Finding]


def diff_findings(
    *, scan_findings: FindingSet, compare_findings: FindingSet
) -> ParityReport:
    """Classify every finding difference between a scan-side and compare-side set.

    Gap membership (``EXPECTED_GAPS``, never ``ALL_EXPECTED_GAPS`` --
    ``finding_evolution`` there is a "not implemented anywhere" tracking
    entry, not a scan-vs-compare capability comparison, and no real
    ``Finding.kind`` is ever spelled that way) is checked per finding by
    its own ``kind``, but "healed" is computed over the whole kind: it only
    fires once every scan finding of that kind also appears on the compare
    side, so closing the gap for one identity while another of the same
    kind is still lost does not falsely read as a completed migration.
    """
    lost = scan_findings - compare_findings
    expected_kinds = frozenset(EXPECTED_GAPS)
    lost_kinds = kinds_of(lost)
    scan_kinds = kinds_of(scan_findings)
    return ParityReport(
        unexplained_losses=frozenset(f for f in lost if f.kind not in expected_kinds),
        expected_losses=frozenset(f for f in lost if f.kind in expected_kinds),
        healed_gaps=frozenset(
            k for k in expected_kinds if k in scan_kinds and k not in lost_kinds
        ),
        compare_only=compare_findings - scan_findings,
    )


def assert_no_capability_loss(
    *, scan_findings: FindingSet, compare_findings: FindingSet, context: str
) -> ParityReport:
    """Fail loudly, naming the check, for any loss `gaps.py` doesn't explain.

    Also fails when a *registered* gap turns out to no longer be a gap
    (``compare`` now produces every one of that kind's findings too) — the
    harness must not keep asserting a red state the migration already
    turned green, or the registry silently stops being "the migration's
    definition of done".
    """
    report = diff_findings(
        scan_findings=scan_findings, compare_findings=compare_findings
    )
    if report.unexplained_losses:
        names = ", ".join(
            f"{f.kind}({f.identity!r})"
            for f in sorted(
                report.unexplained_losses, key=lambda f: (f.kind, f.identity)
            )
        )
        raise AssertionError(
            f"{context}: capability loss -- present under `scan`, absent under "
            f"`compare`, and NOT a recorded parity gap: {names}. Either this is "
            "a real regression (fix it), or it belongs in "
            "tests/parity/gaps.py's EXPECTED_GAPS with the plan phase that "
            "will close it."
        )
    if report.healed_gaps:
        entries = ", ".join(
            f"{k} ({EXPECTED_GAPS[k].plan_phase})" for k in sorted(report.healed_gaps)
        )
        raise AssertionError(
            f"{context}: parity gap closed but tests/parity/gaps.py still "
            f"lists it as scan-only: {entries}. Delete the EXPECTED_GAPS "
            "entry -- once `compare` produces the finding, the registry "
            "must shrink to match (that shrinking IS the migration's "
            "definition of done, plan §6 Phase 0/3)."
        )
    return report
