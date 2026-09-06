# SPDX-License-Identifier: Apache-2.0
"""CLI-invocation and finding-set helpers shared by the parity test modules.

Diffs happen on structured *finding sets* — kind, resolved identity,
severity, evidence refs — never on rendered report text (Phase 0's own
requirement, plan §6). ``compare``/``scan`` are invoked through their real
public CLI entry point (Click's in-process ``CliRunner``, no subprocess);
``run_crosschecks``/``scan_files``/``audit_stable_abi_imports`` are invoked
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

from .gaps import ALL_EXPECTED_GAPS


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

    Calls :func:`abicheck.buildsource.crosscheck.run_crosschecks` directly —
    the same production function ``scan_engine.py`` is the sole caller of.
    """
    from abicheck.buildsource.crosscheck import run_crosschecks

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
    """The outcome of comparing a scan-side and a compare-side kind set."""

    #: Present under scan, absent under compare, and *not* a recorded gap —
    #: an unexplained capability loss. Must always be empty.
    unexplained_losses: frozenset[str]
    #: Present under scan, absent under compare, matching a recorded gap —
    #: the expected red state.
    expected_losses: frozenset[str]
    #: Registered as an expected gap, but scan and compare now agree on it
    #: (compare produces it too) — the gap is closed and must be deleted
    #: from tests/parity/gaps.py in the same PR that closed it.
    healed_gaps: frozenset[str]
    #: Present under compare but not under scan — richer, always allowed.
    compare_only: frozenset[str]


def diff_kind_sets(
    *, scan_kinds: frozenset[str], compare_kinds: frozenset[str]
) -> ParityReport:
    """Classify every kind difference between a scan-side and compare-side set."""
    lost = scan_kinds - compare_kinds
    expected = frozenset(ALL_EXPECTED_GAPS)
    return ParityReport(
        unexplained_losses=lost - expected,
        expected_losses=lost & expected,
        healed_gaps=expected & scan_kinds & compare_kinds,
        compare_only=compare_kinds - scan_kinds,
    )


def assert_no_capability_loss(
    *, scan_kinds: frozenset[str], compare_kinds: frozenset[str], context: str
) -> ParityReport:
    """Fail loudly, naming the check, for any loss `gaps.py` doesn't explain.

    Also fails when a *registered* gap turns out to no longer be a gap
    (``compare`` now produces the kind too) — the harness must not keep
    asserting a red state the migration already turned green, or the
    registry silently stops being "the migration's definition of done".
    """
    report = diff_kind_sets(scan_kinds=scan_kinds, compare_kinds=compare_kinds)
    if report.unexplained_losses:
        names = ", ".join(sorted(report.unexplained_losses))
        raise AssertionError(
            f"{context}: capability loss -- present under `scan`, absent under "
            f"`compare`, and NOT a recorded parity gap: {names}. Either this is "
            "a real regression (fix it), or it belongs in "
            "tests/parity/gaps.py's EXPECTED_GAPS with the plan phase that "
            "will close it."
        )
    if report.healed_gaps:
        entries = ", ".join(
            f"{k} ({ALL_EXPECTED_GAPS[k].plan_phase})"
            for k in sorted(report.healed_gaps)
        )
        raise AssertionError(
            f"{context}: parity gap closed but tests/parity/gaps.py still "
            f"lists it as scan-only: {entries}. Delete the EXPECTED_GAPS "
            "entry -- once `compare` produces the finding, the registry "
            "must shrink to match (that shrinking IS the migration's "
            "definition of done, plan §6 Phase 0/3)."
        )
    return report
