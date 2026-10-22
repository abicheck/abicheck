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

"""Orchestration body for the ``compare`` command (size-split from cli.py).

The click-decorated ``compare`` wrapper in :mod:`abicheck.cli` parses options
and delegates to :func:`run_compare` here, keeping cli.py under the
AI-readiness file-size cap. Not a leaf helper module
(plain, cli-independent utilities): ``run_compare`` drives the full
single-pair compare flow and reuses the option-parsing/render/exit helpers
still in :mod:`abicheck.cli` (imported back below -- the by-design sibling
cycle, allow-listed in ``check_ai_readiness``). Classification goes through
the shared ``workflows.compare_classify`` core every scalar route runs, never
a direct ``checker.compare`` call (cli-contract, ADR-037 D10.1).
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from . import cli_resolve
from .cli_audit import echo_pattern_modulations
from .cli_compare_fold import (
    _exit_on_budget_overflow,
    report_not_comparable_and_exit,
)
from .cli_compare_options import (
    _NormalizedCompareOptions,
    _param_from_cli,
    _reject_bundle_facts_out_for_single_pair,
    _resolve_debug_roots,
    _warn_force_public_ignored,
    echo_coverage_warnings,
)
from .cli_dump_helpers import resolve_dump_debug_format
from .cli_options import (
    resolve_compile_context,
    resolve_contract_domain,
    resolve_contract_evaluation,
)
from .cli_resolve import (
    _resolve_compare_pair,
    classify_compare_operand,
    resolve_directory_compile_context,
)
from .contract_scoped_promotion import stamp_scoped_result_findings
from .errors import (
    ProfileMismatchError,
    ScopeMismatchError,
    SnapshotError,
    ValidationError,
)
from .frontends.cli import compare_enrichment as _enrichment
from .frontends.cli.acknowledgment_config import (
    compare_acknowledgments_for,
    release_acknowledgment_inputs,
)
from .frontends.cli.compare_report import (
    # A cohesive slice of ``run_compare``'s post-comparison phase (scoped
    # gating, report rendering, suppression-audit attachment, set-input flag
    # rejection) -- size-split out to its own module (see that module's own
    # docstring for why, including why ``_resolve_evaluation_config``/
    # ``_attach_use_case_impact``/``_report_compare_result`` stay here
    # instead of moving too). Imported back here, not just called from
    # there, so every existing ``cli_compare_helpers.<name>`` reference
    # (docstrings/comments elsewhere in this codebase) keeps resolving
    # unchanged -- a bare name is looked up in the *calling* module's
    # globals at call time, not in the module that defined it.
    _apply_scoped_gating as _apply_scoped_gating,
    _reject_flags_unsupported_for_set_inputs,
    _render_compare_report as _render_compare_report,
)
from .frontends.cli.compare_use_cases import reject_use_cases_without_carrying_output
from .frontends.cli.contract_overlays import (
    note_unapplied_post_manifest,
    post_manifest_allowlist_for,
)
from .frontends.cli.ignored_flag_warnings import _warn_ignored_flags
from .frontends.cli.options.params import _load_suppression_and_policy
from .frontends.cli.ownership_config import project_ownership_request
from .frontends.cli.project_config import (
    compare_settings_usage_errors,
    fold_project_policy_overrides_for_cli,
    resolve_project_compare_config,
)
from .frontends.cli.runtime import (
    _announce_exit_scheme,
    _exit_with_severity_or_verdict,
    _finalize_compare_result,
    _log_debug_resolution,
    _setup_verbosity,
    _write_or_echo,
)
from .frontends.render import ONELINE_FORMAT, resolve_demangle_for_format
from .model.sided_inputs import resolve_per_side_inputs
from .report.report_modes import normalize_report_mode
from .serialization import run_scoped_digest_cache
from .workflows.compare.configuration import (
    fold_compare_collect_inputs,
    resolve_required_symbol_policy,
    split_compile_context_frontend,
)
from .workflows.compare.use_case_attribution import (
    UseCaseAttributionError,
    attach_use_case_impact,
    read_use_case_manifest,
)
from .workflows.compare_configuration import (
    _pair_wide_dialect_override,
    load_required_symbols,
    resolve_compare_collect_mode,
    resolve_force_public_scope,
)
from .workflows.header_exclusion_audit import unmatched_exclusion_warning
from .workflows.operand_rules import (
    DebugFormatNotElfError,
    ManifestOperandError,
    reject_debug_format_for_non_elf,
    reject_manifest_header_conflicts,
    reject_manifest_non_elf,
)
from .workflows.ownership_request import project_ownership_scope
from .workflows.public_header_boundary import (
    project_config_public_header_dirs,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .model.consumer_spec import ConsumerAppInput
    from .workflows.compare_configuration import ResolvedCompareConfig
    from .workflows.extraction import DumpManifest
    from .workflows.policy_file import PolicyFile


def _normalize_compare_options(
    resolved_cfg: ResolvedCompareConfig,
    *,
    depth: str | None,
    headers: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    debug_format_opt: str | None,
    fmt: str,
    report_mode: str,
    old_sources: Path | None = None,
    new_sources: Path | None = None,
    old_build_info: Path | None = None,
    new_build_info: Path | None = None,
) -> _NormalizedCompareOptions:
    """Fold the compare option flags into dispatch-ready values.

    The semantic collect fold is ``fold_compare_collect_inputs``; the flag
    spellings (debug format, demangle, report mode) resolve here.
    """
    # Fold the --depth dial into the internal collect mode (ADR-037 D5), the
    # same way `dump` does; when omitted, infer it from --sources/--build-info
    # (or config source.method) rather than defaulting to "off" (P1 fix).
    with compare_settings_usage_errors():
        collect_mode, headers, old_headers_only, new_headers_only = (
            fold_compare_collect_inputs(
                depth=depth,
                source_method=resolved_cfg.source_method,
                headers=headers,
                old_headers_only=old_headers_only,
                new_headers_only=new_headers_only,
                old_sources=old_sources,
                new_sources=new_sources,
                old_build_info=old_build_info,
                new_build_info=new_build_info,
            )
        )

    # An explicit "auto" returns to auto-detection (None); any other value is
    # passed through verbatim.
    effective_debug_format = resolve_dump_debug_format(debug_format_opt)

    demangle_resolved = resolve_demangle_for_format(fmt)

    # --report-mode impact is sugar for a "full" report with the impact table
    # on -- the one way to ask for that table (the separate --show-impact flag
    # it used to duplicate is gone). The fold itself is *not* this layer's to
    # own: it used to live here and in `_dispatch_release_compare` as two
    # private copies, which is why every public Python rendering path silently
    # ignored `report_mode="impact"` (Codex review, PR #1284). One function,
    # shared with those paths.
    report_mode, show_impact = normalize_report_mode(report_mode)

    return _NormalizedCompareOptions(
        collect_mode,
        headers,
        old_headers_only,
        new_headers_only,
        effective_debug_format,
        demangle_resolved,
        report_mode,
        show_impact,
    )


def _classify_and_reject_operands(
    old_input: Path,
    new_input: Path,
) -> tuple[str, str]:
    """Classify both compare operands and reject an application/PIE operand.

    ADR-037 D7 input-type dispatch: a directory/package operand fans out to a
    per-library comparison; an application/PIE operand is not a library `compare`
    can pair (hint at `appcompat`). A single .so / snapshot / dump falls through.
    """
    from .frontends.cli.operand_diagnostics import _reject_application_operand

    old_kind = classify_compare_operand(old_input)
    new_kind = classify_compare_operand(new_input)
    if old_kind == "app" or new_kind == "app":
        _reject_application_operand(old_input, new_input, old_kind, new_kind)
    return old_kind, new_kind


#: ADR-068 §3 #19: mirrored ``cli_scan._DURATION_UNITS`` (kept as its own
#: copy -- ``scan`` has since been deleted by ADR-068 Phase 6).
_DURATION_UNITS: dict[str, int] = {"s": 1, "m": 60, "h": 3600}


def _parse_budget(value: str | None) -> float | None:
    """Parse a ``time``-style duration (``15m``/``900s``/``1h``) to seconds.

    A bare number is seconds; ``None``/empty means no budget. Raises
    :class:`click.BadParameter` for an unparseable value.
    """
    if not value:
        return None
    raw = value.strip().lower()
    unit = 1
    if raw and raw[-1] in _DURATION_UNITS:
        unit = _DURATION_UNITS[raw[-1]]
        raw = raw[:-1]
    try:
        amount = float(raw)
    except ValueError as exc:
        raise click.BadParameter(
            f"invalid --budget {value!r}; use e.g. 15m, 900s, 1h"
        ) from exc
    # CodeRabbit review: nan/inf both pass `< 0` and would store a deadline
    # `left <= 0` never trips, silently defeating the budget.
    if not math.isfinite(amount) or amount < 0:
        raise click.BadParameter(
            f"--budget must be a finite, non-negative duration, got {value!r}"
        )
    return amount * unit


def _enter_budget_scope(ctx: click.Context, budget_s: float | None) -> None:
    """Set ``--budget``'s deadline for the rest of this Click invocation.

    Entered once, as early as possible -- inline ``--sources``/
    ``--build-info`` embedding included (Codex review: entering only around
    resolution left it unbounded). ``ctx.call_on_close`` (not a ``with``
    wrapping the rest of this function) releases it on every early exit
    Click already tears the context down for, so it never leaks into the
    next ``CliRunner`` invocation in the same test process. A no-op for
    ``budget_s is None`` (``--budget`` omitted) -- unbounded, as today.
    """
    if budget_s is None:
        return
    from . import deadline

    scope = deadline.deadline_scope(budget_s)
    scope.__enter__()
    ctx.call_on_close(lambda: scope.__exit__(None, None, None))


# Plan slice 7m: ``_reject_incoherent_compare_flags`` is gone. Both rules it
# enforced -- a dry run asked to write a report, and two reports aimed at one
# file -- are now properties of the one export request, checked by
# ``frontends.cli.options.export`` while the operand is parsed: collision
# detection runs across *every* target rather than only between the primary
# and the repeats of one flag, and ``reject_dry_run_with_exports`` covers the
# dry run at ``compare``'s own callback boundary. Two flags with two grammars
# needed a coherence check between them; one grammar does not.


def _preflight_manifests_and_audit(
    *,
    old_dump_manifest: Path | None,
    new_dump_manifest: Path | None,
    pack_paths: Any,
    policy_file_path: Path | None,
    contract_evaluation: bool,
) -> tuple[DumpManifest | None, DumpManifest | None]:
    """Load the dump manifests and reject what a dry run must not report "ok".

    The loading and validation are in ``workflows.compare.manifests``; this
    renders their typed refusals as the usage errors ``compare`` prints.
    """
    from .errors import ManifestValidationError, PackManifestError
    from .workflows.compare.manifests import (
        load_compare_dump_manifests,
        validate_compare_pack_manifests,
    )

    try:
        manifests = load_compare_dump_manifests(old_dump_manifest, new_dump_manifest)
    except ManifestValidationError as exc:
        raise click.UsageError(str(exc)) from exc
    try:
        validate_compare_pack_manifests(
            pack_paths,
            policy_file_path=policy_file_path,
            contract_evaluation=contract_evaluation,
        )
    except PackManifestError as exc:
        raise click.UsageError(str(exc)) from exc
    return manifests


@contextmanager
def _operand_rule_errors() -> Iterator[None]:
    """Word a refused operand combination the way ``compare`` words its flags.

    The rules are decided in :mod:`abicheck.workflows.operand_rules`; this only
    renders the typed refusal as the usage error (or ``.abicheck.yml`` key
    message) this command has always printed.
    """
    try:
        yield
    except DebugFormatNotElfError as exc:
        raise click.BadParameter(
            f"debug.format: {exc.debug_format} (.abicheck.yml) is only supported "
            f"for ELF binaries, but the {exc.side} input is {exc.binary_fmt.upper()}."
        ) from exc
    except ManifestOperandError as exc:
        raise click.UsageError(str(exc)) from exc


def _resolve_evaluation_config(
    ctx: click.Context,
    *,
    headers: tuple[Path, ...],
    resolved_cfg: Any,
    project_cfg: Any,
    cfg_path: Path | None,
    cfg_sha: str | None,
    config_explicit: bool,
    policy: str,
    policy_file_path: Path | None,
    policy_file: PolicyFile | None,
    suppression: Any,
    suppress: Path | None,
    symbols_list: Any,
    contract_mode: str | None,
    contract_evaluation: bool,
    require_justification: bool,
    severity_preset: str | None,
    pack_paths: tuple[Path, ...],
    policy_selected_by: str | None,
    policy_selected_path: Path | None,
    policy_selected_sha: str | None,
) -> tuple[Any, PolicyFile | None, Any]:
    """Adapt the CLI inputs to ``workflows.compare.configuration`` and render refusals.

    The raw flag values (not merged locals, which would look CLI-stated) and
    the Click-typed set are the CLI's; the fold itself is in the workflow.
    """
    from .workflows import compare_input_adapters as _adapters
    from .workflows.compare.configuration import resolve_evaluation_configuration
    from .workflows.compare_configuration import (
        ConfigurationConflictError,
        ProjectPolicyOverridesError,
    )

    try:
        return resolve_evaluation_configuration(
            {
                "contract_mode": contract_mode,
                "policy": policy,
                "policy_file_path": policy_file_path,
                "suppress": suppress,
                "require_justification": require_justification,
                "severity_preset": severity_preset,
                "pack_paths": pack_paths,
                # The roots each side was classified under (ADR-075 D7).
                "headers": headers,
            },
            # The stated settings: only this module holds the Click context.
            typed={n for n in _adapters.typed_parameter_names() if _param_from_cli(n)},
            resolved_cfg=resolved_cfg,
            project_cfg=project_cfg,
            cfg_path=cfg_path,
            cfg_sha=cfg_sha,
            config_explicit=config_explicit,
            policy=policy,
            policy_file=policy_file,
            suppression=suppression,
            suppress=suppress,
            symbols_list=symbols_list,
            contract_evaluation=contract_evaluation,
            policy_selected_by=policy_selected_by,
            policy_selected_path=policy_selected_path,
            policy_selected_sha=policy_selected_sha,
        )
    except ConfigurationConflictError as exc:
        raise click.UsageError(str(exc)) from exc
    except ProjectPolicyOverridesError as exc:
        raise click.BadParameter(str(exc), param_hint="--policy") from exc


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
    ctx: click.Context,
    result: Any,
    old: Any,
    new: Any,
    *,
    old_input: Path,
    new_input: Path,
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
    from .cli_compare_receipt import record_resolved_config

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
        severity_config=report_severity,
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
    from .workflows.suppression_audit_attach import attach_suppression_audit

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
        from .report.view import finding_id

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
    from .workflows.gate import effective_gate_for_resolved_compare_config

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


@run_scoped_digest_cache
def run_compare(
    ctx: click.Context,
    *,
    exclude_headers: tuple[str, ...] = (),
    old_input: Path,
    new_input: Path,
    output_dir: Path | None,
    select: tuple[str, ...] = (),
    select_required: tuple[str, ...] = (),
    debug_info1: Path | None,
    debug_info2: Path | None,
    devel_pkg1: Path | None,
    devel_pkg2: Path | None,
    manifest_path: Path
    | None,  # bundle_system_providers/cohorts: PR J, see resolved_cfg
    bundle_facts_out: Path | None,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    # Phase 7 (one-comparison-product.md §4.1, ADR-037 D8.1): `lang`/
    # `header_backend`/`sysroot`/`nostdinc`/`compiler_path`/
    # `compiler_prefix`/`compiler_option_tokens`/`old_header_backend`/
    # `new_header_backend` are no longer CLI-populated -- `--lang`/
    # `--ast-frontend`/`--compiler`/`--compiler-prefix`/`--compiler-option`/
    # `--sysroot`/`--nostdinc` are all gone from `compare`'s CLI (CONFIG
    # class; `.abicheck.yml`'s `compile:` block is their only source now).
    # `lang` is resolved from config below (no `CompileContext` field for
    # it); the rest reach the same `resolve_compile_context` call as
    # before with fixed "nothing explicit" inputs, which already treats an
    # unset/default value as "defer to config" the same way `compile.std`/
    # `compile.defines` already did.
    lang: str | None = None,
    header_backend: str = "auto",
    sysroot: Path | None = None,
    nostdinc: bool = False,
    # --gcc-options removed as a CLI flag (CLI audit PR 5/5); kept as an
    # internal-only, defaulted-None parameter -- see cli.py's dump_cmd for
    # why (never populated from the CLI anymore, only ever None here).
    gcc_options: str | None = None,
    compiler_path: str | None = None,
    compiler_prefix: str | None = None,
    compiler_option_tokens: tuple[str, ...] = (),
    # ADR-074's -D/--define: one both-sides macro set (never per-side -- two
    # sides parsed under different macro contexts are two different public
    # surfaces), folded by macro name against .abicheck.yml compile.defines
    # inside the shared resolve_compile_context below.
    defines: tuple[str, ...] = (),
    old_header_backend: str | None = None,
    new_header_backend: str | None = None,
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...],
    new_includes_only: tuple[Path, ...],
    old_version: str,
    new_version: str,
    fmt: str,
    output: Path | None,
    suppress: Path | None,
    acknowledgments: Path | None = None,
    policy: str,
    policy_file_path: Path | None,
    severity_preset: str | None,
    config: Path | None,
    follow_deps: bool,
    search_paths: tuple[Path, ...],
    ld_library_path: str,
    include_dependencies: bool,
    show_only: str | None,
    report_mode: str,
    debug_roots: tuple[Path, ...],
    debug_roots_old: tuple[Path, ...],
    debug_roots_new: tuple[Path, ...],
    # ADR-068 D4/Phase 5: --pattern-verdicts/--surface-metrics are gone --
    # both run unconditionally now (compare_snapshots() passes True for
    # both below). Plan slice 7o did the same to the three *disclosure*
    # tokens that used to gate rendering the ledgers those analyses produce
    # (`--view patterns`/`filtered`/`suppressions`), so no parameter
    # corresponds to them here any more either.
    verbose: bool,
    use_cases_manifest: Path | None = None,
    old_build_info: Path | None = None,
    new_build_info: Path | None = None,
    old_sources: Path | None = None,
    new_sources: Path | None = None,
    depth: str | None = None,
    probe_matrix_old: Path | None = None,
    probe_matrix_new: Path | None = None,
    # ADR-068 D4/Phase 5: -o is repeatable (§4.1). Each entry is an
    # already-parsed, already PATH-uniqueness-checked (FORMAT, PATH) pair.
    secondary_writes: tuple[tuple[str, Path], ...] = (),
    dry_run: bool = False,
    used_by_apps: tuple[ConsumerAppInput, ...] = (),
    used_by_manifests: tuple[Path, ...] = (),
    required_symbols_opt: tuple[str, ...] = (),
    diagnostic_comparison: bool = False,
    contract_mode: str | None = None,
    pack_paths: tuple[Path, ...] = (),
    include_labels: dict[Path, str] | None = None,
    old_dump_manifest: Path | None = None,
    new_dump_manifest: Path | None = None,
    frontend_context: str = "host",
    since: str | None = None,  # ADR-068 Phase 2c: changed-path localization
    changed_paths_opt: tuple[str, ...] = (),
    abi3: str | None = None,  # ADR-068 Phase 2d: candidate-side abi3 audit
    budget: str | None = None,
) -> None:
    """Run the single-pair (or set fan-out) ``compare`` flow and exit accordingly."""
    from .dry_run import reject_dry_run_with_output
    from .frontends.cli.operand_diagnostics import _warn_unused_set_flags

    reject_dry_run_with_output(dry_run, output)
    budget_s = _parse_budget(budget)
    _enter_budget_scope(ctx, budget_s)  # before inline embedding too; see its docstring
    # --contract is the only way to ask for the ADR-049 evaluator on the CLI
    # (abicheck.cli_options.resolve_contract_evaluation) -- resolved here,
    # before contract_evaluation is used for anything else in this function,
    # so every downstream use (the typed CompareRequest included) sees the
    # already-resolved value and behaves exactly as if the caller had passed
    # contract_evaluation=True explicitly.
    contract_evaluation = resolve_contract_evaluation(contract_mode)
    contract_mode = resolve_contract_domain(contract_mode, ctx)
    _setup_verbosity(verbose)

    # G31 Phase C follow-up (AGENTS.md "dump --lang c++ is silently
    # discarded ..." known gap): --lang used to carry the same Click default
    # ("c++", indistinguishable from a genuine --lang c++) that dump_cmd's
    # own lang_explicit detection existed to resolve. Threaded through
    # _resolve_compare_snapshots -> CompareRequest.lang_explicit so a live
    # ELF/PE/Mach-O side's header-AST pass honors an explicit request on a
    # language-ambiguous header instead of silently auto-detecting past it.
    #
    # Phase 7 (one-comparison-product.md §4.1): `--lang` has no CLI spelling
    # left on `compare` at all, so `ctx.get_parameter_source("lang")` can
    # never report COMMANDLINE any more -- `compile.lang` in `.abicheck.yml`
    # is the only remaining source of an explicit request, resolved below
    # once `resolved_cfg` exists (Codex review: computing this before that
    # resolution silently discarded every config-set `compile.lang`).

    # Workstream D-S1: merge --used-by-manifest-named consumers into the same
    # --used-by pipeline before anything else looks at used_by_apps -- every
    # downstream consumer (the mutual-exclusivity check just below, the
    # release-fan-out rejection, the dry-run preview, and the real scoped
    # gate) then sees one combined tuple and needs no manifest-specific
    # branch of its own.
    if used_by_manifests:
        from .model.consumer_spec import parse_consumer_manifest

        manifest_specs: tuple[ConsumerAppInput, ...] = tuple(
            spec
            for manifest_path_ in used_by_manifests
            for spec in parse_consumer_manifest(manifest_path_)
        )
        used_by_apps = (*used_by_apps, *manifest_specs)

    with compare_settings_usage_errors():
        (
            required_symbols,
            required_symbols_from_file,
            required_symbols_sha,
            required_symbols_path,
        ) = load_required_symbols(required_symbols_opt)
    if used_by_apps and required_symbols:
        raise click.UsageError(
            "--used-by and --required-symbol are mutually "
            "exclusive: scope the comparison to either application imports or "
            "an explicit required-symbol contract, not both."
        )
    policy, policy_selected_by, policy_selected_path, policy_selected_sha = (
        resolve_required_symbol_policy(
            policy,
            required_symbols=required_symbols,
            policy_from_commandline=(
                ctx.get_parameter_source("policy")
                == click.core.ParameterSource.COMMANDLINE
            ),
            required_symbols_from_file=required_symbols_from_file,
            required_symbols_path=required_symbols_path,
            required_symbols_sha=required_symbols_sha,
        )
    )
    # ADR-037 D4: load the project config and merge CLI flags over it
    # (precedence CLI > config > built-in default) *before* dispatch, so both the
    # single-file and the directory/package fan-out paths share one resolution.
    cfg_path, project_cfg, resolved_cfg, cfg_sha = resolve_project_compare_config(
        config=config,
        severity_preset=severity_preset,
    )
    # `scope.exclude_headers` -- the config equivalent of `--exclude-header`
    # (see `BuildConfig.exclude_headers` for why it is its own key and not
    # `sources.exclude`). Weaker than the flag and never unioned with it: a
    # run stating `--exclude-header` at all uses exactly the rules it
    # stated, because the rule *set* is the run's scope identity and
    # silently widening it would make the run narrower than the command
    # line says. From here on `exclude_headers` is the run's one canonical
    # rule set, and every path -- scalar, directory/package fan-out,
    # stranded capture, dry-run receipt, configuration digest -- reads this
    # value.
    if not exclude_headers:
        exclude_headers = tuple(getattr(project_cfg, "exclude_headers", ()) or ())
    # One-comparison-product Phase 9b: no CLI flag any more -- scope.public
    # (or its built-in True) is the whole answer for a run with no --contract.
    scope_public_headers = resolved_cfg.scope_public
    collapse_versioned_symbols = resolved_cfg.collapse_versioned_symbols
    strict_suppressions = resolved_cfg.strict_suppressions
    require_justification = resolved_cfg.require_justification
    require_complete_analysis = (
        resolved_cfg.require_complete_analysis
    )  # former CLI flag
    # Was assurance.require_complete literally stated (vs. an omitted key
    # defaulting to the same resolved value)? See
    # contract_gate_require_complete_provenance.py's module docstring.
    require_complete_analysis_stated = (
        getattr(project_cfg, "assurance_require_complete", None) is not None
    )
    # ADR-068 D5 / Phase 7: `--lang` and the debug flags are config-only;
    # `config_run_settings` is the one reading of them, shared with
    # `--no-baseline` so the two `compare` shapes cannot differ. `lang` is
    # always None here (compare_cmd's kwargs carry no "lang" key; the
    # stored-bundle-facts path resolves its own upstream).
    from .frontends.cli.compare_config_settings import config_run_settings

    run_settings = config_run_settings(resolved_cfg)
    debug_format_opt = run_settings.debug_format
    dwarf_only = run_settings.dwarf_only
    debuginfod = run_settings.debuginfod
    debuginfod_url = run_settings.debuginfod_url
    # Phase 7 (§4.1 CONFIG row): --pdb-path is config-only; see compare_pdb_config's docstring.
    from .frontends.cli.compare_pdb_config import resolve_and_reject_shared_pdb_path

    pdb_path = resolve_and_reject_shared_pdb_path(
        run_settings.pdb_path, old_input=old_input, new_input=new_input
    )
    old_pdb_path: Path | None = None
    new_pdb_path: Path | None = None
    show_redundant = resolved_cfg.show_redundant
    if lang is None:
        lang = run_settings.lang
    lang_explicit = run_settings.lang_explicit
    # Phase 7: `--allow-ast-frontend-fallback`/`--allow-unsupported-castxml`
    # are gone from compare's CLI too; a `.abicheck.yml` `compile:` block
    # setting either to `true` has the identical effect the removed flag
    # had (both were already pure env-var togglers).
    from .buildsource.build_config import BuildConfig as _BuildConfig
    from .cli_options import apply_compile_config_env_toggles

    apply_compile_config_env_toggles(
        ctx, project_cfg if isinstance(project_cfg, _BuildConfig) else None
    )

    # P1.1 (Codex review): resolved ahead of the inline-embed block below so a
    # raw --old/new-sources tree's inline `dump` also gets per-side debug roots.
    resolved_old_debug, resolved_new_debug = _resolve_debug_roots(
        debug_roots, debug_roots_old, debug_roots_new
    )

    # ADR-037 D7: input-type dispatch. The resolved config (scope/suppression/
    # severity) is forwarded so a set-input compare classifies the same way a
    # single-pair one would (ADR-037 D4).
    old_kind, new_kind = _classify_and_reject_operands(old_input, new_input)

    # ADR-068 D4/Phase 5: split out to _helpers_compare.reject_use_cases_
    # without_carrying_output (keeps this module under the 2000-line hard
    # cap) -- with -o repeatable, "one output carrying it" is now the
    # primary render OR any secondary write, not just one --write.
    reject_use_cases_without_carrying_output(
        fmt=fmt,
        secondary_fmts=[f for f, _ in secondary_writes],
        use_cases_manifest=use_cases_manifest,
    )

    # CLI cleanup phase two, PR B slice 1: None for a single-pair compare or a
    # release/directory one with no --pack; resolved just below (ahead of the
    # --dry-run emit) otherwise, so a dry run and the real run agree.
    release_pack_application = None
    release_depth: str | None = None
    if {old_kind, new_kind} & {"directory", "package"}:
        release_depth = _reject_flags_unsupported_for_set_inputs(
            ctx,
            used_by_apps=used_by_apps,
            required_symbols=required_symbols,
            diagnostic_comparison=diagnostic_comparison,
            include_labels=include_labels,
            use_cases_manifest=use_cases_manifest,
            suppress=suppress,
            budget=budget,
            pdb_path=pdb_path,
        )
        note_unapplied_post_manifest(
            project_cfg,
            route="a directory/package comparison",
            reason="the per-library fan-out has no contract-overlay channel",
        )
        # Codex review, fresh evidence ("Validate release-only view
        # restrictions before dry-run exit"): --view leaf/root-cause is
        # rejected for a directory/package operand inside
        # _dispatch_release_compare, but that check never ran for
        # --dry-run (emit_dry_run raises SystemExit before dispatch is ever
        # reached) -- so a dry run reported "ok" (exit 0) for exactly the
        # combination the identical non-dry-run invocation rejects (exit
        # 64). Validated here too, ahead of the --dry-run emit below, the
        # same way every other release-only flag conflict in this block
        # already is.
        from .frontends.cli.commands.compare_routing import (
            reject_release_incompatible_view_mode,
        )

        reject_release_incompatible_view_mode(report_mode)
        # Resolved unconditionally, not only under `--pack` (finding 1,
        # round 4): a no-pack run's own project-backed `.abicheck.yml`
        # `policy.overrides` still needs to reach a real config for
        # `record_release_resolved_config` -- see that resolver's docstring.
        from .cli_compare_receipt import resolve_release_pack_application_from_ctx

        release_pack_application = resolve_release_pack_application_from_ctx(
            ctx,
            contract_mode=contract_mode,
            policy=policy,
            policy_file_path=policy_file_path,
            suppress=suppress,
            require_justification=require_justification,
            severity_preset=severity_preset,
            pack_paths=pack_paths,
            contract_evaluation=contract_evaluation,
            project_cfg=project_cfg,
            project_path=cfg_path,
            project_sha256=cfg_sha,
            policy_option=policy_selected_by,
            policy_path=policy_selected_path,
            policy_sha256=policy_selected_sha,
            headers=(*headers, *old_headers_only, *new_headers_only),
        )

    # Parsed here, in the preflight, not only at the post-comparison
    # attribution call: --dry-run returns before that call, so a malformed
    # manifest passed a dry run as "validated" (exit 0) while the identical
    # real invocation rejected it (exit 64) -- the one thing --dry-run
    # promises not to do (Codex review). The parse is pure I/O + validation,
    # the same cheap read-only resolution the dry run already performs for
    # every other input; the result is discarded because attribution needs
    # both snapshots' graphs, which a dry run deliberately never builds.
    #
    # After the flag-combination rejections above, for the reason the
    # --dump-manifest parse below states for itself: when --use-cases was
    # never going to work here at all, "not supported here" is the useful
    # message, not "your manifest is malformed".
    #
    # The *other* --use-cases exit a dry run still cannot predict is "neither
    # side carries a source graph", and deliberately so: that is a property
    # of the resolved operands, not of the command line. It is knowable for a
    # snapshot operand and unknowable for a live binary, whose graph only
    # exists after a dump the dry run must not perform -- so checking it
    # would make the dry run's answer depend on which operand shape it was
    # given. Manifest validity has no such asymmetry.
    if use_cases_manifest is not None:
        try:
            read_use_case_manifest(use_cases_manifest)
        except UseCaseAttributionError as exc:
            raise click.UsageError(str(exc)) from exc

    # Parsed after the directory/package rejection above (not before, like an
    # earlier revision of this function did): a malformed --dump-manifest on
    # a directory/package compare must fail with that block's clear "not
    # supported for directory/package" message, not a confusing "invalid
    # YAML" one for a flag combination that was never going to work anyway
    # (Codex review).
    old_manifest_obj, new_manifest_obj = _preflight_manifests_and_audit(
        old_dump_manifest=old_dump_manifest,
        new_dump_manifest=new_dump_manifest,
        pack_paths=pack_paths,
        policy_file_path=policy_file_path,
        contract_evaluation=contract_evaluation,
    )
    from .cli_compare_receipt import dry_run_scheme_label

    # Round 5 finding 1: validate policy.overrides before --dry-run exits.
    from .pack_application import preflight_validate_project_policy_overrides

    preflight_validate_project_policy_overrides(project_cfg, cfg_path)
    # One warning per run for a rule that matched nothing -- asked here,
    # above the scalar-versus-release branch, so it is stated exactly once
    # whatever the operand is (see the owner's docstring for why once).
    _exclusion_warning = unmatched_exclusion_warning(
        [*headers, *old_headers_only, *new_headers_only], exclude_headers
    )
    if _exclusion_warning is not None:
        click.echo(_exclusion_warning, err=True)
    # What the ONE shared L2 compile-context resolution resolves from (ADR-037
    # D3; ADR-074's by-macro-name defines fold), bound once so the --dry-run
    # receipt and the real resolution below cannot drift. `cfg_path` is `config`
    # or the cwd-upward auto-discovered .abicheck.yml (PR #1154, as above).
    _compile_context_kwargs: dict[str, Any] = dict(
        ctx=ctx,
        gcc_options=gcc_options,
        sysroot=sysroot,
        nostdinc=nostdinc,
        header_backend=header_backend,
        includes=includes,
        build_config=cfg_path,
        frontend_context=frontend_context,
        compiler_path=compiler_path,
        compiler_prefix=compiler_prefix,
        compiler_option_tokens=compiler_option_tokens,
        defines=defines,
        config_explicit=(config is not None),
    )
    if dry_run:
        from .dry_run import emit_dry_run
        from .frontends.cli.compare_dry_run import (
            build_compare_dry_run_result,
            effective_compare_defines,
        )

        # ADR-043 D4's dry-run report must reflect the *effective* depth, not
        # just echo `--depth` back; `frontends/cli/compare_dry_run.py`'s
        # docstring explains why it takes the resolved pair as parameters.
        # The parsed manifests say which operands parse headers without -H.
        with compare_settings_usage_errors():
            collect_mode_dr, effective_depth_label_dr = resolve_compare_collect_mode(
                depth,
                resolved_cfg.source_method,
                old_sources,
                new_sources,
                old_build_info,
                new_build_info,
            )
        emit_dry_run(
            build_compare_dry_run_result(
                # ADR-074: the effective macro set, resolved through the very
                # same call the real run makes below (see that helper).
                defines=effective_compare_defines(
                    resolve_compile_context, **_compile_context_kwargs
                ),
                compile_context=resolve_compile_context(**_compile_context_kwargs)[0],
                lang=lang,
                old_dump_manifest=old_manifest_obj,
                new_dump_manifest=new_manifest_obj,
                old_input=old_input,
                new_input=new_input,
                old_kind=old_kind,
                new_kind=new_kind,
                depth=depth,
                collect_mode=collect_mode_dr,
                effective_depth_label=effective_depth_label_dr,
                source_method=resolved_cfg.source_method,
                headers=headers,
                includes=includes,
                old_headers_only=old_headers_only,
                new_headers_only=new_headers_only,
                old_sources=old_sources,
                new_sources=new_sources,
                old_build_info=old_build_info,
                new_build_info=new_build_info,
                cfg_path=cfg_path,
                fmt=fmt,
                exit_code_scheme=dry_run_scheme_label(resolved_cfg, pack_paths),
                header_backend=header_backend,
                used_by_apps=used_by_apps,
                required_symbols=required_symbols,
                select=select,
                select_required=select_required,
                exclude_headers=tuple(exclude_headers or ()),
                since=since,
                changed_paths_opt=changed_paths_opt,
                config=config,
            )
        )

    if {old_kind, new_kind} & {"directory", "package"}:
        # Both-sides L2 compile context for the release fan-out -- see
        # resolve_directory_compile_context's own docstring.
        directory_compile_context, directory_includes = (
            # The identical resolution the single-pair path performs, from the
            # identical inputs (see the kwargs dict bound above the --dry-run
            # branch) -- including `config_explicit`, without which an
            # auto-discovered `compile.compiler` would bypass the untrusted-
            # executable-selection gate (Codex review, PR #1154), and ADR-074's
            # `defines`, without which a release fan-out would silently drop
            # every -D the single-pair path honors.
            resolve_directory_compile_context(**_compile_context_kwargs)
        )
        # Dirs the config appended past the CLI -I roots (mirrors the single-pair
        # `config_includes` split below): must survive a per-library-pair
        # `--old/new-include` override, which otherwise replaces `includes`.
        directory_config_includes = tuple(directory_includes[len(includes) :])
        # Off the owner, never via ``abicheck.cli`` (install_facade_guard);
        # ADR-068 §3 #23: also thread policy.overrides to the release fan-out.
        from .frontends.cli.commands.compare_routing import _dispatch_release_compare
        from .pack_application import resolve_release_project_policy_overrides

        # ADR-075: every member dump and the release surface are classified
        # under the one project config (a run-scoped rule, like crosschecks).
        with project_ownership_scope(project_ownership_request(project_cfg, cfg_path)):
            _dispatch_release_compare(
                ctx,
                old_dir=old_input,
                new_dir=new_input,
                project_policy_overrides=resolve_release_project_policy_overrides(
                    project_cfg, cfg_path
                ),
                **release_acknowledgment_inputs(
                    project_cfg,
                    cfg_path,
                    config_explicit=config is not None,
                    override_path=acknowledgments,
                ),
                headers=headers,
                includes=directory_includes,
                old_headers_only=old_headers_only,
                new_headers_only=new_headers_only,
                old_includes_only=old_includes_only,
                new_includes_only=new_includes_only,
                old_version=old_version,
                new_version=new_version,
                lang=lang,
                lang_explicit=lang_explicit,
                fmt=fmt,
                output=output,
                output_dir=output_dir,
                suppress=suppress,
                strict_suppressions=strict_suppressions,
                require_justification=require_justification,
                policy=policy,
                policy_file_path=policy_file_path,
                dso_only=resolved_cfg.release_dso_only,  # Phase 7d: config-only, no CLI kwarg
                fail_on_removed=resolved_cfg.fail_on_removed_library,
                on_incomplete_scope=resolved_cfg.on_incomplete_scope,
                # ADR-071: the release fan-out honors assurance.require_complete
                # now (it folds every compared member's own assurance with max),
                # so the resolved value is forwarded instead of being rejected.
                require_complete_analysis=require_complete_analysis,
                support_promise=resolved_cfg.release_support_promise,  # Phase 7: config-only, no CLI kwarg
                select=select,
                select_required=select_required,
                debug_info1=debug_info1,
                debug_info2=debug_info2,
                devel_pkg1=devel_pkg1,
                devel_pkg2=devel_pkg2,
                include_private_dso=resolved_cfg.release_include_private_dso,
                manifest_path=manifest_path,
                bundle_system_providers=resolved_cfg.bundle_system_providers,
                bundle_cohorts=resolved_cfg.bundle_cohorts,
                bundle_facts_out=bundle_facts_out,
                scope_public_headers=scope_public_headers,
                include_dependencies=include_dependencies,
                severity_preset=resolved_cfg.merged_severity_preset,
                severity_abi_breaking=resolved_cfg.merged_severity_abi_breaking,
                severity_potential_breaking=resolved_cfg.merged_severity_potential_breaking,
                severity_quality_issues=resolved_cfg.merged_severity_quality_issues,
                severity_addition=resolved_cfg.merged_severity_addition,
                probe_matrix_old=probe_matrix_old,
                probe_matrix_new=probe_matrix_new,
                verbose=verbose,
                contract_evaluation=contract_evaluation,
                contract_mode=contract_mode,
                pack_application=release_pack_application,
                secondary_writes=secondary_writes,
                compile_context=directory_compile_context,
                config_includes=directory_config_includes,
                depth=release_depth,
                public_header_dirs=project_config_public_header_dirs(project_cfg),
                collapse_versioned_symbols=collapse_versioned_symbols,
                env_matrix=resolved_cfg.deployment,  # ADR-020b: config-only, no CLI kwarg
                # Codex review (PR #1154 follow-up): --view's derived values were
                # silently dropped from this dispatch -- forwarded raw
                # (unnormalized against `fmt`/`report_mode`'s "impact" sugar);
                # _dispatch_release_compare resolves and validates them.
                report_mode=report_mode,
                show_only=show_only,
                # The canonical exclusion rules -- the same tuple the scalar
                # path below hands `_resolve_compare_snapshots`. Forwarded
                # rather than dropped: a directory operand accepted
                # `--exclude-header` and silently ignored it, so a release tree
                # that cannot be parsed whole (Intel MKL's FFTW2/FFTW3 typedef
                # clash) failed every member under the exact arguments that made
                # the single-file comparison succeed.
                exclude_headers=tuple(exclude_headers or ()),
            )
        return
    # Single-file/snapshot inputs: the set-only fan-out flags do not apply.
    _reject_bundle_facts_out_for_single_pair(bundle_facts_out)
    _warn_unused_set_flags(
        dso_only=resolved_cfg.release_dso_only,
        output_dir=output_dir,
        select=select,
        select_required=select_required,
    )

    (
        collect_mode,
        headers,
        old_headers_only,
        new_headers_only,
        effective_debug_format,
        demangle,
        report_mode,
        show_impact,
    ) = _normalize_compare_options(
        resolved_cfg,
        depth=depth,
        headers=headers,
        old_headers_only=old_headers_only,
        new_headers_only=new_headers_only,
        debug_format_opt=debug_format_opt,
        fmt=fmt,
        report_mode=report_mode,
        old_sources=old_sources,
        new_sources=new_sources,
        old_build_info=old_build_info,
        new_build_info=new_build_info,
    )
    # ADR-068 Phase 2c/2d (plan §3 #12/#15): the changed-path seed (scoping input
    # only, narrowing the L4/L5 points of interest per ADR-043 D7) + abi3 floor.
    _enrich = _enrichment.resolve_compare_enrichment_inputs(
        since=since,
        changed_paths_opt=changed_paths_opt,
        abi3=abi3,
        project_cfg=project_cfg,
        sources=new_sources or old_sources,
    )
    collect_mode = _enrich.localize_collect_mode(collect_mode)

    # Applies to both sides; a per-side --ast-frontend old=/new= still wins.
    import dataclasses

    compile_context, merged_includes = resolve_compile_context(
        **_compile_context_kwargs
    )
    # The dirs the config appended past the CLI -I roots. These are documented as
    # applying to *both* sides, so they must survive a per-side --old/new-include
    # override (which replaces the both-sides -I for that side). Keep them separate
    # and re-append after per-side resolution rather than folding into the shared
    # tuple, else the overridden side would lose them (Codex review).
    config_includes = tuple(merged_includes[len(includes) :])
    # The merged frontend flows to both sides through the explicit header_backend
    # (so --ast-frontend old=/new= can still override per side); neutralize the
    # frontend on the threaded context so run_dump's `compile.frontend` does NOT
    # outrank that per-side header_backend (it only carries the --gcc-*/--sysroot/
    # --nostdinc knobs for both sides).
    header_backend, side_compile_context = split_compile_context_frontend(
        compile_context
    )

    old_h, new_h, old_inc, new_inc = resolve_per_side_inputs(
        headers,
        includes,
        old_headers_only,
        new_headers_only,
        old_includes_only,
        new_includes_only,
    )
    with _operand_rule_errors():
        reject_manifest_header_conflicts(
            old_manifest_obj, new_manifest_obj, old_h, new_h
        )
    if config_includes:
        old_inc = list(old_inc) + list(config_includes)
        new_inc = list(new_inc) + list(config_includes)

    # Pair-wide C++20 dialect resolution (P0 fix) — see
    # workflows.compare_configuration._pair_wide_dialect_override's docstring. Applied to
    # both `compile_context` (used by the inline-source-embed path below) and
    # `side_compile_context` (used by `_resolve_compare_snapshots`).
    compile_context, side_compile_context = _pair_wide_dialect_override(
        lang, old_h, new_h, compile_context, side_compile_context
    )

    from . import deadline

    # Follow GNU ld linker scripts up front so the resolved DSO (not the text
    # script) drives format detection, metadata, and dependency analysis.
    # ``cli_resolve`` owns this name; patch it there. (The comment here used to
    # claim the call went through ``abicheck.cli`` for monkeypatch reasons -- it
    # did not, and that alias table no longer exists.)
    old_input, old_fmt = cli_resolve._normalize_binary_input(old_input)
    new_input, new_fmt = cli_resolve._normalize_binary_input(new_input)
    with _operand_rule_errors():
        reject_manifest_non_elf(old_manifest_obj, new_manifest_obj, old_fmt, new_fmt)
        reject_debug_format_for_non_elf(effective_debug_format, old_fmt, new_fmt)
    _warn_ignored_flags(
        old_fmt is not None,
        new_fmt is not None,
        headers,
        includes,
        old_headers_only,
        new_headers_only,
        old_includes_only,
        new_includes_only,
    )

    _log_debug_resolution(
        old_input,
        new_input,
        resolved_old_debug,
        resolved_new_debug,
        debuginfod=debuginfod,
        debuginfod_url=debuginfod_url,
    )

    # A raw --old/new-sources checkout or --build-info build dir (not a pack)
    # is collected by the shared resolution itself, at the run's collect
    # mode, under the config/compile/execution decisions `dump` would make for
    # it (`frontends.cli.compare_inline_evidence`); prebuilt packs it does not
    # consume stay on the out-of-band path below.
    from .frontends.cli.compare_inline_evidence import (
        InlineEvidence,
        InlineEvidenceRun,
    )
    from .frontends.cli.dump_debug_config import DumpDebugConfig

    _inline = InlineEvidence(old_sources, old_build_info, new_sources, new_build_info)
    _inline_run = InlineEvidenceRun(
        ctx=ctx,
        compile_context=compile_context,
        old_header_backend=old_header_backend,
        new_header_backend=new_header_backend,
        collect_mode=collect_mode,
        depth=depth,
        changed_paths=_enrich.changed_paths,
        config=config,
        lang=lang,
        debug=DumpDebugConfig(
            format=effective_debug_format,
            dwarf_only=dwarf_only,
            debuginfod=debuginfod,
            debuginfod_url=debuginfod_url,
            pdb_path=pdb_path,
        ),
        old_debug_roots=tuple(resolved_old_debug),
        new_debug_roots=tuple(resolved_new_debug),
    )
    # ADR-068 §3 #19: `--budget`'s deadline is already ambient (entered once,
    # early, by `enter_budget_scope` above) -- `deadline.check()`/
    # `bounded_timeout()` consult it however deep the call stack; the explicit
    # `deadline.check()` below closes the gap for a stored-snapshot-only pair,
    # which touches neither (Codex review, PR #1178). Only a fresh try/except
    # is needed, not a second `deadline_scope` entry (which would reset it).
    try:
        deadline.check()
        resolution_request, pair = _resolve_compare_pair(
            old_input,
            new_input,
            old_fmt,
            new_fmt,
            old_h,
            new_h,
            old_inc,
            new_inc,
            old_version,
            new_version,
            lang,
            pdb_path,
            old_pdb_path,
            new_pdb_path,
            dwarf_only,
            effective_debug_format,
            follow_deps,
            search_paths,
            ld_library_path,
            header_backend=header_backend,
            old_header_backend=old_header_backend,
            new_header_backend=new_header_backend,
            compile_context=side_compile_context,
            old_debug_roots=resolved_old_debug or None,
            new_debug_roots=resolved_new_debug or None,
            enable_debuginfod=debuginfod,
            debuginfod_url=debuginfod_url,
            include_labels=include_labels,
            old_dump_manifest=old_manifest_obj,
            new_dump_manifest=new_manifest_obj,
            include_dependencies=include_dependencies,
            lang_explicit=lang_explicit,
            changed_paths=_enrich.changed_paths,
            config_public_header_dirs=project_config_public_header_dirs(project_cfg),
            exclude_headers=tuple(exclude_headers or ()),
            ownership=project_ownership_request(project_cfg, cfg_path),
            prepare=lambda request: _inline.apply(request, _inline_run),
        )
    except deadline.DeadlineExceeded as exc:
        _exit_on_budget_overflow(
            exc,
            budget,
            f"'{old_input}'/'{new_input}'",
            old_input.stem,
            str(old_input),
            str(new_input),
            fmt=fmt,
            output=output,
            secondary_writes=secondary_writes,
        )

    suppression, pf = _load_suppression_and_policy(
        suppress,
        policy,
        policy_file_path,
        strict_suppressions=strict_suppressions,
        require_justification=require_justification,
    )
    # One read for both consumers -- the live overlay and the ADR-049 receipt
    # that names this file with its digest (see the helper for why).
    force_public, symbols_list = resolve_force_public_scope(
        resolved_cfg.public_symbols, None
    )
    _warn_force_public_ignored(force_public, scope_public_headers)

    evaluation_config, pf, resolved_cfg = _resolve_evaluation_config(
        ctx,
        # Only a side this run extracts is classified under -H; a stored
        # snapshot side ignores it (`_warn_ignored_flags`).
        headers=(
            *(headers if old_fmt or new_fmt else ()),
            *(old_headers_only if old_fmt else ()),
            *(new_headers_only if new_fmt else ()),
        ),
        resolved_cfg=resolved_cfg,
        project_cfg=project_cfg,
        cfg_path=cfg_path,
        cfg_sha=cfg_sha,
        config_explicit=config is not None,
        policy=policy,
        policy_file_path=policy_file_path,
        policy_file=pf,
        suppression=suppression,
        suppress=suppress,
        symbols_list=symbols_list,
        contract_mode=contract_mode,
        contract_evaluation=contract_evaluation,
        require_justification=require_justification,
        severity_preset=severity_preset,
        pack_paths=pack_paths,
        policy_selected_by=policy_selected_by,
        policy_selected_path=policy_selected_path,
        policy_selected_sha=policy_selected_sha,
    )
    # ADR-068 §3 #23 / ADR-049 D7: `.abicheck.yml`'s `policy.overrides` at
    # PROJECT_CONFIG tier, after the pack fold above (the typed API's order).
    pf = fold_project_policy_overrides_for_cli(pf, project_cfg, cfg_path, policy)

    # An out-of-band --old/new-build-info/--sources pack overrides the
    # snapshot's embedded payload per layer (`resolve_side_pack`). Attached
    # here, before classification, so the shared depth projection caps it
    # exactly as it caps an embedded pack -- the typed route's order, where
    # resolution embeds the pack (Codex review, PR #1020).
    from .cli_buildsource_helpers import _with_out_of_band_packs

    _packs = _inline.out_of_band(resolution_request)
    pair = _with_out_of_band_packs(
        pair,
        _packs.old_build_info,
        _packs.old_sources,
        _packs.new_build_info,
        _packs.new_sources,
    )
    # contract.overlays.post_manifest: scope the comparison to the POST
    # manifest's committed `pp_*`/ufunc-loop surface. Narrowing, so only an
    # explicitly named --config is trusted to apply it.
    post_manifest_allowlist = post_manifest_allowlist_for(
        project_cfg, cfg_path, pair.old, pair.new, config_explicit=config is not None
    )
    # ADR-067 D5/D6: `.abicheck.yml`'s `acknowledgment:` block (records only
    # from an explicit --config; see acknowledgment_config.py).
    acks = compare_acknowledgments_for(
        project_cfg,
        cfg_path,
        pf,
        config_explicit=config is not None,
        override_path=acknowledgments,
    )

    # ADR-068 D4 (the correctness fix this phase exists for): pattern-verdict
    # modulation is unconditional now, not a flag (removed; see
    # adr027_compare_options). It is independently evidence-gated
    # (idiom-only demotion), so this never manufactures a false negative --
    # it only fixes the bug where asking *why* (--view patterns) used to
    # also decide *whether* modulation happened, which could change the
    # verdict and exit code.
    # ADR-068 D4/Phase 5: --surface-metrics computation is unconditional
    # too now (§4.1's AUTO classification) -- always True regardless of
    # what the (now vestigial, accepted-for-compatibility) flag says, the
    # same "the flag is a no-op, the analysis always runs" treatment
    # pattern_verdicts=True above already gets.
    # The run's one resolved gate (duplication-and-convergence P0
    # `EffectiveGate`), read after any gate pack moved a severity level. Its
    # severity is `None` exactly under the legacy scheme, so every consumer
    # below -- report rendering, the scoped gate -- reads one value instead of
    # re-pairing a severity map with a separately-derived scheme.
    from .workflows.gate import effective_gate_for_resolved_compare_config

    report_severity = effective_gate_for_resolved_compare_config(resolved_cfg).severity
    # One Semantic Pipeline plan, 4B: the evaluation config's resolved
    # contract.mode, unless only the built-in default chose it (ADR-049
    # Phase 7: that default depends on evidence the comparison decides).
    from .workflows.compare_policy import stated_contract_mode

    resolved_contract_mode = stated_contract_mode(
        evaluation_config, contract_mode, contract_evaluation=contract_evaluation
    )
    # ADR-020b / ADR-068 D5: `deployment:` (former `--env-matrix FILE`) is
    # config-only -- `resolved_cfg.deployment` is the resolved matrix.
    request = dataclasses.replace(
        resolution_request,
        policy=policy,
        policy_file_path=policy_file_path,
        suppress=suppress,
        strict_suppressions=strict_suppressions,
        require_justification=require_justification,
        scope_public=scope_public_headers,
        force_public_symbols=frozenset(force_public) if force_public else None,
        public_surface_allowlist=(
            frozenset(post_manifest_allowlist)
            if post_manifest_allowlist is not None
            else None
        ),
        pattern_verdicts=True,
        collapse_versioned_symbols=collapse_versioned_symbols,
        env_matrix=resolved_cfg.deployment,
        diagnostic_comparison=diagnostic_comparison,
        contract_evaluation=contract_evaluation,
        contract_mode=resolved_contract_mode,
        depth=depth,
        old_probe_matrix=probe_matrix_old,
        new_probe_matrix=probe_matrix_new,
        abi3_floor=_enrich.abi3_floor,
    )
    from .cli_buildsource_helpers import _echo as _evidence_echo
    from .workflows.compare_classify import classify_resolved_pair
    from .workflows.compare_request_policy import RequestPolicyInputs

    try:
        classified = classify_resolved_pair(
            request,
            pair,
            RequestPolicyInputs(suppression, pf, evaluation_config, acks),
            on_output=_evidence_echo,
        )
    except ValidationError as exc:  # a one-sided --build-info probe matrix
        raise click.UsageError(str(exc)) from exc
    except SnapshotError as exc:  # an unloadable build-source pack: exit 1
        raise click.ClickException(str(exc)) from exc
    except (ProfileMismatchError, ScopeMismatchError) as exc:
        report_not_comparable_and_exit(
            exc,
            pair.old,
            pair.new,
            fmt=fmt,
            output=output,
            secondary_writes=secondary_writes,
        )
    except deadline.DeadlineExceeded as exc:
        # ADR-068 §3 #19: the budget expired during classification (the
        # automatic cross-source/pattern/preprocessor scans can still run
        # `clang -E`) rather than during resolution -- same axis, same exit.
        _exit_on_budget_overflow(
            exc,
            budget,
            f"'{pair.old.library}'",
            pair.old.library,
            pair.old.version,
            pair.new.version,
            fmt=fmt,
            output=output,
            secondary_writes=secondary_writes,
        )
    result, old, new = classified.result, classified.old, classified.new
    _enrichment.echo_abi3_evidence_contract_error(classified.abi3_failure)
    _report_compare_result(
        ctx,
        result,
        old,
        new,
        old_input=old_input,
        new_input=new_input,
        resolved_cfg=resolved_cfg,
        evaluation_config=evaluation_config,
        report_severity=report_severity,
        show_redundant=show_redundant,
        contract_evaluation=contract_evaluation,
        policy=policy,
        pf=pf,
        used_by_apps=used_by_apps,
        required_symbols=required_symbols,
        used_by_old_input=old_input,
        used_by_new_input=new_input,
        suppression=suppression,
        fmt=fmt,
        output=output,
        show_only=show_only,
        report_mode=report_mode,
        show_impact=show_impact,
        follow_deps=follow_deps,
        secondary_writes=secondary_writes,
        require_complete_analysis=require_complete_analysis,
        require_complete_analysis_stated=require_complete_analysis_stated,
        use_cases_manifest=use_cases_manifest,
        project_config_path=cfg_path,
        project_config_sha256=cfg_sha,
    )
