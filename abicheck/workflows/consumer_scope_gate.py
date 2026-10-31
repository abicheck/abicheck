# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""ADR-043 scoped gate: ``--used-by`` / ``consumers.required_symbols`` computation.

Owns the worst-wins scoping passes and the choice of which pass applies
(:func:`apply_scoped_gating`); the scoped exit code each pass reports is
decided by ``policy.exit_decision_precedence.resolve_scoped_exit_code``,
the one owner of every ``compare`` exit decision. Input refusals are
typed :class:`ScopedGateError` subclasses; the CLI adapter renders them as
click errors, it does not decide them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..compare.finding_identity import report_finding_id
from ..model.consumer_requirements import LibraryExportsUnreadableError
from ..model.consumer_spec import ConsumerUnreadableError
from ..policy.exit_decision_precedence import resolve_scoped_exit_code
from .gate import consumer_verdict_rank, effective_kind_sets
from .input_resolution import detect_binary_format

if TYPE_CHECKING:
    from ..model.change_catalog.registry import Verdict
    from ..model.consumer_spec import ConsumerAppInput
    from .consumer_scope import AppCompatResult, PluginHostContractResult
    from .policy_file import PolicyFile


class ScopedGateError(Exception):
    """An input the scoped gate refuses. The CLI adapter maps each subclass to
    its own click error and exit code; this layer carries the facts only."""


class UsedByEvidenceError(ScopedGateError):
    """OLD or NEW carries no binary evidence to scope a ``--used-by`` consumer
    against: not a real library path, nor a JSON snapshot with an
    ``elf``/``pe``/``macho`` block (i.e. a headers-only dump)."""

    def __init__(self, label: str, path: Path) -> None:
        super().__init__(f"{label} ({path}) carries no binary evidence")
        self.label = label
        self.path = path


class UsedByConsumerError(ScopedGateError):
    """A REQUIRED ``--used-by`` consumer could not be read (an ADVISORY one never
    raises: it comes back ``unreadable=True``)."""

    def __init__(self, cause: ConsumerUnreadableError) -> None:
        super().__init__(str(cause))
        self.cause = cause


class UsedByLibraryError(ScopedGateError):
    """An OLD/NEW library whose export table was not read: no consumer can be
    scoped against it, so the gate refuses rather than report an unchecked verdict."""

    def __init__(self, cause: LibraryExportsUnreadableError) -> None:
        super().__init__(str(cause))
        self.cause = cause


def resolve_scoping_libs(
    old_input: Path,
    new_input: Path,
    old_snapshot: Any,
    new_snapshot: Any,
) -> tuple[Any, Any]:
    """Pick the OLD/NEW object each scoping pass reads binary facts from.

    A recognized binary is read from its path; anything else falls back to the
    already-loaded snapshot.
    """
    old_lib = old_input if detect_binary_format(old_input) is not None else old_snapshot
    new_lib = new_input if detect_binary_format(new_input) is not None else new_snapshot
    return old_lib, new_lib


def require_used_by_binary_evidence(
    old_lib: Any,
    new_lib: Any,
    old_input: Path,
    new_input: Path,
) -> None:
    """Reject a ``--used-by`` run whose OLD/NEW side carries no binary evidence.

    A real library path always qualifies; a JSON snapshot qualifies only when it
    carries an ``elf``/``pe``/``macho`` block (i.e. it is a ``dump`` of a real
    library, not a headers-only one) -- that is what supplies the SONAME/export
    table/version list/PE ordinal table the scoping needs. OLD is checked first.

    Raises :class:`UsedByEvidenceError` for the first side that fails.
    """
    for lib, path, label in (
        (old_lib, old_input, "OLD"),
        (new_lib, new_input, "NEW"),
    ):
        has_binary_evidence = isinstance(lib, Path) or any(
            getattr(lib, field, None) is not None for field in ("elf", "pe", "macho")
        )
        if not has_binary_evidence:
            raise UsedByEvidenceError(label, path)


#: Fields a scope produces only sometimes. A ``None`` here means "this scope
#: did not produce it" and the attribute is not written at all. Every other
#: field is always written, even when its value is ``None`` (``scoped_verdict``
#: is ``None`` when no consumer was supplied to ``--used-by``).
_CONDITIONAL_SCOPED_FIELDS = frozenset(
    {
        "used_by",
        "consumer_impact_summary",
        "required_symbols",
        "scoped_blocking_categories",
        "scoped_severity_counts",
    }
)


@dataclass(frozen=True)
class ScopedGateFields:
    """The scoped-gate attributes a worst-wins pass stamps onto a compare result.

    ADR-043's ``--used-by`` / ``consumers.required_symbols`` passes write these onto
    the ``DiffResult`` they scope. Readers -- ``policy.effective_gate``, the
    renderers and the SARIF/JUnit writers -- still look them up by attribute
    name, so :meth:`stamp` is the only place the set is written and the
    attribute names live here, once, rather than at every assignment.
    """

    gate_scope: str
    scoped_verdict: Verdict | None
    scoped_exit_code: int
    scoped_exit_code_scheme: str
    scoped_relevant_finding_ids: frozenset[str]
    scoped_missing_labels: tuple[str, ...]
    scoped_only_changes: tuple[Any, ...]
    #: ``--used-by`` only: one JSON-safe summary per consumer.
    used_by: list[dict[str, Any]] | None = None
    #: ``--used-by`` only: "N of M consumers affected".
    consumer_impact_summary: dict[str, Any] | None = None
    #: ``consumers.required_symbols`` only: the plugin-host contract summary.
    required_symbols: dict[str, Any] | None = None
    #: Severity-scheme runs only.
    scoped_blocking_categories: tuple[str, ...] | None = None
    scoped_severity_counts: dict[str, int] | None = None

    def stamp(self, result: object) -> None:
        """Write this scope's attributes onto *result* (see the class docstring)."""
        for field_info in fields(self):
            value = getattr(self, field_info.name)
            if value is None and field_info.name in _CONDITIONAL_SCOPED_FIELDS:
                continue
            setattr(result, field_info.name, value)


def _app_compat_summary(result: AppCompatResult) -> dict[str, Any]:
    """Project an :class:`appcompat.AppCompatResult` into a small JSON-safe dict.

    Workstream D-S1: ``platform``/``profile``/``provider_baseline``/
    ``digest``/``requirement``/``unreadable`` are only present when the
    supplied consumer actually carried that provenance (a
    :class:`~abicheck.model.consumer_spec.ConsumerSpec`, e.g. via
    ``consumers.manifests``) -- a bare ``--used-by <path>`` consumer's summary
    is byte-for-byte the same shape it has always been.
    """
    summary: dict[str, Any] = {
        "app": result.app_path,
        "verdict": result.verdict.value,
        "required_symbol_count": result.required_symbol_count,
        "missing_symbols": result.missing_symbols,
        "missing_versions": result.missing_versions,
        "relevant_change_count": len(result.breaking_for_app),
        "symbol_coverage": round(result.symbol_coverage, 1),
    }
    for field in ("platform", "profile", "provider_baseline", "digest"):
        value = getattr(result, field, None)
        if value is not None:
            summary[field] = value
    requirement = getattr(result, "requirement", "required")
    if requirement != "required":
        summary["requirement"] = requirement
    if getattr(result, "unreadable", False):
        summary["unreadable"] = True
        summary["unreadable_reason"] = getattr(result, "unreadable_reason", None)
    return summary


def _is_consumer_affected(summary: dict[str, Any]) -> bool:
    """Whether one ``_app_compat_summary`` entry counts as "affected" in the
    "N of M consumers affected" statistic (Workstream D-S1).

    An unreadable (necessarily advisory -- a REQUIRED one would have raised
    before a summary was ever built) consumer is excluded from both N and M's
    "evaluated" denominator: there is no verdict to call affected or not.
    """
    return summary.get("verdict") not in ("COMPATIBLE", "NO_CHANGE")


def _consumer_impact_summary(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """ "N of M consumers affected" statistics across every supplied
    ``--used-by`` consumer (Workstream D-S1's "Missing" item)."""
    from ..model.consumer_spec import ConsumerImpactSummary

    unreadable = [s for s in summaries if s.get("unreadable")]
    evaluated = [s for s in summaries if not s.get("unreadable")]
    affected = [s for s in evaluated if _is_consumer_affected(s)]
    return ConsumerImpactSummary(
        total=len(summaries),
        evaluated=len(evaluated),
        affected=len(affected),
        unreadable_advisory=len(unreadable),
        unreadable_paths=tuple(s["app"] for s in unreadable),
    ).to_json()


def _plugin_contract_summary(result: PluginHostContractResult) -> dict[str, Any]:
    """Project a :class:`appcompat.PluginHostContractResult` into a small dict."""
    return {
        "verdict": result.verdict.value,
        "required_entrypoints": sorted(result.required_entrypoints),
        "missing_entrypoints": result.missing_entrypoints,
        "relevant_change_count": len(result.breaking_for_host),
        "coverage": round(result.coverage, 1),
    }


def _scoped_severity_summary(
    relevant_changes: list[Any],
    missing: Iterable[str],
    result: Any,
    sev_config: Any,
    policy: str,
    policy_file: PolicyFile | None,
) -> tuple[tuple[str, ...], dict[str, int]]:
    """(blocking_categories, per-category counts) for one scoped result.

    Mirrors ``resolve_scoped_exit_code``'s missing-contract floor: a missing
    symbol/version/entrypoint with no matching diff Change is folded into
    ``abi_breaking`` directly here -- both into the blocking-categories set
    (when abi_breaking is severity-configured as error, matching the exit
    -code floor) and into the count (always, since a count is a factual
    tally, not a gate decision) -- otherwise a missing-contract-only scoped
    BREAKING would report an empty ``blocking_categories`` alongside a
    nonzero exit code, or a ``categories.abi_breaking.count`` of 0 alongside
    a blocking ``abi_breaking`` category (Codex review). A *missing* entry
    that already has a matching Change in *relevant_changes* (e.g. a removed
    symbol is both "missing" from the new export table and a ``FUNC_REMOVED``
    Change) is excluded via ``uncovered_missing_symbols`` -- otherwise that
    single ABI break would be counted twice (Codex review follow-up).
    """
    from .consumer_scope import uncovered_missing_symbols
    from .gate import (
        IssueCategory,
        SeverityLevel,
        categorize_changes,
        compute_gate_decision,
    )

    categorized = categorize_changes(
        relevant_changes,
        policy=policy,
        kind_sets=effective_kind_sets(result),
        policy_file=policy_file,
    )
    counts = {
        "abi_breaking": len(categorized.abi_breaking),
        "potential_breaking": len(categorized.potential_breaking),
        "quality_issues": len(categorized.quality_issues),
        "addition": len(categorized.addition),
        "informational": len(categorized.informational),
    }
    gate = compute_gate_decision(
        relevant_changes,
        sev_config,
        policy=policy,
        kind_sets=effective_kind_sets(result),
        policy_file=policy_file,
    )
    categories = list(gate.blocking_categories)
    uncovered = uncovered_missing_symbols(missing, relevant_changes)
    if uncovered:
        counts["abi_breaking"] += len(uncovered)
        if (
            sev_config.abi_breaking == SeverityLevel.ERROR
            and IssueCategory.ABI_BREAKING.value not in categories
        ):
            categories.append(IssueCategory.ABI_BREAKING.value)
    return tuple(categories), counts


def _apply_used_by_scoping(
    result: Any,
    used_by_apps: tuple[ConsumerAppInput, ...],
    old_input: Path,
    new_input: Path,
    old_snapshot: Any,
    new_snapshot: Any,
    policy: str,
    policy_file: PolicyFile | None,
    severity: Any = None,
    suppression: Any = None,
) -> int:
    """Scope *result* to each ``--used-by`` app; worst-wins (ADR-043).

    OLD/NEW may be real library binaries or JSON snapshots (e.g. a saved
    ``dump`` output): a recognized binary is parsed directly; otherwise the
    already-loaded snapshot (``old_snapshot``/``new_snapshot``, from
    ``compare``'s own pipeline) is used instead, since a snapshot's
    ``elf``/``pe``/``macho`` fields already carry the SONAME/export table/
    version list/PE ordinal table :func:`~abicheck.workflows.consumer_scope.scope_diff_to_app`
    needs. Attaches a JSON-safe summary to ``result.used_by`` for the
    renderer and returns the worst app's exit code, computed under
    the run's scheme (legacy verdict floor when *severity* is ``None``, or
    severity-aware over each app's relevant changes otherwise).

    *suppression* (ADR-044 P2, Codex review) is forwarded to
    :func:`~abicheck.workflows.consumer_scope.scope_diff_to_app`: its findings are
    synthesized *after* the pipeline's own suppression pass already ran over
    ``result.changes``, so without this they would be unsuppressible even by
    an exact rule.
    """
    from .consumer_scope import scope_diff_to_app
    from .disposition import close_consumer_scope, ledger_for

    old_lib, new_lib = resolve_scoping_libs(
        old_input, new_input, old_snapshot, new_snapshot
    )

    from .consumer_scope import uncovered_missing_symbols

    summaries = []
    worst_exit = 0
    worst_verdict: Verdict | None = None
    worst_verdict_rank = -1
    # Keyed by the change's semantic identity (kind/symbol/old/new/location/
    # description, via `_finding_id`) -- not id(change) -- so a Change or
    # missing symbol shared by two tied apps (e.g. both import the same
    # removed symbol) collapses to one entry instead of being tallied once
    # per app (Codex review) -- `_scoped_severity_summary` runs once at the
    # end over this deduplicated union, not per app summed together.
    # `id()` alone under-deduplicates PE_ORDINAL_RETARGETED findings:
    # `scope_diff_to_app` synthesizes a fresh `Change` object per app (via
    # `_check_pe_ordinal_imports`), so two apps hitting the same ordinal
    # retarget produce structurally-identical but object-distinct `Change`s
    # that `id()` would double-count in the severity summary.
    worst_changes: dict[str, Any] = {}
    worst_missing: set[str] = set()
    # Union across ALL apps (not just the worst-exit-code one) of which
    # findings this --used-by gate actually cares about -- SARIF/JUnit
    # consult this to make their own result levels/failure counts follow
    # the scoped gate instead of the full, unscoped library diff (CLI-audit
    # P1: "SARIF/JUnit computing pass/fail from the full library diff").
    relevant_finding_ids: set[str] = set()
    # Union across ALL apps of relevant Change objects, keyed by finding id --
    # not just their ids -- so scoped-only changes (e.g. PE_ORDINAL_RETARGETED,
    # which scope_diff_to_app synthesizes fresh per app and never adds to
    # result.changes) can still be rendered by SARIF/JUnit instead of only
    # contributing to the gate's exit code with nothing to explain it (Codex
    # review).
    relevant_changes_by_id: dict[str, Any] = {}
    missing_labels: set[str] = set()
    for app in used_by_apps:
        scoped = scope_diff_to_app(
            result,
            app,
            old_lib,
            new_lib,
            policy=policy,
            policy_file=policy_file,
            suppression=suppression,
            # ADR-057: old_lib above is the *path* whenever OLD is a real
            # binary, and a path carries no L5 graph -- pass the snapshot
            # compare's own pipeline already resolved so the consumer-impact
            # join can explain why a consumer required a removed symbol.
            # Graph lookup only; old_lib still owns every export/version read.
            old_snapshot=old_snapshot,
        )
        summaries.append(_app_compat_summary(scoped))
        relevant_finding_ids.update(
            report_finding_id(c) for c in scoped.breaking_for_app
        )
        relevant_changes_by_id.update(
            {report_finding_id(c): c for c in scoped.breaking_for_app}
        )
        # A missing symbol/version already covered by a relevant Change (e.g.
        # FUNC_REMOVED) must not also become a synthetic missing-contract
        # finding -- that would double-report the same ABI break (Codex
        # review, mirrors _scoped_severity_summary's own dedup below).
        missing_labels.update(
            uncovered_missing_symbols(
                list(scoped.missing_symbols) + list(scoped.missing_versions),
                scoped.breaking_for_app,
            )
        )
        exit_code = resolve_scoped_exit_code(
            scoped.verdict,
            scoped.breaking_for_app,
            result,
            severity,
            policy,
            policy_file,
            has_missing_contract=bool(
                scoped.missing_symbols or scoped.missing_versions
            ),
        )
        # exit code (gating) and verdict (reporting) are maxed/ranked
        # independently: under a severity scheme the two can disagree (a
        # BREAKING app can carry exit code 0 under e.g. `--severity-preset
        # info-only`), so picking the reported scoped_verdict by exit code
        # could let a later, less-severe app overwrite an earlier BREAKING
        # one merely because their exit codes tied at 0 (Codex review).
        if severity is not None:
            if exit_code > worst_exit:
                worst_changes = {
                    report_finding_id(c): c for c in scoped.breaking_for_app
                }
                worst_missing = set(scoped.missing_symbols) | set(
                    scoped.missing_versions
                )
            elif exit_code == worst_exit:
                worst_changes.update(
                    {report_finding_id(c): c for c in scoped.breaking_for_app}
                )
                worst_missing |= set(scoped.missing_symbols) | set(
                    scoped.missing_versions
                )
        worst_exit = max(worst_exit, exit_code)
        rank = consumer_verdict_rank(scoped.verdict)
        if worst_verdict is None or rank >= worst_verdict_rank:
            worst_verdict_rank = rank
            worst_verdict = scoped.verdict
    from .gate import gate_exit_code_scheme

    # Workstream D-S1: "N of M consumers affected" -- computed over every
    # supplied consumer's own summary, unreadable-advisory ones included (as
    # non-evaluated), so it stays in step with `summaries` regardless of
    # which apps ended up worst-wins.
    consumer_impact_summary = _consumer_impact_summary(summaries)
    _existing_ids = {report_finding_id(c) for c in result.changes}
    scoped_only_changes = tuple(
        c for fid, c in relevant_changes_by_id.items() if fid not in _existing_ids
    )
    # ADR-067: close the disposition ledger once, here, over the *union* of
    # every consumer's relevant findings. `scope_diff_to_app` deliberately
    # does not close it per app. The union does not narrow the audit's
    # gating set (D-S1: the exit is the full-library decision, so the audit
    # must count what that decision scores). `scoped_only_changes`
    # are passed as newly detected: they are real consumer findings that
    # never reach `result.changes`, so without them the scoped views would
    # count a population the audit does not.
    close_consumer_scope(
        ledger_for(result),
        result,
        gating=list(relevant_changes_by_id.values()),
        also_detected=scoped_only_changes,
        # D-S1: the exit is the full-library decision, not the scoped one.
        scope_decides_exit=False,
    )

    blocking_categories: tuple[str, ...] | None = None
    severity_counts: dict[str, int] | None = None
    if severity is not None:
        blocking_categories, severity_counts = _scoped_severity_summary(
            list(worst_changes.values()),
            worst_missing,
            result,
            severity,
            policy,
            policy_file,
        )
    ScopedGateFields(
        gate_scope="used_by",
        scoped_verdict=worst_verdict,
        scoped_exit_code=worst_exit,
        scoped_exit_code_scheme=gate_exit_code_scheme(severity is not None),
        scoped_relevant_finding_ids=frozenset(relevant_finding_ids),
        scoped_missing_labels=tuple(sorted(missing_labels)),
        scoped_only_changes=scoped_only_changes,
        used_by=summaries,
        consumer_impact_summary=consumer_impact_summary,
        scoped_blocking_categories=blocking_categories,
        scoped_severity_counts=severity_counts,
    ).stamp(result)
    return worst_exit


def _apply_required_symbol_scoping(
    result: Any,
    required_symbols: tuple[str, ...],
    old: Any,
    new: Any,
    policy: str,
    policy_file: PolicyFile | None,
    severity: Any = None,
    suppression: Any = None,
) -> int:
    """Scope *result* to an explicit ``consumers.required_symbols`` contract (ADR-043).

    *suppression* (ADR-067 C-S2, mirrors :func:`_apply_used_by_scoping`'s
    identical parameter -- ADR-044 P2) is forwarded to
    :func:`~abicheck.workflows.consumer_scope.scope_diff_to_required_symbols`: its missing-
    entrypoint overlay is synthesized *after* the pipeline's own suppression
    pass already ran over ``result.changes``, so without this it would be
    unsuppressible even by an exact rule.
    """
    from .consumer_scope import (
        scope_diff_to_required_symbols,
        uncovered_missing_symbols,
    )
    from .disposition import close_consumer_scope, ledger_for

    scoped = scope_diff_to_required_symbols(
        result,
        old,
        new,
        required_symbols,
        policy=policy,
        policy_file=policy_file,
        suppression=suppression,
    )
    contract_summary = _plugin_contract_summary(scoped)
    # An entrypoint already covered by a relevant Change must not also
    # become a synthetic missing-contract finding (Codex review, mirrors
    # _apply_used_by_scoping's identical dedup).
    scoped_missing_labels = tuple(
        sorted(
            uncovered_missing_symbols(
                scoped.missing_entrypoints, scoped.breaking_for_host
            )
        )
    )
    # Scoped-only changes: relevant to the host contract but never added to
    # result.changes (mirrors _apply_used_by_scoping's identical handling).
    _existing_ids = {report_finding_id(c) for c in result.changes}
    scoped_only_changes = tuple(
        c for c in scoped.breaking_for_host if report_finding_id(c) not in _existing_ids
    )
    # ADR-067: same single close as the `--used-by` orchestrator above --
    # `consumers.required_symbols` resolves one scope, but it is still the scope this
    # run's exit code comes from, so the audit's gating set has to follow it.
    close_consumer_scope(
        ledger_for(result),
        result,
        gating=scoped.breaking_for_host,
        also_detected=scoped_only_changes,
        # D-S1: the exit is the full-library decision, not the scoped one.
        scope_decides_exit=False,
    )

    exit_code = resolve_scoped_exit_code(
        scoped.verdict,
        scoped.breaking_for_host,
        result,
        severity,
        policy,
        policy_file,
        has_missing_contract=bool(scoped.missing_entrypoints),
    )
    from .gate import gate_exit_code_scheme

    blocking_categories: tuple[str, ...] | None = None
    severity_counts: dict[str, int] | None = None
    if severity is not None:
        blocking_categories, severity_counts = _scoped_severity_summary(
            scoped.breaking_for_host,
            scoped.missing_entrypoints,
            result,
            severity,
            policy,
            policy_file,
        )
    ScopedGateFields(
        gate_scope="required_symbol",
        scoped_verdict=scoped.verdict,
        scoped_exit_code=exit_code,
        scoped_exit_code_scheme=gate_exit_code_scheme(severity is not None),
        scoped_relevant_finding_ids=frozenset(
            report_finding_id(c) for c in scoped.breaking_for_host
        ),
        scoped_missing_labels=scoped_missing_labels,
        scoped_only_changes=scoped_only_changes,
        required_symbols=contract_summary,
        scoped_blocking_categories=blocking_categories,
        scoped_severity_counts=severity_counts,
    ).stamp(result)
    return exit_code


def apply_scoped_gating(
    result: Any,
    old: Any,
    new: Any,
    policy: str,
    policy_file: PolicyFile | None,
    *,
    used_by_apps: tuple[ConsumerAppInput, ...],
    required_symbols: tuple[str, ...],
    used_by_old_input: Path,
    used_by_new_input: Path,
    severity: Any,
    suppression: Any,
) -> int | None:
    """Apply whichever ADR-043 scoped gate this run selected, if any.

    ``--used-by`` and ``consumers.required_symbols`` are mutually exclusive (rejected
    earlier), so at most one applies; ``--used-by`` wins if both arrive. *severity*
    is the run's ``EffectiveGate.severity`` (``None`` under the legacy scheme).
    Returns the scoped exit code, or ``None`` when the run is unscoped and the
    full-library verdict gates instead.

    Raises :class:`ScopedGateError` subclasses for refused ``--used-by`` inputs.
    Only ``--used-by`` maps consumer-read failures to those types: a
    ``consumers.required_symbols`` read error propagates unwrapped, as it always has.
    """
    if used_by_apps:
        try:
            old_lib, new_lib = resolve_scoping_libs(
                used_by_old_input, used_by_new_input, old, new
            )
            require_used_by_binary_evidence(
                old_lib, new_lib, used_by_old_input, used_by_new_input
            )
            return _apply_used_by_scoping(
                result,
                used_by_apps,
                used_by_old_input,
                used_by_new_input,
                old,
                new,
                policy,
                policy_file,
                severity=severity,
                suppression=suppression,
            )
        except ConsumerUnreadableError as exc:
            # A REQUIRED consumer that cannot be read is an input error (an
            # ADVISORY one never raises -- it comes back `unreadable=True`).
            raise UsedByConsumerError(exc) from exc
        except LibraryExportsUnreadableError as exc:
            # An OLD/NEW library whose export table was not read cannot scope
            # any consumer: refuse rather than report a verdict nobody checked.
            raise UsedByLibraryError(exc) from exc
    if required_symbols:
        return _apply_required_symbol_scoping(
            result,
            required_symbols,
            old,
            new,
            policy,
            policy_file,
            severity=severity,
            suppression=suppression,
        )
    return None
