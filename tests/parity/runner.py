# SPDX-License-Identifier: Apache-2.0
"""CLI-invocation and finding-set helpers shared by the parity test modules.

Diffs happen on structured *finding sets* — kind, resolved identity,
severity, evidence refs — never on rendered report text (Phase 0's own
requirement, plan §6). ``compare`` is invoked through its real public CLI
entry point (Click's in-process ``CliRunner``, no subprocess);
``run_crosschecks``/``find_pattern_facts``/``audit_stable_abi_imports`` are
invoked directly where a test wants the underlying production primitive
itself rather than a full CLI round-trip.

``tests/parity/`` used to be a live scan-vs-compare comparison harness --
proving each engine primitive ADR-068 §1 named as scan-only had migrated
onto ``compare``'s own pipeline, by running the identical input through
both real CLIs and diffing the results. ``scan`` was deleted outright with
ADR-068 Phase 6 (it duplicated `compare`); this module (and the surviving
test modules under this package) is now a **compare-only regression
corpus** instead -- the finding sets/verdicts/exit codes/identities that
used to be *proven* equal to a live ``scan`` invocation are now pinned
directly as ``compare``'s own expected behavior. The scan-invoking half of
this harness (``scan_json``/``scan_finding_set``/``RunOutcome``/
``scan_outcome``/``compare_outcome``/``assert_full_parity``) was deleted
along with the tests that were its only callers; ``FindingSet``/
``ParityReport``/``diff_findings``/``assert_no_capability_loss`` survive
because ``test_cross_source_checks_parity.py`` still uses them to diff a
direct ``run_crosschecks()`` call against ``compare``'s own automatic
cross-source-checks stage -- a real, still-current two-implementation
comparison, not a scan/compare one.
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
    """One finding, projected to the dimensions parity is judged on.

    ``finding_id`` (Codex review, PR #1172, round 16, fresh evidence)
    disambiguates two same-``kind``/same-``identity`` findings on the same
    symbol -- a cross-source check like ``private_header_leak`` can
    legitimately emit several findings for one function, one per leaked
    type, and without this field they collapsed onto the same
    ``Finding`` value: a partial capability loss (one of the two rows
    missing on one side) still hashed identically to the other side's set
    and silently read as full parity. ``report_finding_id``
    (``finding_identity.py``) is the shared per-finding fingerprint both
    ``compare``'s and `scan`'s own report dicts already carry (folding in
    ``old_value``/``new_value``/``source_location``/``description``, the
    exact per-instance detail that distinguishes two same-kind occurrences)
    -- reusing it here means no second identity scheme to keep in sync with
    the real one every report consumer already relies on.
    """

    kind: str
    identity: str
    severity: str
    evidence_refs: tuple[str, ...] = ()
    finding_id: str = ""


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
    from abicheck.finding_identity import report_finding_id

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
                finding_id=report_finding_id(c),
            )
        )
    return frozenset(findings)


def compare_json(old: Path | str, new: Path | str, *extra_args: str) -> dict[str, Any]:
    """Invoke ``compare OLD NEW --format json`` and return the parsed report."""
    result = invoke_cli("compare", str(old), str(new), "-o", "json=-", *extra_args)
    # 5/7 (ADR-068 §3 #19/#28, Phase 4 commit 2): `--budget` overflow and the
    # `--depth build`/`--depth source` evidence-contract floor are both
    # legitimate, reported axes now -- a real result still renders (this
    # helper reads its JSON below), not a crash to fail this helper's own
    # sanity check over.
    if result.exit_code not in (0, 1, 2, 4, 5, 6, 7):
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
                finding_id=c.get("finding_id") or "",
            )
        )
    return frozenset(findings)


def kinds_of(finding_set: FindingSet) -> frozenset[str]:
    return frozenset(f.kind for f in finding_set)


@dataclass(frozen=True)
class RunOutcome:
    """The whole-invocation outcome of one real CLI run -- not just its
    finding set.

    A finding-set diff alone cannot see a run's overall verdict, exit code,
    or per-finding gate contribution diverge from another run's, or from
    what a test expects, even when the two finding *sets* agree -- because
    presence and contribution-to-the-gate are different questions. This
    used to matter for a two-tool (``scan``/``compare``) comparison (see
    this module's own docstring); ``_outcome_from_findings`` below has no
    remaining CLI-invoking production caller after ``scan``'s deletion
    (ADR-068 Phase 6), but stays as a tested, reusable primitive per this
    repo's "Primitive-level property tests" convention --
    ``test_runner_finding_identity.py`` exercises its finding-multiplicity
    contract directly.
    """

    verdict: str | None
    exit_code: int
    findings: FindingSet
    #: (kind, identity, finding_id) -> the finding's *actually applied*
    #: severity label for this run (``changes[]``'s own ``severity`` field)
    #: -- unlike ``Finding.severity`` above (a static per-kind registry
    #: default), this can and does vary by run context. ``finding_id``
    #: (Codex review, PR #1172, round 16, fresh evidence) is part of the
    #: key, not just ``Finding``'s own dedup fields, for the identical
    #: reason: a check like ``private_header_leak`` can emit several
    #: same-``(kind, identity)`` findings for one function (one per leaked
    #: type), and without it the second finding's severity/gate entry would
    #: silently overwrite the first's in this dict instead of the two
    #: staying distinguishable.
    severities: dict[tuple[str, str, str], str]
    #: (kind, identity, finding_id) -> ADR-049 D1 ``gate_contribution`` for
    #: this run, or ``None`` when the raw finding dict never carries the
    #: key at all (`compare`'s `changes[]` entries always carry it --
    #: `reporter.py`'s `_change_to_dict` stamps it unconditionally, `0`
    #: included -- a real, computed "no contribution"). `None` here keeps
    #: "genuinely absent" distinguishable from "computed as 0" for any
    #: caller that needs the distinction.
    gate_contributions: dict[tuple[str, str, str], int | None]


def _outcome_from_findings(
    result: Any,
    verdict: object,
    raw_findings: list[dict[str, Any]],
    *,
    evidence_key: str | None,
) -> RunOutcome:
    findings = set()
    severities: dict[tuple[str, str, str], str] = {}
    gate: dict[tuple[str, str, str], int | None] = {}
    for c in raw_findings:
        kind = c["kind"]
        identity = c.get("symbol") or ""
        finding_id = c.get("finding_id") or ""
        key = (kind, identity, finding_id)
        findings.add(
            Finding(
                kind=kind,
                identity=identity,
                severity=severity_for_kind(kind),
                evidence_refs=(
                    tuple(c.get(evidence_key) or ()) if evidence_key else ()
                ),
                finding_id=finding_id,
            )
        )
        severities[key] = c.get("severity", c.get("bucket", "unknown"))
        # `None` (not `0`) when the key is genuinely absent -- see
        # RunOutcome.gate_contributions' own docstring for why the two must
        # not be conflated.
        gate[key] = c.get("gate_contribution")
    return RunOutcome(
        verdict=verdict if verdict is None else str(verdict),
        exit_code=result.exit_code,
        findings=frozenset(findings),
        severities=severities,
        gate_contributions=gate,
    )


#: ADR-068 D4/Phase 5: --surface-metrics computation is unconditional on
#: `compare` now (ADR-027 A1/D1.2's aggregate roll-ups) -- these are
#: expected, permanent compare-only "richer" noise for any scenario whose
#: public-surface count changes. Excluded from the source-depth (F-3/F-4)
#: exact-identity checks the same way `assert_no_capability_loss` already
#: treats every compare-only finding.
SURFACE_METRIC_KINDS = frozenset(
    {
        ChangeKind.PUBLIC_SURFACE_GREW.value,
        ChangeKind.PUBLIC_SURFACE_SHRANK.value,
        ChangeKind.UNDOCUMENTED_EXPORT_RATIO_INCREASED.value,
    }
)


def without_surface_metrics(finding_set: FindingSet) -> FindingSet:
    return frozenset(f for f in finding_set if f.kind not in SURFACE_METRIC_KINDS)


def write_snapshot(snapshot: Any, path: Path) -> Path:
    """Serialize *snapshot* to *path* for a CLI invocation to consume."""
    from abicheck.serialization import snapshot_to_json

    path.write_text(snapshot_to_json(snapshot), encoding="utf-8")
    return path


@dataclass(frozen=True)
class ParityReport:
    """The outcome of comparing two finding sets (e.g. a direct
    ``run_crosschecks()`` call against ``compare``'s own automatic
    cross-source-checks stage) -- the ``scan``/``compare`` naming below
    predates ``scan``'s deletion (ADR-068 Phase 6) but the shape is
    generic: "left-hand finding set" vs. "right-hand finding set".

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
