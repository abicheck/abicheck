# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Parse policy-aware gate and contract-coverage facts from report envelopes.

This leaf owns report gate validation. It does not load files or fold targets.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, TypeGuard

from abicheck.change_registry_types import Verdict
from abicheck.policy.outcome import (
    OperationalStatus,
    PolicyGateDecision,
    fold_gate_and_operational,
    operational_status_exit_code,
)

COVERAGE_INCOMPLETE_EXIT = 1
_VALID_GATE_EXIT = frozenset({0, 1, 2, 4})
_LEGACY_SEVERITY: dict[Verdict, int] = {
    Verdict.NO_CHANGE: 0,
    Verdict.COMPATIBLE: 0,
    Verdict.COMPATIBLE_WITH_RISK: 0,
    Verdict.API_BREAK: 2,
    Verdict.BREAKING: 4,
}


def _run_outcome_gate_and_operational(
    data: Mapping[str, Any],
) -> tuple[PolicyGateDecision, OperationalStatus] | None:
    """The report's own top-level ``run_outcome`` block (ADR-063 Phase 7),
    parsed to its ``(gate, operational)`` axes, or ``None`` when the key is
    genuinely absent (an old report predating this field, the caller's own
    named legacy-fallback case).

    A *present but unparseable* ``run_outcome`` is a different case entirely
    and fails closed (:class:`_MalformedGate`) rather than returning
    ``None`` too (Codex review) -- returning ``None`` for both made a
    corrupt, policy-blocked report indistinguishable from an old one that
    never carried this field at all, so both of this function's callers
    would silently fall through to their own legacy decode (verdict
    mapping / raw ``exit_code``) instead of failing the target unavailable,
    exactly the class of defect ``GateInfo.from_report_data``'s own
    ``severity``-block handling already guards against.

    This -- and :meth:`GateInfo.from_report_data`/`from_scan_report`, which
    call it -- is the one place a fresh report's ``RunOutcome`` axes are
    read back structured-first; legacy ``severity``/``exit_code`` decoding
    is the named fallback for a report that predates this field, never the
    only path for one that carries it (ADR-063 D6).
    """
    from abicheck.policy.outcome import RunOutcome

    if "run_outcome" not in data:
        return None
    outcome = RunOutcome.from_dict(data.get("run_outcome"))
    if outcome is None:
        raise _MalformedGate("'run_outcome' is present but not a valid RunOutcome block")
    return outcome.gate, outcome.operational


def _run_outcome_blocking_categories(
    gate: PolicyGateDecision, operational: OperationalStatus
) -> tuple[str, ...]:
    """Label(s) explaining a structured-fields-derived :class:`GateInfo`'s
    ``blocking_categories`` -- the ``PolicyGateDecision``/``OperationalStatus``
    value(s) that are actually non-``NONE``, mirroring the existing
    ``severity`` gate's own category-string convention (``"abi_breaking"``
    etc. -- ``PolicyGateDecision``'s values are spelled identically on
    purpose) without recomputing the granular per-category counts a
    ``severity`` block alone carries.
    """
    cats: list[str] = []
    if gate is not PolicyGateDecision.NONE:
        cats.append(gate.value)
    if operational is not OperationalStatus.NONE:
        cats.append(operational.value)
    return tuple(cats)


class _MalformedGate(ValueError):
    """A gate/severity block that is *present but invalid*.

    Distinct from "no gate block at all": a report that never carried a
    ``severity`` block is an old/policy-less report and may legacy-fall-back to
    the verdict mapping, but a report that *does* carry a gate block which is
    corrupt must **fail closed** — the target becomes unavailable rather than
    silently reverting to the (possibly greener) legacy verdict path.
    """


@dataclass(frozen=True)
class GateInfo:
    """One target's own CI gate decision, as it recorded it.

    ``exit_code`` is in ``compare``'s severity-aware scheme (0 pass / 1
    addition-or-quality error / 2 potential-breaking error / 4 abi-breaking
    error). ``blocking_categories`` names which severity categories are
    failing. This is read from the report's ``severity`` block when present
    (the policy-aware, authoritative value), or synthesized from the verdict
    via :data:`_LEGACY_SEVERITY` for reports produced without a policy.
    """

    exit_code: int
    blocking: bool
    blocking_categories: tuple[str, ...] = ()
    from_report: bool = True  # False when legacy-derived from the verdict

    @classmethod
    def from_report_data(cls, data: Mapping[str, Any]) -> GateInfo | None:
        """Read the ``severity`` gate block, fail-closed.

        Returns ``None`` only when the report carries **no** ``severity`` key
        (an old/policy-less report the caller may legacy-fall-back). When a
        ``severity`` block is present it is validated strictly and a
        :class:`_MalformedGate` is raised on any inconsistency — a corrupt
        policy-blocked report must never silently revert to the greener legacy
        verdict path.
        """
        run_outcome = _run_outcome_gate_and_operational(data)
        if "severity" not in data:
            if run_outcome is None:
                return None
            gate, operational = run_outcome
            outcome_exit_code = fold_gate_and_operational(gate, operational)
            return cls(
                exit_code=outcome_exit_code,
                blocking=outcome_exit_code != 0,
                blocking_categories=_run_outcome_blocking_categories(gate, operational),
                from_report=True,
            )
        sev = data.get("severity")
        if not isinstance(sev, dict):
            raise _MalformedGate("'severity' is not an object")
        exit_code = sev.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise _MalformedGate("'severity.exit_code' is missing or not an integer")
        if exit_code not in _VALID_GATE_EXIT:
            raise _MalformedGate(
                f"'severity.exit_code' {exit_code} is not one of "
                f"{sorted(_VALID_GATE_EXIT)}"
            )
        blocking = sev.get("blocking", exit_code != 0)
        if not isinstance(blocking, bool):
            raise _MalformedGate("'severity.blocking' is not a boolean")
        if blocking != (exit_code != 0):
            raise _MalformedGate(
                f"'severity.blocking'={blocking} contradicts exit_code={exit_code}"
            )
        cats = sev.get("blocking_categories", [])
        if not isinstance(cats, list) or any(not isinstance(c, str) for c in cats):
            raise _MalformedGate(
                "'severity.blocking_categories' is not a list of strings"
            )
        result = cls(
            exit_code=exit_code,
            blocking=blocking,
            blocking_categories=tuple(cats),
            from_report=True,
        )
        # ADR-063 Phase 7: fold in `RunOutcome.operational`, the one axis
        # the `severity` block above never carried at all. `gate` is not
        # re-folded here -- the `severity` block is already the precise,
        # policy-aware compatibility gate (including its granular
        # `blocking_categories`), and `RunOutcome.gate` is derived from the
        # identical computation (see `reporter._run_outcome_for_result`), so
        # the two can never disagree on a fresh report; only a real
        # operational failure can raise this result beyond what `severity`
        # alone already stated.
        if run_outcome is not None:
            _, operational = run_outcome
            op_exit = operational_status_exit_code(operational)
            if op_exit > result.exit_code:
                result = replace(
                    result,
                    exit_code=op_exit,
                    blocking=True,
                    blocking_categories=tuple(
                        sorted({*result.blocking_categories, operational.value})
                    ),
                )
        return result

    @classmethod
    def from_scan_report(cls, data: Mapping[str, Any]) -> GateInfo | None:
        """Read a ``scan`` report's gate.

        A legacy-scheme ``scan`` JSON report records its gate only as a
        top-level ``exit_code`` (scheme 0 pass / 2 source break / 4 abi break /
        5 budget overflow / 6 not-comparable) rather than a ``compare``-style
        ``severity`` block. A severity-scheme ``scan --against`` (scan schema
        1.9+) additionally publishes that block at ``diff.severity``, which is
        preferred when present -- see the body. Keyed on
        ``scan_schema_version`` so arbitrary JSON that merely happens to carry
        an ``exit_code`` is not mistaken for a scan gate. Fails closed on a
        scan report whose gate is unusable, by either route.
        """
        if "scan_schema_version" not in data:
            return None
        # A severity-scheme `scan --against` (scan schema 1.9+) publishes a
        # real `compare`-shaped gate at `diff.severity`, whose `exit_code` is
        # this run's *pre-coverage* compatibility contribution. Prefer it, and
        # validate it through the very same strict reader a `compare` report
        # goes through, so the two commands' gates are read identically rather
        # than by two validators that can disagree.
        #
        # This is not an optimization -- it is what keeps the raw-code branch
        # below sound. That branch separates the coverage contribution by
        # arguing scan "has no native 1", which stopped being true once
        # severity reached scan: an error-level addition/quality finding is a
        # native compatibility 1, and folded with a coverage 1 it produced a
        # top-level 1 that the branch below would attribute entirely to
        # coverage and pass (Codex review). A severity-scheme report answers
        # the question directly, so it never reaches that argument.
        nested = _scan_severity_gate(data)
        if nested is not None:
            return nested
        # ADR-063 Phase 7: a fresh scan report's own top-level `run_outcome`
        # (`ScanOutcome.to_dict()`/`ScanResult.to_dict()`/
        # `ScanSetResult.to_dict()`) is preferred over decoding the raw
        # legacy `exit_code` below -- structured-first, legacy decode as the
        # named fallback for a report that predates this field. This is
        # exactly what lets a `BUDGET_OVERFLOW`/`NOT_COMPARABLE` abort keep
        # blocking without this reader having to reverse-engineer which
        # raw exit code (5/6) it came from.
        run_outcome = _run_outcome_gate_and_operational(data)
        if run_outcome is not None:
            gate, operational = run_outcome
            exit_code = fold_gate_and_operational(gate, operational)
            return cls(
                exit_code=exit_code,
                blocking=exit_code != 0,
                blocking_categories=_run_outcome_blocking_categories(gate, operational),
                from_report=True,
            )
        code = data.get("exit_code")
        if not isinstance(code, int) or isinstance(code, bool):
            raise _MalformedGate("scan report 'exit_code' is missing or not an integer")
        # A scan report's `exit_code` is already a *fold* of two orthogonal
        # axes — its own compatibility gate and ADR-049 Phase 7's
        # contract-coverage contribution, which `cli_scan_baseline` folds in
        # with `max`. Reading the folded number as the compatibility gate
        # therefore double-counts a coverage-only failure: it landed the
        # target in `blocking_targets` and made its profile `affected`, so a
        # `NO_CHANGE` scan target read as a compatibility problem where the
        # equivalent `compare` report did not (Codex review, reproduced).
        #
        # Scan's own scheme is what makes this separable rather than a guess:
        # it emits 0/2/4/5/6 and has no native 1, so a *raw* 1 can only be
        # the orthogonal contribution. Discriminating on the raw code matters
        # — 5 (budget overflow) and 6 (NOT_COMPARABLE) both map to 1 below
        # and are real compatibility-gate failures that must keep blocking.
        # The report must also state the contribution itself; a bare 1 with
        # nothing to attribute it to stays blocking, fail-closed.
        if code == COVERAGE_INCOMPLETE_EXIT and _contract_coverage_exit(data) == 1:
            return cls(exit_code=0, blocking=False, from_report=True)
        # Map scan's own scheme onto the aggregate gate scheme: 0/2/4 pass
        # through; any other scan failure (e.g. 5 budget overflow, or a
        # nonsensical value) is a non-ABI failure that still blocks, folded to
        # exit 1 — fail toward blocking, never toward a fake ABI-break 4.
        mapped = code if code in (0, 2, 4) else COVERAGE_INCOMPLETE_EXIT
        return cls(exit_code=mapped, blocking=mapped != 0, from_report=True)

    @classmethod
    def legacy_from_verdict(cls, verdict: Verdict | None) -> GateInfo:
        code = _LEGACY_SEVERITY.get(verdict, 0) if verdict is not None else 0
        return cls(exit_code=code, blocking=code > 0, from_report=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "blocking": self.blocking,
            "blocking_categories": list(self.blocking_categories),
            "from_report": self.from_report,
        }


def _contract_coverage_declared(data: Mapping[str, Any]) -> bool:
    """Whether the report actually *stated* a usable coverage contribution.

    :func:`_contract_coverage_exit` fails open, so its ``0`` covers three
    different situations: the run declared ``0``, the run declared nothing,
    and the run declared something unusable. That is the right answer for
    the gate -- none of them may block -- but not for prose *about* the run:
    only the first is a ``contract.unresolved=warn`` acceptance, and saying
    so about the other two attributes a policy decision the report never
    made (CodeRabbit review).
    """
    return any(
        _is_valid_contribution(block.get("contract_coverage_exit_contribution"))
        for block in contract_coverage_blocks(data)
    )


def _scan_severity_gate(data: Mapping[str, Any]) -> GateInfo | None:
    """A severity-scheme scan report's own ``diff.severity`` gate, if it has one.

    ``None`` for a legacy-scheme scan (which runs no severity gate and so
    publishes no block), leaving :meth:`GateInfo.from_scan_report`'s raw
    top-level ``exit_code`` path to answer.

    Delegates to :meth:`GateInfo.from_report_data` rather than re-validating:
    ``cli_scan_baseline`` builds this block with the same
    ``reporter._build_severity_json`` that writes ``compare``'s, so a second
    validator here could only ever disagree with the first. That also means a
    *corrupt* scan gate block fails closed (``_MalformedGate``) exactly as a
    corrupt ``compare`` one does, instead of silently falling through to the
    greener raw-code path -- the same fail-closed principle the surrounding
    reader already applies.
    """
    # Through the shared path definition, so the reader and
    # `_neutralize_gate`'s writer cannot disagree about where the block lives
    # (the coverage axis learned this the hard way -- see
    # `contract_coverage_block_paths`).
    for path in scan_severity_gate_paths(data):
        node: Any = data
        for key in path:
            node = node[key]
        return GateInfo.from_report_data(node)
    return None


def _contract_coverage_exit(data: Mapping[str, Any]) -> int:
    """The report's own ADR-049 contract-coverage contribution (``0``/``1``).

    Read rather than recomputed: ``contract_coverage_exit_contribution``
    (report schema 2.26) is the number that actually gated the run that wrote
    it -- already reflecting the selected ``--contract`` domain and any
    ``contract.unresolved=warn`` acceptance -- and this aggregate holds none
    of the evidence needed to answer it again. A per-target run that accepted
    incomplete coverage must not have the aggregate re-impose it.

    Fails *open*, unlike the ``severity`` gate's ``_MalformedGate``: an absent
    or unusable value means "this report says nothing about a contract
    domain", which is the honest reading for every pre-2.26 report and every
    run without ``--contract``. Treating it as a failure would make
    the aggregate block on reports that never asked the question.

    A ``scan --against`` report carries the field one level down, inside its
    ``diff`` block (``cli_scan_baseline`` writes it into the summary that
    becomes ``ScanOutcome.to_dict()['diff']``), so that block is consulted for
    a scan report too. Without it the aggregate still *failed* -- the scan's
    own top-level ``exit_code`` already folds the contribution -- but reported
    ``contract_coverage.exit_contribution: 0`` and an empty
    ``incomplete_targets`` beside it, hiding which axis caused the failure
    (Codex review). That is exactly what plan Section 7 requires a report to
    make identifiable. Keyed on ``scan_schema_version``, mirroring
    :meth:`GateInfo.from_scan_report`, so arbitrary JSON that merely happens
    to carry a ``diff`` key is never mined for one. Consulted whenever the
    root value is *unusable*, not merely absent, so a malformed root key
    cannot shadow a valid nested one.
    """
    for block in contract_coverage_blocks(data):
        raw = block.get("contract_coverage_exit_contribution")
        if _is_valid_contribution(raw):
            return raw
    return 0


def scan_severity_gate_paths(data: Mapping[str, Any]) -> list[tuple[str, ...]]:
    """Key paths within a *scan* report that may carry a ``severity`` gate block.

    :func:`contract_coverage_block_paths`' sibling, for the other axis and for
    the same reason: two consumers must agree exactly on where the block
    lives. :func:`_scan_severity_gate` reads it as the target's compatibility
    gate, and ``buildsource.check_report._neutralize_gate`` must zero it for
    ``gate-mode: advisory``. Zeroing only the top-level scan ``exit_code`` left
    an explicitly advisory report's nested gate blocking the trailing
    aggregate (Codex review) -- the identical bug the coverage axis already
    had, so it gets the identical remedy rather than a second hand-written
    traversal.

    Returns the paths to the *containers* of a ``severity`` key, so a writer
    can rebind each one it touches; ``augment_report`` copies only the top
    level, so writing through a nested mapping in place would reach back into
    the caller's own report.

    Unlike the coverage paths this never includes the document root: a root
    ``severity`` block is a ``compare`` report's own gate, which
    ``_neutralize_gate`` already handles directly and which a scan never
    writes.
    """
    if "scan_schema_version" not in data:
        return []
    paths: list[tuple[str, ...]] = []
    diff = data.get("diff")
    if isinstance(diff, Mapping) and isinstance(diff.get("severity"), Mapping):
        paths.append(("diff",))
    report = data.get("report")
    if isinstance(report, Mapping):
        inner = report.get("diff")
        if isinstance(inner, Mapping) and isinstance(inner.get("severity"), Mapping):
            paths.append(("report", "diff"))
    return paths


def contract_coverage_block_paths(data: Mapping[str, Any]) -> list[tuple[str, ...]]:
    """Key paths within *data* that may carry a contract-coverage block.

    The single definition of *where these fields live*. Two consumers need
    it and they need it to agree exactly: this module reads the blocks, and
    ``buildsource.check_report._neutralize_gate`` zeroes them for
    ``gate-mode: advisory``. A hand-written copy of the traversal there
    already missed the scan-shaped nested block once (Codex review), so the
    shape is stated once and both sides derive from it.

    Paths rather than the blocks themselves, because a writer additionally
    has to *rebind* each container it touches -- ``augment_report`` copies
    only the top level, so mutating a nested block in place would reach back
    into the caller's own report (Codex review, again).
    """
    paths: list[tuple[str, ...]] = [()]
    # A `compare` report may carry an unrelated `diff` key; only a scan
    # report nests its coverage fields, so the marker gates the descent.
    if "scan_schema_version" not in data:
        return paths
    if isinstance(data.get("diff"), Mapping):
        paths.append(("diff",))
    report = data.get("report")
    if isinstance(report, Mapping) and isinstance(report.get("diff"), Mapping):
        paths.append(("report", "diff"))
    return paths


def contract_coverage_blocks(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every block of *data* that may carry a contract-coverage contribution.

    The read-side view of :func:`contract_coverage_block_paths`.
    """
    blocks: list[Mapping[str, Any]] = []
    for path in contract_coverage_block_paths(data):
        node: Any = data
        for key in path:
            node = node[key]
        blocks.append(node)
    return blocks


def _is_valid_contribution(raw: object) -> TypeGuard[int]:
    """Whether *raw* is a usable ``0``/``1`` contract-coverage contribution.

    Exactly ``0`` or ``1``, never merely "a non-negative integer": the axis is
    defined as a ``0``/``1`` floor, and letting a malformed ``2`` through would
    make this aggregate exit ``2`` -- indistinguishable from a source/API
    break -- and emit a ``contract_coverage.exit_contribution`` its own schema
    rejects (Codex review).

    The ``bool`` rejection has to come *first*, and both the root check and the
    final check must route through this one predicate rather than spelling the
    test twice. Testing a root value with a bare ``raw not in (0, 1)`` looks
    equivalent but is not: ``True == 1`` in Python, so a scan report whose root
    key is the malformed ``true`` satisfied that membership test, won against a
    perfectly valid nested ``diff`` value, and was only then rejected by the
    type check -- reporting ``0`` for a scan whose own exit code had already
    folded a ``1`` (Codex review, fresh evidence). A *string* root fell through
    correctly, which is what made the gap easy to miss.
    """
    return not isinstance(raw, bool) and isinstance(raw, int) and raw in (0, 1)


def _contract_coverage_incomplete(data: Mapping[str, Any]) -> bool:
    """Whether the report listed any contract-coverage failure at all.

    Tracked *separately* from :func:`_contract_coverage_exit` because the two
    genuinely differ: ``contract.unresolved=warn`` zeroes the exit floor and
    changes nothing else, so an accepting target's report still carries a
    populated ``contract_coverage_failures`` ledger beside a contribution of
    ``0`` (ADR-049 Section 6.2 -- accepting incomplete assurance is not
    hiding it). Deriving incompleteness from the contribution alone therefore
    reported ``incomplete_targets: []`` and printed no diagnostic for a
    matrix in which a target's contract domain never closed, which is the
    hiding that mode is explicitly not supposed to do (Codex review, fresh
    evidence).

    Searches the same blocks as the contribution, through the same shared
    :func:`contract_coverage_blocks` -- the two answer different questions
    off the same keys in the same places, so a nesting one knows and the other
    does not is a guaranteed divergence. A non-list, or an empty list, is
    "nothing to report" -- the same fail-open reading as the contribution, and
    correct for every pre-2.26 report and every run without
    ``--contract``.
    """
    for block in contract_coverage_blocks(data):
        failures = block.get("contract_coverage_failures")
        if isinstance(failures, list):
            return bool(failures)
    return False
