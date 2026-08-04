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

"""Verdict, exit-code and change-entry helpers for the MCP tool surface.

Split out of :mod:`abicheck.mcp_server` when that module crossed the
AI-readiness hard 2000-line cap. A leaf by construction: it imports only
``checker_policy`` and ``mcp_shared``'s logger at module scope (the
heavier ``severity``/``reporter``/``appcompat`` dependencies stay
function-local exactly as they were), so ``mcp_server`` keeps a
one-directional edge onto it and no import cycle forms.

Every function here is a near-copy of its ``cli_compare_helpers``
counterpart, and deliberately so — each docstring names the sibling it
mirrors and why the MCP surface needs its own. That duplication predates
this split and is untouched by it; this module only moves the code.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from .checker_policy import ChangeKind, policy_kind_sets
from .mcp_shared import _logger

if TYPE_CHECKING:
    from .severity import SeverityConfig


def _scoped_verdict_exit_code(verdict: object) -> int:
    """Map a scoped-comparison (--used-by/--required-symbols) Verdict to its
    floor exit code (ADR-043): BREAKING -> 4, API_BREAK -> 2, else 0."""
    value = getattr(verdict, "value", verdict)
    if value == "BREAKING":
        return 4
    if value == "API_BREAK":
        return 2
    return 0


def _scoped_exit_code(
    verdict: object, relevant_changes: list[Any], result: Any,
    severity_config: SeverityConfig | None, policy: str, policy_file: object,
    *, has_missing_contract: bool = False,
) -> int:
    """Scoped-verdict exit code, respecting a severity config when given.

    Mirrors ``cli_compare_helpers._scoped_exit_code``: without this, a
    used_by/required_symbols scope always fell back to the legacy 0/2/4
    verdict floor, silently ignoring any severity_* argument the caller
    passed (parity bug with the severity-aware unscoped path above).

    *has_missing_contract* (a required symbol/version/entrypoint absent from
    the new library) floors the severity-scheme exit code separately from
    *relevant_changes*: a missing contract symbol is BREAKING but is not a
    diff Change, so ``compute_exit_code`` never sees it and would otherwise
    return 0 (Codex review).
    """
    if severity_config is not None:
        from .severity import compute_exit_code, missing_contract_exit_code

        code = compute_exit_code(
            relevant_changes, severity_config,
            policy=policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=policy_file,
        )
        if has_missing_contract:
            code = max(code, missing_contract_exit_code(severity_config))
        return code
    return _scoped_verdict_exit_code(verdict)


def _scoped_severity_summary(
    relevant_changes: list[Any], missing: Iterable[str],
    result: Any, severity_config: SeverityConfig, policy: str, policy_file: object,
) -> tuple[tuple[str, ...], dict[str, int]]:
    """(blocking_categories, per-category counts) for one scoped result.

    Mirrors ``cli_compare_helpers._scoped_severity_summary``: a missing
    contract symbol/version/entrypoint with no matching diff Change is
    folded into ``abi_breaking`` directly here -- into the blocking
    -categories set (when abi_breaking is severity-configured as error,
    matching the exit-code floor) and into the count (always, since a count
    is a factual tally, not a gate decision). A *missing* entry that already
    has a matching Change in *relevant_changes* is excluded via
    ``uncovered_missing_symbols`` so it isn't counted twice.
    """
    from .appcompat import uncovered_missing_symbols
    from .severity import (
        IssueCategory,
        SeverityLevel,
        categorize_changes,
        compute_gate_decision,
    )

    categorized = categorize_changes(
        relevant_changes, policy=policy,
        kind_sets=result._effective_kind_sets(), policy_file=policy_file,
    )
    counts = {
        "abi_breaking": len(categorized.abi_breaking),
        "potential_breaking": len(categorized.potential_breaking),
        "quality_issues": len(categorized.quality_issues),
        "addition": len(categorized.addition),
    }
    gate = compute_gate_decision(
        relevant_changes, severity_config,
        policy=policy, kind_sets=result._effective_kind_sets(), policy_file=policy_file,
    )
    categories = list(gate.blocking_categories)
    uncovered = uncovered_missing_symbols(missing, relevant_changes)
    if uncovered:
        counts["abi_breaking"] += len(uncovered)
        if (
            severity_config.abi_breaking == SeverityLevel.ERROR
            and IssueCategory.ABI_BREAKING.value not in categories
        ):
            categories.append(IssueCategory.ABI_BREAKING.value)
    return tuple(categories), counts


_VERDICT_SEVERITY_RANK = {
    "BREAKING": 3, "API_BREAK": 2, "COMPATIBLE_WITH_RISK": 1,
    "COMPATIBLE": 0, "NO_CHANGE": 0,
}


def _verdict_severity_rank(verdict: object) -> int:
    """Rank a Verdict by severity, independent of any exit-code scheme.

    Mirrors ``cli_compare_helpers._verdict_severity_rank``: under a severity
    scheme a BREAKING app can carry exit code 0 (e.g. an info-only preset),
    so picking the reported scoped verdict by exit code could let a later,
    less-severe app overwrite an earlier BREAKING one merely because their
    exit codes tied at 0 (Codex review).
    """
    value = getattr(verdict, "value", verdict)
    return _VERDICT_SEVERITY_RANK.get(value, 0) if isinstance(value, str) else 0


def _impact_category(kind: ChangeKind, policy: str = "strict_abi") -> str:
    """Return the impact category string for a ChangeKind under the given policy.

    When *policy* is not ``strict_abi``, some kinds may be downgraded
    (e.g. ``sdk_vendor`` downgrades source-level renames from ``api_break``
    to ``compatible``).  This ensures per-change impact labels agree with
    the policy-aware verdict.
    """
    breaking, api_break, compatible, risk = policy_kind_sets(policy)
    if kind in breaking:
        return "breaking"
    if kind in api_break:
        return "api_break"
    if kind in risk:
        return "risk"
    if kind in compatible:
        return "compatible"
    _logger.warning(
        "_impact_category: unknown ChangeKind %r, defaulting to breaking", kind
    )
    return "breaking"  # fail-safe for unknown kinds


def _mcp_change_entry(
    c: Any,
    policy: str,
    *,
    severity_config: Any = None,
    kind_sets: Any = None,
    policy_file: Any = None,
) -> dict[str, object]:
    """Build ``abi_compare``'s top-level, compact ``changes``/scoped-only
    entry for one ``Change``.

    Also attaches ADR-049's per-finding contract-evaluation fields
    (``contract_relevance``/``contract_reason_code``/``contract_assurance``,
    plus D1's ``compatibility_evaluation_status``/``compatibility_decision``/
    ``gate_contribution``) via ``reporter._add_contract_evaluation_fields``
    -- the same helper the full ``response["report"]`` JSON already uses for
    its own ``changes`` entries. Without this, a caller opting into
    ``contract_evaluation=True`` saw the decision in
    ``response["report"]["changes"]`` but not in this top-level, more
    commonly consumed array, silently losing the result the caller asked for
    (Codex review, fresh evidence). A no-op when *c* was never stamped
    (``contract_evaluation`` not requested), mirroring that helper's own
    documented default.

    Unlike the audit-ledger entries that helper also serializes, these
    findings *do* reach a gate -- they are ``result.changes`` (and the
    scoped-only overlay, which the scoped gate scores) -- so the run's own
    gate inputs are threaded in and the emitted ``gate_contribution`` is the
    number that actually applied, not a default ``0``.
    """
    from .reporter import _add_contract_evaluation_fields
    from .severity import gate_contribution_for_change

    entry: dict[str, object] = {
        "kind": c.kind.value,
        "symbol": c.symbol,
        "description": c.description,
        "impact": _impact_category(c.kind, policy),
        "old_value": c.old_value,
        "new_value": c.new_value,
        "source_location": c.source_location,
    }
    _add_contract_evaluation_fields(
        entry,
        c,
        gate_contribution=gate_contribution_for_change(
            c, severity_config, policy=policy,
            kind_sets=kind_sets, policy_file=policy_file,
        ),
    )
    return entry
