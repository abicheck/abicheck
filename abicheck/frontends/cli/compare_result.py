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

"""Post-comparison result handling for the ``compare`` CLI command.

``run_compare``'s third phase (resolve -> compare -> report) stays in
:mod:`abicheck.cli_compare_helpers`. This module holds the Click-facing half
that runs once a comparison result exists: attaching ``--use-cases`` impact,
rendering every requested export, and ending in the exit decision. Split out
of ``cli_compare_helpers`` (ADR-061 frontends routing); no behavior change.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ...cli_audit import echo_pattern_modulations
from ...cli_compare_options import echo_coverage_warnings
from ...contract_scoped_promotion import stamp_scoped_result_findings
from ...workflows.compare.use_case_attribution import (
    UseCaseAttributionError,
    attach_use_case_impact,
)
from ..render import ONELINE_FORMAT, resolve_demangle_for_format
from .compare_report import _apply_scoped_gating, _render_compare_report
from .runtime import (
    _announce_exit_scheme,
    _exit_with_severity_or_verdict,
    _finalize_compare_result,
    _write_or_echo,
)

if TYPE_CHECKING:
    from ...model.consumer_spec import ConsumerAppInput
    from ...workflows.policy_file import PolicyFile


def _attach_use_case_impact(
    result: Any, old: Any, new: Any, manifest: Path | None
) -> None:
    """Attach ``compare --use-cases``'s block; a refusal is a usage error (exit 64).

    The typed workflow owns the manifest load and the attribution (see
    :func:`abicheck.workflows.compare.use_case_attribution.attach_use_case_impact`).
    """
    try:
        attach_use_case_impact(result, old, new, manifest)
    except UseCaseAttributionError as exc:
        raise click.UsageError(str(exc)) from exc


def _report_compare_result(
    result: Any,
    old: Any,
    new: Any,
    *,
    resolved_cfg: Any,
    evaluation_config: Any,
    report_severity: Any,
    show_redundant: bool,
    contract_evaluation: bool,
    policy: str,
    pf: PolicyFile | None,
    used_by_apps: tuple[ConsumerAppInput, ...],
    required_symbols: tuple[str, ...],
    used_by_old_input: Path,
    used_by_new_input: Path,
    suppression: Any,
    fmt: str,
    output: Path | None,
    show_only: str | None,
    report_mode: str,
    show_impact: bool,
    follow_deps: bool,
    secondary_writes: tuple[tuple[str, Path], ...],
    require_complete_analysis: bool = False,
    require_complete_analysis_stated: bool = False,
    use_cases_manifest: Path | None = None,
    project_config_path: Path | None = None,
    project_config_sha256: str | None = None,
) -> None:
    """Everything after the comparison: scope, render, exit.

    ``run_compare``'s third phase (resolve -> compare -> report), split out so
    each reads as one job. Terminal: ends in
    :func:`_exit_with_severity_or_verdict`, which never returns.

    *project_config_path*/*project_config_sha256* are ``run_compare``'s own
    already-resolved ``cfg_path``/``cfg_sha`` (the ``.abicheck.yml``
    ``resolve_project_compare_config`` loaded for this same invocation) -- forwarded
    to :func:`~abicheck.cli_compare_receipt.record_resolved_config` so a
    ``gate.require_complete_analysis`` receipt entry can name the real
    document/digest that supplied ``assurance.require_complete``, the same
    identity every other project-config-sourced provenance entry in this
    receipt already carries (P2, Codex review, fresh evidence).

    *require_complete_analysis_stated* is whether the resolved project
    config LITERALLY carried an ``assurance.require_complete`` key (true or
    false), distinct from *require_complete_analysis* itself, which already
    collapses an explicit ``false`` and an omitted key onto the same
    resolved value -- see ``contract_gate_require_complete_provenance.py``'s
    own module docstring (Codex review, fresh evidence, PR #1222 fourth
    round, second finding).
    """
    from ...cli_compare_receipt import record_resolved_config

    record_resolved_config(
        result,
        resolved_cfg,
        evaluation_config,
        project_config_path=project_config_path,
        project_config_sha256=project_config_sha256,
        require_complete_analysis_stated=require_complete_analysis_stated,
    )

    # Plan slice 7o: unconditional. The pattern-modulation ledger used to be
    # gated on `--view patterns`; ADR-067's record-before-disposing rule makes
    # a disposition's own reason part of the result, not a display preference.
    # Echoed only when there is something to disclose -- an unconditional
    # "no modulations applied" line on every run would be noise, not
    # accounting, and would make every quiet run differ from before for no
    # reader's benefit.
    if result.pattern_modulations:
        echo_pattern_modulations(result)

    _finalize_compare_result(
        result,
        show_redundant=show_redundant,
        contract_evaluation=contract_evaluation,
    )

    # Workstream D-S1 (vision-api-abi-evolution.md "D. Optional
    # prebuilt-consumer lifecycle"): a supplied `--used-by`/`--required-symbol`
    # consumer used to *replace* the process's gate outright (`scoped_verdict`/
    # `gate_scope`, worst-app-wins, `sys.exit(scoped_exit_code)` below). That is
    # reverted -- a consumer's confirmed/potential/unresolved impact is
    # reported *beside* the full-library compatibility result computed by
    # `_exit_with_severity_or_verdict` further down; it never substitutes for
    # it and never narrows what the run gates on. `_apply_scoped_gating` still
    # runs (unchanged) to populate `result.used_by`/`result.required_symbols`/
    # `result.scoped_verdict`/`result.scoped_exit_code`/etc. for the report's
    # enrichment section -- those fields are read by
    # `report/scoped_gate.py`/`sarif.py`/`junit_report.py`/`html_report.py` to
    # render the per-consumer breakdown -- but the value returned here is no
    # longer used to pick the process exit code.
    _apply_scoped_gating(
        result,
        old,
        new,
        policy,
        pf,
        used_by_apps=used_by_apps,
        required_symbols=required_symbols,
        used_by_old_input=used_by_old_input,
        used_by_new_input=used_by_new_input,
        severity=report_severity,
        suppression=suppression,
    )
    # `result.scoped_exit_code` (as computed by `_apply_scoped_gating`) is
    # still the *pre-orthogonal-floor* per-consumer number at this point --
    # left as-is (informational) rather than folded against
    # coverage/analysis-assurance, since neither axis is scored against a
    # per-consumer scope; both fold only against the global exit code
    # `_exit_with_severity_or_verdict` computes below.

    # ADR-068 D4/Phase 5: computed unconditionally whenever a suppression
    # file is in play -- "forgot --audit-suppressions" must never withhold a
    # fact from the canonical result. The flag survives only as a rendering
    # choice (the markdown "## Suppression Audit" section, gated below in
    # _render_compare_report); JSON/SARIF/JUnit/HTML carry the field either way.
    from ...workflows.suppression_audit_attach import attach_suppression_audit

    attach_suppression_audit(result, suppression)

    _attach_use_case_impact(result, old, new, use_cases_manifest)

    # ADR-049 Phase 3 (Codex review, fresh evidence): --used-by/
    # --required-symbol scoping above can add scoped_only_changes (fresh
    # Change objects scope_diff_to_app/scope_diff_to_required_symbols
    # synthesize, e.g. PE_ORDINAL_RETARGETED) and mark existing
    # result.changes entries as relevant to the scoped contract -- neither
    # ever passes through checker._apply_contract_evaluation_shadow, so
    # both stayed permanently unstamped even when --contract was
    # given. This must run before _render_output below serializes
    # result.changes, and mirrors the identical fix already applied to the
    # MCP abi_compare tool (mcp_server.py) -- both share the same traversal (CodeRabbit review: hand-copying it here previously let one call site drift out of sync with the other).
    if contract_evaluation:
        from ...report.view import finding_id

        stamp_scoped_result_findings(result, finding_id=finding_id)
    # Only the same-binary warning, not every pre-existing coverage_warnings entry ("no binary metadata"/detector-disabled reasons) -- those are deliberately absent from the one-line summary today (existing tests pin exactly zero extra lines).
    # Plan slice 7m: one export request, so one emission loop. Every target
    # -- the stdout one and every file one alike -- renders the *same*
    # already-computed `result` under the *same* presentation options, which
    # is what makes §7's F-19 structural rather than tested per permutation:
    # the canonical result block cannot differ between exports because two
    # exports of one format are literally the same string (rendered once,
    # memoized below), and two exports of different formats are two
    # projections of one `ReportEnvelope` (ADR-061 gap C).
    #
    # The retired `-o` deliberately rendered its artifacts *unfiltered*
    # (`show_only=None, report_mode="full"`) regardless of what the primary
    # the stdout export was asked for. That asymmetry was defensible only while
    # "primary" and "secondary" were two different flags carrying two
    # different grammars; under one repeatable operand there is no principled
    # answer to "which of `-o json=a.json -o markdown=-` is the unfiltered
    # one", so a display selector now means the same thing for every export
    # it is rendered into. A reader wanting the complete document alongside a
    # narrowed human view still has one: `--view show=` narrows what is
    # *displayed*, and every machine projection continues to carry the full
    # disposition/suppression accounting plus a `show_only_filter`/
    # `filtered_summary` block stating exactly what the filter did.
    #
    # `demangle` stays resolved *per format* (`resolve_demangle_for_format`) -- that is
    # not a per-target grammar but a property of the format itself (human
    # formats demangle, machine formats keep raw mangled symbols so tooling
    # can match on them), and it is exactly what the primary target's own
    # already-resolved `demangle` argument is (see `_normalize_compare_
    # options`, which resolves it through the same function).
    targets: tuple[tuple[str, Path | None], ...] = ((fmt, output), *secondary_writes)
    if any(target_fmt == ONELINE_FORMAT for target_fmt, _ in targets):
        echo_coverage_warnings(
            [w for w in result.coverage_warnings if "byte-identical" in w]
        )
    rendered: dict[str, str] = {}
    for target_fmt, target_output in targets:
        text = rendered.get(target_fmt)
        if text is None:
            text = _render_compare_report(
                result,
                old,
                new,
                fmt=target_fmt,
                follow_deps=follow_deps,
                show_only=show_only,
                report_mode=report_mode,
                show_impact=show_impact,
                severity_config=report_severity,
                demangle=resolve_demangle_for_format(target_fmt),
                contract_evaluation=contract_evaluation,
                require_complete_analysis=require_complete_analysis,
            )
            rendered[target_fmt] = text
        _write_or_echo(target_output, text)

    # Workstream D-S1: no early `sys.exit` on the scoped/consumer result here
    # any more -- `--used-by`/`--required-symbol(s)` no longer float their own
    # exit code over the full-library one. Every run, scoped or not, exits
    # through the identical `_exit_with_severity_or_verdict` path below, so
    # the compatibility verdict a supplied consumer enriches can never be
    # narrowed or replaced by that consumer's own result.
    _announce_exit_scheme(resolved_cfg.exit_code_scheme, fmt=fmt)
    from ...workflows.gate import effective_gate_for_resolved_compare_config

    _exit_with_severity_or_verdict(
        result,
        effective_gate_for_resolved_compare_config(
            resolved_cfg,
            result=result,
            require_complete_analysis=require_complete_analysis,
        ),
        fmt,
        [f for f, _ in secondary_writes],
    )
