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

"""The report-level blocks every JSON path attaches after the findings.

Split out of :mod:`abicheck.reporter`, which is at the 2000-line hard cap --
same reason ``reporter_markdown``/``report_summary`` live beside it. Every
import here is function-local for the reason the original site documented:
``contract_evidence``/``contract_context_io`` reach
``compatibility_evaluation_config`` -> ``checker_policy``, which a module-level
import would pull into every consumer of a ``DiffResult``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .impact.use_case_impact import add_use_case_impact

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .checker_types import Change, DiffResult
    from .severity import SeverityConfig


def add_contract_context(
    d: dict[str, Any],
    result: DiffResult,
    displayed: Sequence[Change] | None = None,
    *,
    require_complete_analysis: bool = False,
    severity_config: SeverityConfig | None = None,
    include_exit_decision: bool = True,
) -> None:
    """ADR-049 Phase 4's persisted contract blocks, plus P0.4's
    ``analysis_assurance``/``analysis_assurance_exit_contribution``
    (piggybacked here, unguarded below, to stay under the file-size cap).
    ``contract_context`` itself stays opt-in
    (``compare(..., contract_evaluation=True)``), serialized via
    :mod:`abicheck.contract_context_io` to match
    :func:`~abicheck.contract_replay.replay_original_decisions`. Called from
    all three JSON paths, same as ``_add_surface_scope``/``_add_reconciled``.
    """
    from .analysis_assurance import (
        analysis_assurance_exit_contribution,
        analysis_assurance_report_dict,
    )

    if (block := analysis_assurance_report_dict(result)) is not None:
        d["analysis_assurance"] = block
        # Persisted alongside the block itself, not unconditionally: a
        # `result` carrying no real `AnalysisAssurance` (a hand-built
        # object, e.g. in a test) has nothing to report a contribution
        # *for* either. Read by `aggregate.py`'s `_analysis_assurance_exit`
        # via the same document-shape traversal `contract_coverage_exit_
        # contribution` already uses -- without persisting this, a compare
        # report whose severity/compatibility gate read a clean 0 while
        # this axis independently floored the *real* exit to 1 fed
        # `abicheck aggregate` a green result for that report, since
        # neither `GateInfo.from_report_data` nor `from_scan_report` reads
        # this orthogonal axis (Codex review, PR #780). `0` covers both
        # "the flag was never given" and "given but already complete".
        d["analysis_assurance_exit_contribution"] = (
            analysis_assurance_exit_contribution(
                result, require_complete=require_complete_analysis
            )
        )
    # CLI cleanup phase two, PR G1: the same canonical `ExitDecision`
    # `cli._exit_with_severity_or_verdict` resolves for the real process
    # exit, persisted here so a report reader doesn't have to re-derive it
    # from `severity.exit_code`/`contract_coverage_exit_contribution`/
    # `analysis_assurance_exit_contribution` separately. `severity_config
    # is not None` is the same signal every other block in this module and
    # in `reporter.py` already uses to mean "the severity-aware scheme is in
    # effect" (`cli_compare_helpers.report_severity` is `None` whenever
    # `resolved_cfg.exit_code_scheme != "severity"`), so this reproduces
    # `_exit_with_severity_or_verdict`'s own scheme selection rather than
    # guessing at a new one. Unconditional for a native `compare` call --
    # unlike `contract_context` below, every comparison has a compatibility
    # contribution, so there is always a decision to report, not just under
    # `--contract`. `include_exit_decision=False` (only `compat/cli.py`
    # passes this) skips it entirely: `compat check`'s own process exit
    # follows an unrelated 0/1/2 ABICC-style scheme, so this block's
    # native-scheme `code` would disagree with the real compat exit for the
    # same run (Codex review).
    if include_exit_decision:
        from .exit_decision import resolve_compare_exit_decision

        scheme = "severity" if severity_config is not None else "legacy"
        d["exit"] = resolve_compare_exit_decision(
            result, severity_config, scheme,
            require_complete_analysis=require_complete_analysis,
        ).to_dict()
    add_use_case_impact(d, result, displayed)

    ctx = result.contract_context
    if ctx is None:
        return
    from .contract_context_io import persisted_context_to_dict
    from .contract_evidence import PersistedContractContext

    # `DiffResult.contract_context` is typed `object` (its real type reaches
    # `compatibility_evaluation_config` -> `checker_policy`, which every
    # consumer of `DiffResult` would then import), so narrow it here rather
    # than suppressing the argument type -- an `isinstance` check is also a
    # real guard against a caller having stuffed something else into an
    # untyped field (CodeRabbit review).
    if not isinstance(ctx, PersistedContractContext):
        return
    d["contract_context"] = persisted_context_to_dict(ctx)
    # ADR-049 Phase 5's *sibling* ledger (plan Section 6.1). It sits beside
    # the findings, not among them, because that is what makes it
    # unsuppressible: a coverage failure is not a `Change`, so
    # `checker._filter_suppressed_changes` -- the one place suppression is
    # applied -- can never see one, and "ordinary change suppressions ...
    # cannot suppress a provider/domain coverage failure" (Section 6.2) is a
    # structural fact rather than a rule something has to remember to
    # enforce. Emitted as `[]` rather than omitted when there are none: an
    # empty ledger is the real, checkable answer "this domain closed", which
    # an absent key could not distinguish from "not computed".
    from .contract_coverage_exit import coverage_exit_for_context
    from .contract_coverage_ledger import coverage_failures_for_context

    failures = coverage_failures_for_context(ctx)
    d["contract_coverage_failures"] = [f.to_dict() for f in failures]
    # ADR-049 Phase 7: what the ledger contributes to the exit code, now
    # actually applied rather than merely stated. Derived by the same
    # function the exit path uses, so the number a user reads is the one
    # that gated them -- including `contract.unresolved=warn` zeroing it
    # while the failures above stay listed, which is what accepting
    # incomplete assurance means as opposed to hiding it.
    d["contract_coverage_exit_contribution"] = coverage_exit_for_context(ctx)


