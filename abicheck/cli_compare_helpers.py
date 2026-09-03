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

The click-decorated ``compare`` wrapper in :mod:`abicheck.cli` parses options and
delegates to :func:`run_compare` here, keeping cli.py under the AI-readiness
file-size cap. This is *not* the leaf helper module ``cli_helpers_compare`` (plain,
cli-independent utilities): ``run_compare`` drives the full single-pair compare
flow and reuses the option-parsing/render/exit helpers that still live in
:mod:`abicheck.cli` (imported back below — the by-design sibling cycle, allow-listed
in ``check_ai_readiness``). Verdict routing stays through the Tier-2 service
(``service.compare_snapshots``), never a direct ``checker.compare`` call
(cli-contract, ADR-037 D10.1).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from . import cli_resolve
from .cli_audit import echo_pattern_modulations
from .cli_compare_fold import (
    _fold_scoped_compat_into_text as _fold_scoped_compat_into_text,
    _fold_suppression_audit_into_text as _fold_suppression_audit_into_text,
    _fold_use_case_impact_into_text,
    format_carries_use_case_impact,
)
from .cli_compare_options import (
    _cli_flag,
    _merge_cli_debug_format,
    _NormalizedCompareOptions,
    _param_from_cli,
    _reject_bundle_facts_out_for_single_pair,
    _reject_debug_format_for_non_elf,
    _reject_set_input_flags,
    _resolve_debug_roots,
    _resolve_demangle,
    _warn_force_public_ignored,
    echo_coverage_warnings,
)
from .cli_dump_helpers import resolve_dump_depth
from .cli_helpers_compare import (
    # The ADR-043 scoped-gating family lives there (this module is at the
    # file-size cap); re-exported so ``cli_compare_helpers._verdict_exit_code``
    # -- which cli_scan_baseline imports -- and the existing test patch targets
    # keep resolving unchanged.
    _app_compat_summary as _app_compat_summary,
    _apply_required_symbol_scoping as _apply_required_symbol_scoping,
    _apply_used_by_scoping as _apply_used_by_scoping,
    _pair_wide_dialect_override,
    _plugin_contract_summary as _plugin_contract_summary,
    _require_used_by_binary_evidence as _require_used_by_binary_evidence,
    _resolve_per_side_options,
    _scoped_exit_code as _scoped_exit_code,
    _scoped_severity_summary as _scoped_severity_summary,
    _verdict_exit_code as _verdict_exit_code,
    _verdict_severity_rank as _verdict_severity_rank,
    _warn_ignored_flags,
    fold_l0_hard_removals,
    load_required_symbols,
    resolve_force_public_scope,
)
from .cli_options import (
    _shared_frontend_explicit,
    resolve_compile_context,
    resolve_contract_domain,
    resolve_contract_evaluation,
)
from .cli_resolve import (
    _reject_compile_context_for_set_inputs,
    _reject_evidence_flags_for_set_inputs,
    _resolve_compare_snapshots,
    classify_compare_operand,
    resolve_directory_compile_context,
)
from .contract_scoped_promotion import stamp_scoped_result_findings
from .errors import AbicheckError, ProfileMismatchError, ScopeMismatchError
from .frontends.cli.options import reject_incoherent_secondary_output
from .frontends.cli.options.params import _load_suppression_and_policy
from .frontends.cli.runtime import (
    _EXIT_NOT_COMPARABLE,
    _announce_exit_scheme,
    _exit_with_severity_or_verdict,
    _finalize_compare_result,
    _load_probe_matrix_changes,
    _log_debug_resolution,
    _render_output,
    _setup_verbosity,
    _write_or_echo,
)
from .service_render import ONELINE_FORMAT
from .workflows.gate import announce_coverage_floor, fold_coverage_exit

if TYPE_CHECKING:
    from .cli_helpers_compare import ResolvedCompareConfig
    from .model import AbiSnapshot
    from .workflows.extraction import DumpManifest
    from .workflows.policy_file import PolicyFile


def _resolve_compare_config(
    *,
    config: Path | None,
    severity_preset: str | None,
    scope_public_headers: bool,
    exit_code_scheme: str | None,
    debug_format_opt: str | None,
    debug_format: str | None,
    dwarf_only: bool,
    debuginfod: bool,
    debuginfod_url: str | None,
) -> tuple[Path | None, object, ResolvedCompareConfig, str | None]:
    """Load the project config and merge CLI flags over it (CLI > config > default).

    ADR-037 D4: resolved *before* dispatch so both the single-file and the
    directory/package fan-out paths share one resolution. Auto-discovered from the
    current directory upward, overridable with ``--config``.

    The fourth element is the digest of the bytes the config was parsed from
    (``None`` when there is no config), captured by the same read so an
    ADR-049 receipt can prove *which revision* of the file supplied a value
    rather than only naming its path (Codex review, fresh evidence).
    """
    from .cli_helpers_compare import discover_project_config, resolve_compare_config
    from .workflows.extraction import load_build_config_with_digest

    cfg_path = config if config is not None else discover_project_config()
    cfg_sha: str | None = None
    try:
        project_cfg = None
        if cfg_path is not None:
            project_cfg, cfg_sha = load_build_config_with_digest(cfg_path)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    resolved_cfg = resolve_compare_config(
        project_cfg,
        cli_severity_preset=severity_preset,
        cli_scope_public=_cli_flag("scope_public_headers", scope_public_headers),
        cli_exit_code_scheme=exit_code_scheme,
        # ADR-040 Lever 2: debug-resolution demoted to config.
        # ``--debug-format``/``--debuginfod-url`` default to None (absent ⇒
        # config wins); the is_flags need the COMMANDLINE-source gate so their
        # default ``False`` doesn't mask a configured ``True``. A typed legacy
        # --btf/--ctf/--dwarf must also beat config, so fold it into the CLI value.
        cli_debug_format=_merge_cli_debug_format(
            debug_format_opt, debug_format,
            legacy_from_cli=_param_from_cli("debug_format"),
        ),
        cli_dwarf_only=_cli_flag("dwarf_only", dwarf_only),
        cli_debuginfod=_cli_flag("debuginfod", debuginfod),
        cli_debuginfod_url=debuginfod_url,
    )
    return cfg_path, project_cfg, resolved_cfg, cfg_sha


def _resolve_compare_collect_mode(
    depth: str | None,
    source_method: str | None,
    old_sources: Path | None,
    new_sources: Path | None,
    old_build_info: Path | None,
    new_build_info: Path | None,
) -> tuple[str, str]:
    """Resolve compare's source/build collect mode, plus a human label for it.

    Precedence (ADR-037 D4/D5, extended by the P1 CLI-contract fix below):
    explicit ``--depth`` > ``.abicheck.yml`` ``source.method`` > inferred from
    raw ``--old/new-sources``/``--old/new-build-info`` given with neither of
    the above > off.

    The inferred rung closes a gap where passing ``--sources``/``--build-info``
    with no ``--depth`` (and no ``source.method`` in config) silently resolved
    to "off" and the inputs were ignored with a warning: an explicit
    source/build-info input is itself a request to use it, so omitted depth
    should not default to discarding it. This mirrors ``scan``'s own
    "auto" depth, which is likewise input-driven rather than a fixed default.
    The label is shown verbatim in ``compare --dry-run``'s "Resolved depth and
    source scope" section so a dry run reports the *effective* depth, not just
    the raw ``--depth`` string the user passed (or omitted).
    """
    if depth is not None:
        return resolve_dump_depth(depth, "off"), f"--depth {depth}"
    if source_method:
        from .buildsource.scan_levels import SourceMethod, method_to_collect_mode
        try:
            mode = method_to_collect_mode(SourceMethod(source_method))
        except ValueError:
            raise click.UsageError(
                f"source.method in .abicheck.yml is invalid: "
                f"{source_method!r} (expected s0..s6 or auto)."
            ) from None
        return mode, f"source.method={source_method} (.abicheck.yml)"
    if old_sources is not None or new_sources is not None:
        return (
            resolve_dump_depth("source", "off"),
            "source (inferred: --sources old=/new= given, no --depth)",
        )
    if old_build_info is not None or new_build_info is not None:
        return (
            resolve_dump_depth("build", "off"),
            "build (inferred: --build-info old=/new= given, no --depth)",
        )
    return "off", "off (no --depth, no --sources/--build-info, no source.method)"


def _normalize_compare_options(
    resolved_cfg: ResolvedCompareConfig,
    *,
    depth: str | None,
    headers: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    debug_format_opt: str | None,
    debug_format: str | None,
    demangle: bool | None,
    fmt: str,
    report_mode: str,
    old_sources: Path | None = None,
    new_sources: Path | None = None,
    old_build_info: Path | None = None,
    new_build_info: Path | None = None,
) -> _NormalizedCompareOptions:
    """Fold the compare option flags into their resolved, dispatch-ready values."""
    # Fold the --depth dial into the internal collect mode (ADR-037 D5), the
    # same way `dump` does; when omitted, infer it from --sources/--build-info
    # (or config source.method) rather than defaulting to "off" (P1 fix).
    collect_mode, _ = _resolve_compare_collect_mode(
        depth, resolved_cfg.source_method,
        old_sources, new_sources, old_build_info, new_build_info,
    )
    if depth == "binary":
        headers, old_headers_only, new_headers_only = (), (), ()

    # Reconcile the --debug-format selector with the legacy --btf/--ctf/--dwarf
    # flags. The selector supersedes the legacy flags whenever it is given:
    # an explicit "auto" returns to auto-detection (None) even if a legacy flag
    # is also present; only when the selector is absent do the legacy flags apply.
    if debug_format_opt is not None:
        effective_debug_format = (
            None if debug_format_opt.lower() == "auto" else debug_format_opt
        )
    else:
        effective_debug_format = debug_format

    demangle_resolved = _resolve_demangle(fmt, demangle)

    # --report-mode impact is sugar for a "full" report with the impact table
    # on -- the one way to ask for that table (the separate --show-impact flag
    # it used to duplicate is gone).
    show_impact = report_mode == "impact"
    if show_impact:
        report_mode = "full"

    return _NormalizedCompareOptions(
        collect_mode, headers, old_headers_only, new_headers_only,
        effective_debug_format, demangle_resolved, report_mode, show_impact,
    )


def _needs_inline_embed(
    old_sources: Path | None, new_sources: Path | None,
    old_build_info: Path | None, new_build_info: Path | None,
) -> bool:
    """True when a side points at a raw checkout / build dir (not a `collect` pack).

    Those sides get dumped inline at --depth so their L3-L5 facts ride embedded in
    the snapshot; pre-built packs fall through to prepare_embedded_build_source.
    """
    from .frontends.cli.commands.compare import _source_is_pack  # cycle
    def _raw_evidence(p: Path | None) -> bool:
        return p is not None and not _source_is_pack(p)

    return any(
        _raw_evidence(p)
        for p in (old_sources, new_sources, old_build_info, new_build_info)
    )


def _resolve_post_manifest_allowlist(
    post_manifest_path: Path | None,
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> set[str] | None:
    """Resolve the --post-manifest committed public surface, or ``None``.

    The manifest *is* the authoritative public surface, so this drives
    FilterNonPublicSurface directly (no header provenance needed) — private
    ``__pp_*`` kernel churn is demoted. Union with the binaries' committed
    (``pp_*``) exports so a *removed* wrapper — absent from a new manifest — stays
    in-surface instead of being silently demoted.
    """
    if post_manifest_path is None:
        return None
    from .post_manifest import contract_scope_allowlist, load_manifest

    try:
        manifest = load_manifest(post_manifest_path)
    except (ValueError, OSError) as exc:
        raise click.UsageError(
            f"--post-manifest {post_manifest_path}: {exc}"
        ) from exc
    return contract_scope_allowlist(manifest, old, new)


def _classify_and_reject_operands(
    old_input: Path, new_input: Path,
) -> tuple[str, str]:
    """Classify both compare operands and reject an application/PIE operand.

    ADR-037 D7 input-type dispatch: a directory/package operand fans out to a
    per-library comparison; an application/PIE operand is not a library `compare`
    can pair (hint at `appcompat`). A single .so / snapshot / dump falls through.
    """
    from .frontends.cli.commands.compare import _reject_application_operand  # cycle
    old_kind = classify_compare_operand(old_input)
    new_kind = classify_compare_operand(new_input)
    if old_kind == "app" or new_kind == "app":
        _reject_application_operand(old_input, new_input, old_kind, new_kind)
    return old_kind, new_kind


def _render_compare_dry_run(
    *,
    old_input: Path, new_input: Path,
    old_kind: str, new_kind: str,
    depth: str | None,
    source_method: str | None = None,
    headers: tuple[Path, ...], includes: tuple[Path, ...],
    old_headers_only: tuple[Path, ...], new_headers_only: tuple[Path, ...],
    old_sources: Path | None, new_sources: Path | None,
    old_build_info: Path | None, new_build_info: Path | None,
    cfg_path: Path | None,
    fmt: str,
    exit_code_scheme: str | None,
    header_backend: str,
    used_by_apps: tuple[Path, ...] = (),
    required_symbols: tuple[str, ...] = (),
) -> Any:
    """Build the ``compare --dry-run`` report (ADR-043 D4): resolve, never diff."""
    from .dry_run import DryRunResult, tool_status

    result = DryRunResult(command="compare")
    result.add(
        "Inputs",
        f"old: {old_input} ({old_kind})",
        f"new: {new_input} ({new_kind})",
    )
    # Effective depth (P1 fix): a dry run must report what the real run will
    # actually do, not just echo the raw --depth string back — the same
    # inference _normalize_compare_options applies (--depth > source.method >
    # inferred from --sources/--build-info > off) drives this.
    collect_mode, effective_depth_label = _resolve_compare_collect_mode(
        depth, source_method, old_sources, new_sources, old_build_info, new_build_info,
    )
    result.add(
        "Resolved depth and source scope",
        f"requested depth: {depth or '(not given)'}",
        f"effective depth: {effective_depth_label}",
        f"effective collect mode: {collect_mode}",
        "source scope: target on each side (compare has no PR change seed)"
        if collect_mode in ("source-target", "source-changed", "graph-full")
        else None,
    )
    all_headers = list(headers) + list(old_headers_only) + list(new_headers_only)
    result.add(
        "Headers and compile context",
        f"ast-frontend: {header_backend}",
        f"headers: {', '.join(str(h) for h in all_headers)}" if all_headers else None,
    )
    result.add(
        "Build/source inputs",
        f"old sources/build-info: {old_sources or old_build_info or '(embedded)'}",
        f"new sources/build-info: {new_sources or new_build_info or '(embedded)'}",
    )
    result.add("Tools and frontends", *tool_status("castxml", "clang", "gcc", "g++"))
    result.add(
        "Configuration and value origins",
        f".abicheck.yml: {cfg_path if cfg_path else '(none found)'}",
    )
    result.add(
        "Output and exit-code behavior",
        f"format: {fmt}",
        f"exit-code scheme: {exit_code_scheme or 'legacy (0/2/4)'}; contract coverage adds an orthogonal 1 under --contract",
    )
    if {old_kind, new_kind} & {"directory", "package"}:
        result.add("Consumer/contract scoping", "dispatch: per-library release fan-out")
    if used_by_apps:
        from .appcompat import parse_app_requirements

        for app in used_by_apps:
            try:
                reqs = parse_app_requirements(app, old_input.stem)
                result.add(
                    "Consumer/contract scoping",
                    f"--used-by {app}: {len(reqs.undefined_symbols)} required "
                    f"symbol(s), {len(reqs.required_versions)} required version(s)",
                )
            except Exception as exc:  # noqa: BLE001 - best-effort dry-run probe
                result.warn(f"--used-by {app}: could not parse requirements: {exc}")
    if required_symbols:
        result.add(
            "Consumer/contract scoping",
            f"--required-symbol(s): {len(required_symbols)} entrypoint(s) required",
        )
    return result


def _report_not_comparable(
    exc: ProfileMismatchError | ScopeMismatchError,
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    fmt: str,
    output: Path | None,
) -> None:
    """Surface an ADR-050 D2 comparability-gate hard failure to the user.

    ``checker.compare``'s gate raises before any ``diff_*`` module runs, so
    there is no ``DiffResult`` for any renderer to work with — unlike an
    ordinary verdict, this cannot be formatted the way a completed comparison
    would be. ``--format json`` gets the schema-conformant ``{"verdict":
    null, "reason": {...}}`` document (schema 2.17,
    ``compare_report.schema.json``); ``sarif``/``junit`` get a real,
    spec-conformant document of their own (a failed-invocation SARIF run /
    an errored JUnit testcase — both formats have a genuine, standard way to
    represent "the run didn't complete", distinct from "zero findings") via
    :func:`sarif.to_sarif_not_comparable`/
    :func:`junit_report.to_junit_xml_not_comparable`, so CI tooling
    consuming those artifacts sees the failure instead of a missing file.
    ``markdown``/``html``/``review`` get the same clear stderr message a
    ``click.UsageError`` would produce and no output file — those are
    human-facing formats already reading this stderr output, and neither has
    an equivalent "run failed" document convention worth fabricating one for.
    """
    kind = "profile_mismatch" if isinstance(exc, ProfileMismatchError) else "scope_mismatch"
    message = str(exc)
    click.echo(
        f"Error: '{old.library}' old={old.version!r} new={new.version!r} are not "
        f"comparable: {message}\n"
        "The two snapshots were not extracted under a comparable profile/scope "
        "contract (ADR-050 D1/D2), so no verdict was produced. Pass "
        '--diagnostic-comparison to force a tentative diff (stamped '
        'assurance: "none") if you understand the risk.',
        err=True,
    )
    refusal = (old.library, old.version, new.version, kind, message)
    if fmt == "json":
        from .report.not_comparable import OperationalStatus, render_not_comparable_json
        from .schemas import REPORT_SCHEMA_VERSION

        _write_or_echo(
            output,
            render_not_comparable_json(
                *refusal,
                report_schema_version=REPORT_SCHEMA_VERSION,
                operational=OperationalStatus.NOT_COMPARABLE,
            ),
        )
    elif fmt == "sarif":
        from .report.render_json import render_mapping_as_json
        from .sarif import to_sarif_not_comparable

        _write_or_echo(output, render_mapping_as_json(to_sarif_not_comparable(*refusal)))
    elif fmt == "junit":
        from .junit_report import to_junit_xml_not_comparable

        xml = to_junit_xml_not_comparable(old.library, old.version, new.version, kind, message)
        _write_or_echo(output, xml)


#: The ``compare`` parameters that can set a gate field on the command line. ``exit_code_scheme`` is its own field; the rest all feed one resolved :class:`~abicheck.severity.SeverityConfig`, so any one of them being typed makes the resolved severity explicitly CLI-selected.

def _reject_incoherent_compare_flags(
    *,
    dry_run: bool,
    output: Path | None,
    secondary_output: Path | None,
    secondary_fmt: str | None,
) -> None:
    """Reject flag combinations that cannot mean anything, before any work.

    Every one of these would otherwise either do nothing silently or
    destroy its own output: a ``--secondary-*`` half-pair, two reports
    aimed at one file, a dry run asked to write a report. Raised as
    ``UsageError`` (exit 64) up front, so none of them is discovered after
    an expensive compare.

    The four ``--secondary-*`` coherence checks are shared with ``scan``
    (Codex review) -- see ``cli_options.reject_incoherent_secondary_output``,
    which this delegates to rather than duplicating them here.

    A ``--contract`` domain given without ``--contract`` used to
    be rejected here too (it would otherwise silently do nothing); CLI audit
    PR 3/5 loosens that into an implication instead -- see
    :func:`abicheck.cli_options.resolve_contract_evaluation`, called by this
    function's caller before ``contract_evaluation`` is used for anything
    else, so there is no longer an incoherent state to reject here.
    """
    reject_incoherent_secondary_output(
        dry_run=dry_run,
        output=output,
        secondary_fmt=secondary_fmt,
        secondary_output=secondary_output,
    )


def _preflight_manifests_and_audit(
    *,
    old_dump_manifest: Path | None,
    new_dump_manifest: Path | None,
    audit_suppressions: bool,
    suppress: Path | None,
    pack_paths: Any,
    policy_file_path: Path | None,
    contract_evaluation: bool,
) -> tuple[DumpManifest | None, DumpManifest | None]:
    """Load the dump manifests and reject what a dry run must not report "ok".

    Everything here runs *ahead* of the ``--dry-run`` emit deliberately: a
    dry run reporting success for an invocation the real run rejects would be
    worse than useless. Returns the two loaded manifest objects (``None`` for
    a side given none).

    ADR-049 D8 pack-vs-pack conflict detection is deliberately *not* part of
    this — see ``validate_pack_manifests`` for which layers it needs that are
    not resolved this early.
    """
    old_manifest_obj: DumpManifest | None = None
    new_manifest_obj: DumpManifest | None = None
    if old_dump_manifest is not None or new_dump_manifest is not None:
        from .errors import ManifestValidationError
        from .workflows.extraction import load_manifest

        try:
            if old_dump_manifest is not None:
                old_manifest_obj = load_manifest(old_dump_manifest)
            if new_dump_manifest is not None:
                new_manifest_obj = load_manifest(new_dump_manifest)
        except ManifestValidationError as exc:
            raise click.UsageError(str(exc)) from exc

    if audit_suppressions and suppress is None:
        # Validated ahead of the --dry-run emit below, same reasoning as the
        # directory/package rejection above (Codex review, fresh evidence):
        # a dry run must not report "ok" for `--audit-suppressions` without
        # `--suppress` when the identical non-dry-run invocation is rejected
        # by the later (post-suppression-loading) guard in this function.
        raise click.UsageError(
            "--audit-suppressions requires --suppress (nothing to audit)."
        )

    # Manifest validity, ahead of the --dry-run emit for the same reason as
    # the two guards above -- see the helper for what deliberately does *not*
    # move here.
    from .cli_compare_receipt import validate_pack_manifests
    from .errors import PackManifestError as _PackManifestError

    try:
        validate_pack_manifests(pack_paths, policy_file_path=policy_file_path, contract_evaluation=contract_evaluation)
    except _PackManifestError as exc:
        raise click.UsageError(str(exc)) from exc
    return old_manifest_obj, new_manifest_obj


def _resolve_required_symbol_policy(
    ctx: click.Context, policy: str, required_symbols: tuple[str, ...],
    required_symbols_from_file: tuple[str, ...],
    required_symbols_file: Path | None, required_symbols_sha: str | None,
) -> tuple[str, str | None, Path | None, str | None]:
    """Pick ``policy`` for a ``--required-symbol`` contract, and say what picked it.

    Required-symbol contracts default to the plugin-oriented policy unless the
    user explicitly picked one -- an explicit ``--policy`` always wins (ADR-043).
    Recorded, not just applied: the ADR-049 receipt has to name whatever really
    selected ``policy.base``, and this value was chosen by a typed
    ``--required-symbol`` rather than by ``--policy`` or a built-in default
    (Codex review, fresh evidence -- the receipt claimed ``strict_abi`` for a run
    that used ``plugin_abi``).

    Which spelling actually *contributed* decides what the receipt names -- not
    merely which was passed. A ``--required-symbols FILE`` run never passed
    ``--required-symbol``, so naming the inline flag fabricates a selector; but a
    file that parsed to nothing selected nothing either, so naming it for a
    ``--required-symbol api_b --required-symbols empty.txt`` run omits the option
    that really made the contract non-empty (Codex review, two rounds). The file
    form wins when it contributed, since it is then both true and the one
    carrying a path and digest to audit.

    Returns ``(policy, selected_by, selected_path, selected_sha)``.
    """
    if not required_symbols or (
        ctx.get_parameter_source("policy") == click.core.ParameterSource.COMMANDLINE
    ):
        return policy, None, None, None
    if required_symbols_from_file:
        return (
            "plugin_abi", "--required-symbols",
            required_symbols_file, required_symbols_sha,
        )
    return "plugin_abi", "--required-symbol", None, None


def _reject_manifest_header_conflicts(
    old_manifest_obj: Any, new_manifest_obj: Any, old_h: Any, new_h: Any,
) -> None:
    """``--dump-manifest <side>=`` and that side's ``-H`` are mutually exclusive."""
    for manifest, side_headers, side in (
        (old_manifest_obj, old_h, "old"), (new_manifest_obj, new_h, "new"),
    ):
        if manifest is not None and side_headers:
            raise click.UsageError(
                f"--dump-manifest {side}=... and a header for the {side} side "
                "(-H/--header) are mutually exclusive -- declare the "
                f"{side} side's public surface in the manifest's own base "
                "profile instead."
            )


def _reject_manifest_non_elf(
    old_manifest_obj: Any, new_manifest_obj: Any,
    old_fmt: str | None, new_fmt: str | None,
) -> None:
    """``--dump-manifest`` extraction is wired for ELF only (ADR-050 D3)."""
    for manifest, fmt, side in (
        (old_manifest_obj, old_fmt, "old"), (new_manifest_obj, new_fmt, "new"),
    ):
        if manifest is not None and fmt != "elf":
            raise click.UsageError(
                f"--dump-manifest {side}=... requires the {side} input to be an "
                f"ELF binary (ADR-050 D3); got {fmt or 'a non-binary input'}."
            )


def _apply_scoped_gating(
    result: Any, old: Any, new: Any, policy: str, pf: PolicyFile | None,
    *,
    used_by_apps: tuple[Path, ...], required_symbols: tuple[str, ...],
    used_by_old_input: Path, used_by_new_input: Path,
    exit_code_scheme: str, sev_config: Any,
    suppression: Any,
) -> int | None:
    """Apply whichever ADR-043 scoped gate this run selected, if any.

    ``--used-by`` and ``--required-symbol`` are mutually exclusive (rejected
    earlier), so at most one applies. Returns the scoped exit code, or ``None``
    when the run is unscoped and the full-library verdict gates instead.
    """
    if used_by_apps:
        return _apply_used_by_scoping(
            result, used_by_apps, used_by_old_input, used_by_new_input, old, new,
            policy, pf,
            exit_code_scheme=exit_code_scheme, sev_config=sev_config,
            suppression=suppression,
        )
    if required_symbols:
        return _apply_required_symbol_scoping(
            result, required_symbols, old, new, policy, pf,
            exit_code_scheme=exit_code_scheme, sev_config=sev_config,
        )
    return None


def _embed_inline_source_sides(
    ctx: click.Context, *,
    old_input: Path, new_input: Path,
    old_sources: Path | None, new_sources: Path | None,
    old_build_info: Path | None, new_build_info: Path | None,
    old_h: Any, new_h: Any, old_inc: Any, new_inc: Any,
    old_version: str, new_version: str, lang: str,
    header_backend: str,
    old_header_backend: str | None, new_header_backend: str | None,
    compile_context: Any,
    follow_deps: bool, search_paths: tuple[Path, ...], ld_library_path: str,
    dwarf_only: bool, effective_debug_format: str | None,
    pdb_path: Path | None, old_pdb_path: Path | None, new_pdb_path: Path | None,
    resolved_old_debug: Any, resolved_new_debug: Any,
    debuginfod: bool, debuginfod_url: str | None,
    collect_mode: str, depth: str | None,
    include_labels: dict[Path, str] | None,
    include_dependencies: bool,
) -> tuple[Path, Path | None, Path | None, Path, Path | None, Path | None]:
    """Dump each raw source/build-dir side inline, returning the rewritten inputs.

    Inline source-tree collection (deep-compare folded into compare): when a
    side's ``--old/new-sources`` points at a raw checkout, or
    ``--old/new-build-info`` at a raw build dir / ``compile_commands.json`` (not
    a ``collect`` pack), dump that side at ``--depth`` so its L3-L5 facts ride
    embedded in the snapshot, the way the standalone deep-compare command used
    to. Pre-built packs fall through unchanged to
    ``prepare_embedded_build_source``.

    Returns ``(old_input, old_sources, old_build_info, new_input, new_sources,
    new_build_info)`` -- an embedded side has its input rewritten to a temporary
    JSON snapshot.
    """
    # G29 Phase A: the L2 header-only semantic graph is no longer a flag
    # a user can request here, so there is nothing to reject loudly. The
    # inline dump below runs through `dump_cmd` (which has no L2-graph
    # attach step of its own — that only lives on compare's own
    # resolve_input calls / dump's own perform_elf_dump/
    # handle_non_elf_dump path), and the rewritten old_input/new_input
    # become a temporary JSON snapshot that _resolve_compare_snapshots
    # below loads via resolve_input's JSON branch, which never attaches
    # a graph either. So a raw --old/new-sources tree or raw
    # --old/new-build-info combination structurally skips the L2 graph
    # (silent, not_collected) — same behavior as before this change,
    # just without a flag to have explicitly asked for it. See
    # docs/contribute/plans/g31-header-graph-default-on-followup.md for
    # extending graph coverage to this path.
    import shutil
    import tempfile

    from .frontends.cli.commands.compare import _embed_inline_source_side  # cycle

    # CLI-over-config explicitness read from compare's *real* ctx (where
    # --ast-frontend/--nostdinc are genuine COMMANDLINE params); the inline
    # dump runs under ctx.invoke where that signal is lost, so we compute it
    # here and thread it through (Codex review). A per-side --ast-frontend old=/new=
    # is itself an explicit frontend for that side.
    _nostdinc_explicit = (
        ctx.get_parameter_source("nostdinc")
        == click.core.ParameterSource.COMMANDLINE
    )
    # The *shared* half only, via the same helper `resolve_compile_context`
    # uses: Click reports one parameter source for the whole repeatable
    # `--ast-frontend`, so `--ast-frontend new=castxml` alone marks it
    # COMMANDLINE and `_split_sided_frontend` then synthesizes the shared
    # value "auto" that nobody typed. Reading the parameter source directly
    # handed that synthesized default to the *old* side as an explicit
    # override, suppressing an `--old-sources` tree's own
    # `.abicheck.yml` `compile.frontend` and freezing it at `auto` -- a
    # materially different snapshot for the side the user never mentioned
    # (Codex review). The per-side override is added back below, where it is
    # genuinely explicit for that side.
    _frontend_explicit = _shared_frontend_explicit(ctx)
    # G31 Phase C follow-up (Codex review): --lang has the identical
    # ctx.invoke-loses-COMMANDLINE-source problem as --ast-frontend/
    # --nostdinc immediately above -- without this, a `compare --lang c++
    # --old-sources tree/` side would silently resolve `lang_explicit=False`
    # in the nested `dump_cmd` invocation below regardless of what the user
    # actually typed, discarding the explicit request on a
    # language-ambiguous header exactly like the bug this file's sibling
    # non-inline path (run_compare's own `lang_explicit`) already fixed.
    _lang_explicit = (
        ctx.get_parameter_source("lang") == click.core.ParameterSource.COMMANDLINE
    )

    _src_tmp = tempfile.mkdtemp(prefix="abicheck-compare-src-")
    # Cleanup on context teardown so the temp dir never leaks, even if an
    # inline dump or _resolve_compare_snapshots raises before we return.
    ctx.call_on_close(lambda: shutil.rmtree(_src_tmp, ignore_errors=True))
    old_input, old_sources, old_build_info = _embed_inline_source_side(
        ctx, input_path=old_input, sources=old_sources,
        headers=old_h, includes=old_inc, version=old_version, lang=lang,
        lang_explicit=_lang_explicit,
        header_backend=old_header_backend or header_backend,
        compile_context=compile_context,
        frontend_explicit=_frontend_explicit or old_header_backend is not None,
        # A nostdinc already resolved True (from --config) must survive the
        # tree-config merge even when the tree omits it (Codex review); False
        # is the default and indistinguishable from "unset", so only True needs
        # preserving.
        nostdinc_explicit=_nostdinc_explicit or compile_context.nostdinc,
        build_info=old_build_info,
        follow_deps=follow_deps, search_paths=search_paths,
        ld_library_path=ld_library_path,
        dwarf_only=dwarf_only, debug_format=effective_debug_format,
        pdb_path=old_pdb_path or pdb_path,
        debug_roots=tuple(resolved_old_debug),
        debuginfod=debuginfod, debuginfod_url=debuginfod_url,
        collect_mode=collect_mode, out_dir=Path(_src_tmp), label="old",
        depth=depth, include_labels=include_labels,
        include_dependencies=include_dependencies,
    )
    new_input, new_sources, new_build_info = _embed_inline_source_side(
        ctx, input_path=new_input, sources=new_sources,
        headers=new_h, includes=new_inc, version=new_version, lang=lang,
        lang_explicit=_lang_explicit,
        header_backend=new_header_backend or header_backend,
        compile_context=compile_context,
        frontend_explicit=_frontend_explicit or new_header_backend is not None,
        nostdinc_explicit=_nostdinc_explicit or compile_context.nostdinc,
        build_info=new_build_info,
        follow_deps=follow_deps, search_paths=search_paths,
        debug_roots=tuple(resolved_new_debug),
        debuginfod=debuginfod, debuginfod_url=debuginfod_url,
        ld_library_path=ld_library_path,
        dwarf_only=dwarf_only, debug_format=effective_debug_format,
        pdb_path=new_pdb_path or pdb_path,
        collect_mode=collect_mode, out_dir=Path(_src_tmp), label="new",
        depth=depth, include_labels=include_labels,
        include_dependencies=include_dependencies,
    )
    return (
        old_input, old_sources, old_build_info,
        new_input, new_sources, new_build_info,
    )


def _resolve_evaluation_config(
    ctx: click.Context, *,
    resolved_cfg: Any, project_cfg: Any, cfg_path: Path | None, cfg_sha: str | None,
    policy: str, policy_file_path: Path | None, policy_file: PolicyFile | None,
    suppression: Any, suppress: Path | None, symbols_list: Any,
    contract_mode: str | None, contract_evaluation: bool,
    scope_public_headers: bool,
    require_justification: bool,
    exit_code_scheme: str | None, severity_preset: str | None,
    pack_paths: tuple[Path, ...],
    policy_selected_by: str | None, policy_selected_path: Path | None,
    policy_selected_sha: str | None,
) -> tuple[Any, PolicyFile | None, Any]:
    """Resolve this invocation's ADR-049 configuration and apply its packs.

    Returns ``(evaluation_config, policy_file, resolved_cfg)``. A D7 same-tier
    conflict, a D8 pack conflict, or an inapplicable manifest is a usage error
    -- the exit code the resolver leaves to its front end.
    """
    pf = policy_file
    # ADR-049: one resolved configuration for this invocation -- the receipt
    # the report carries *and*, since D8's `--pack` landed, the thing that
    # configures the run (hence: before the comparison). Built from the raw
    # CLI values, not the already-merged locals several of these were
    # overwritten with above -- the resolver merges them itself, and a
    # pre-merged value would look CLI-stated.
    from .cli_compare_receipt import resolve_and_apply, typed_parameter_names
    from .cli_options import RUN_PROFILE_META_KEY as _RUN_PROFILE_META_KEY
    from .compatibility_evaluation_resolver import (
        FieldResolutionError,
        PackConflictError,
    )
    from .errors import PackManifestError

    try:
        evaluation_config, pf, resolved_cfg = resolve_and_apply(
            {
                "contract_mode": contract_mode,
                "scope_public_headers": scope_public_headers,
                "policy": policy,
                "policy_file_path": policy_file_path,
                "suppress": suppress,
                "require_justification": require_justification,
                "exit_code_scheme": exit_code_scheme,
                "severity_preset": severity_preset,
                "pack_paths": pack_paths,
            },
            resolved_cfg=resolved_cfg,
            policy=policy,
            contract_evaluation=contract_evaluation,
            # Only this module holds the Click context, so it answers "did the
            # user type this?" and hands the answers over as data -- which is
            # what keeps `cli_compare_receipt` a leaf.
            typed={n for n in typed_parameter_names() if _param_from_cli(n)},
            project_cfg=project_cfg,
            project_path=cfg_path,
            # Both already loaded for the comparison itself; re-reading them
            # here could pair one content's digest with another's rules.
            policy_file=pf,
            suppression=suppression,
            suppress_path=suppress,
            run_profile=ctx.meta.get(_RUN_PROFILE_META_KEY),
            policy_option=policy_selected_by,
            policy_path=policy_selected_path,
            policy_sha256=policy_selected_sha,
            project_sha256=cfg_sha,
            symbols_list=symbols_list,
        )
    except (FieldResolutionError, PackConflictError, PackManifestError) as exc:
        # A D7 same-tier conflict / D8 pack conflict / inapplicable manifest
        # is a usage error, the exit code the resolver leaves to its front end.
        raise click.UsageError(str(exc)) from exc
    return evaluation_config, pf, resolved_cfg


def _render_compare_report(
    result: Any, old: Any, new: Any, *,
    fmt: str, follow_deps: bool, show_only: str | None, report_mode: str,
    show_impact: bool, severity_config: Any,
    demangle: bool, contract_evaluation: bool,
    require_complete_analysis: bool = False,
) -> str:
    """Render one compare report and fold every post-render section into it.

    The primary (``--format``) and secondary (``--write``) renders
    run the identical pipeline and differ only in their arguments, so
    they share this one function rather than keeping two copies that can drift.

    No ``stat``/``recommend`` parameters (CLI cleanup phase two, PR 1): see
    ``_render_output``/``service_render.render_output`` for where the
    one-line format and the unconditional recommendation now live.

    ADR-061 Phase 2 item 5: this used to be a four-fold pipeline -- the
    fourth step, ``_fold_evidence_depth_into_json``, re-parsed the JSON text
    this function was about to return to splice in
    ``old_evidence_depth``/``new_evidence_depth``. Both are now resolved
    once by the caller and attached onto ``result`` before this function
    ever runs (see ``checker_types.DiffResult.old_evidence_depth``'s own
    docstring), so ``_render_output``'s own ``to_json`` call already emits
    them and no fourth fold-in is needed here any more.
    """
    text = _render_output(
        fmt, result, old, new,
        follow_deps=follow_deps,
        show_only=show_only, report_mode=report_mode,
        show_impact=show_impact,
        severity_config=severity_config,
        demangle=demangle,
        contract_evaluation=contract_evaluation,
        require_complete_analysis=require_complete_analysis,
    )
    text = _fold_scoped_compat_into_text(
        text, fmt, result,
        severity_config=severity_config,
        show_only=show_only, report_mode=report_mode,
        contract_evaluation=contract_evaluation,
        demangle=demangle,
    )
    text = _fold_suppression_audit_into_text(
        text, fmt, result.suppression_audit, demangle=demangle
    )
    return _fold_use_case_impact_into_text(
        text, fmt, result, show_only, demangle=demangle
    )


def _attach_use_case_impact(
    result: Any, old: Any, new: Any, manifest: Path | None
) -> None:
    """Attach ``compare --use-cases``'s attribution block to *result*.

    A no-op without the flag. A malformed manifest, and a pair with no
    source graph on either side to resolve entrypoints against, are usage
    errors (exit 64) rather than a silently missing section -- the user
    asked for an attribution the run cannot produce, and an absent block
    would read as "no use case is affected".
    """
    if manifest is None:
        return
    from .errors import UseCaseManifestError
    from .impact.use_case_impact import build_use_case_impact
    from .impact.use_cases import load_use_case_manifest

    try:
        definitions = load_use_case_manifest(manifest)
    except (UseCaseManifestError, OSError) as exc:
        # OSError alongside the manifest-specific error: Click's exists=True
        # only proves the path was there at parse time, not at the read a
        # moment later, and an unhandled OSError would exit 1 with a bare
        # traceback instead of the documented usage-error path.
        raise click.UsageError(str(exc)) from exc

    # Scoped-only findings included, for the same reason
    # `_attach_suppression_audit` below includes them: this runs *after*
    # --used-by/--required-symbol scoping, which synthesizes fresh Change
    # objects onto `scoped_only_changes` (e.g. PE_ORDINAL_RETARGETED) that
    # `_fold_scoped_compat_into_text` then appends to the rendered report's
    # own findings list. Attributing only `result.changes` left
    # `total_changes` smaller than the list beside it, with the synthesized
    # findings neither attributed to a use case nor counted as unattributed
    # (Codex review).
    impact = build_use_case_impact(
        definitions,
        old,
        new,
        list(result.changes) + list(getattr(result, "scoped_only_changes", ()) or ()),
        manifest=str(manifest),
    )
    if impact is None:
        raise click.UsageError(
            f"--use-cases {manifest} needs a source graph to resolve "
            "entrypoints against, and neither side carries one (dump with "
            "--sources/--build-info, or ensure the always-on header-only "
            "graph attached)."
        )
    result.use_case_impact = impact


def _attach_suppression_audit(result: Any, suppression: Any) -> None:
    """Attach the ``--audit-suppressions`` audit trail to *result*.

    Guarded above: audit_suppressions=True implies suppression is not
    None. Audited against the full pre-suppression change set (kept +
    suppressed) plus any --used-by/--required-symbol scoped_only_changes
    (Codex review, fresh evidence: run *after* scoping, not before, so a
    rule matching only a scoping-synthesized finding like
    CONSUMER_REQUIRED_SYMBOL_REMOVED isn't misreported as stale). Not a
    complete fix: scope_diff_to_app/scope_diff_to_required_symbols apply
    suppression internally to their own candidates before this ever
    sees them, so a rule matching only a scoping candidate suppression
    itself already dropped (never reaching scoped_only_changes at all)
    is still invisible here -- closing that needs those functions to
    expose their own pre-suppression candidate list, a separate,
    larger change to appcompat.py this fix does not attempt.
    """
    assert suppression is not None
    # Codex review, fresh evidence: pass the *effective*, policy-override-
    # applied breaking set (not the static BREAKING_KINDS default) so a
    # rule's "high risk" classification matches the verdict this run's
    # own --policy-file would actually produce, e.g. a rule suppressing
    # a kind the policy promoted to BREAKING is reported as high-risk
    # even though it isn't in the built-in BREAKING_KINDS.
    effective_breaking_kinds, _, _, _ = result._effective_kind_sets()
    result.suppression_audit = suppression.audit(
        list(result.changes)
        + list(result.suppressed_changes)
        + list(getattr(result, "scoped_only_changes", ()) or ()),
        breaking_kinds=effective_breaking_kinds,
        # Codex review: a selector-scoped `reclassify:` rule isn't
        # expressible in effective_breaking_kinds at all (that's a
        # kind-wide set); pass the policy file through so `audit()` can
        # classify a reclassified finding by its own rule's resolution.
        policy_file=getattr(result, "policy_file", None),
    )


def _reject_flags_unsupported_for_set_inputs(
    ctx: click.Context, *,
    exit_code_scheme: str | None, reconcile_build_context: bool,
    env_matrix_path: Path | None,
    used_by_apps: tuple[Path, ...], required_symbols: tuple[str, ...],
    diagnostic_comparison: bool, audit_suppressions: bool,
    include_labels: dict[Path, str] | None,
    require_complete_analysis: bool = False,
    use_cases_manifest: Path | None = None,
) -> str | None:
    """Reject the single-pair-only flags on a directory/package compare.

    The per-library fan-out (``compare-release`` backend) consumes the
    resolved scheme from config but has no public CLI support for these
    flags on set inputs -- reject them loudly (ADR-037 D12). Validated ahead
    of the ``--dry-run`` emit so a dry run can't report "ok" for a flag
    combination the real run would then reject.

    ``--pack`` is not rejected here: the caller resolves it separately right
    after this call. ``--write`` (``secondary_fmt``/``secondary_output``) is
    not rejected either -- the release engine supports it directly, so it is
    simply forwarded to ``_dispatch_release_compare``.

    Returns the ``--depth`` value the caller should forward to the fan-out
    (D1: currently always ``"binary"`` or ``None`` --
    :func:`~abicheck.cli_resolve._reject_depth_for_set_inputs` rejects
    everything else outright).
    """
    _reject_set_input_flags(
        exit_code_scheme, reconcile_build_context, env_matrix_path,
        used_by_apps=used_by_apps, required_symbols=required_symbols,
        use_cases_manifest=use_cases_manifest,
        diagnostic_comparison=diagnostic_comparison,
        audit_suppressions=audit_suppressions,
        include_labels=include_labels,
        require_complete_analysis=require_complete_analysis,
    )
    _reject_compile_context_for_set_inputs(ctx)
    return _reject_evidence_flags_for_set_inputs(ctx)


def _report_compare_result(
    ctx: click.Context, result: Any, old: Any, new: Any, *,
    old_input: Path, new_input: Path,
    resolved_cfg: Any, evaluation_config: Any,
    sev_config: Any, report_severity: Any,
    layer_coverage_rows: Any, evidence_metrics: Any, extra_changes: Any,
    explain_patterns: bool,
    show_redundant: bool, show_filtered: bool,
    contract_evaluation: bool,
    policy: str, pf: PolicyFile | None,
    used_by_apps: tuple[Path, ...], required_symbols: tuple[str, ...],
    used_by_old_input: Path, used_by_new_input: Path,
    suppression: Any, audit_suppressions: bool,
    fmt: str, output: Path | None, show_only: str | None, report_mode: str,
    show_impact: bool,
    demangle: bool, demangle_explicit: bool | None, follow_deps: bool,
    secondary_fmt: str | None, secondary_output: Path | None,
    require_complete_analysis: bool = False,
    depth: str | None = None,
    use_cases_manifest: Path | None = None,
) -> None:
    """Everything after the comparison: scope, render, exit.

    ``run_compare``'s third phase (resolve -> compare -> report), split out so
    each reads as one job. Terminal: ends in
    :func:`_exit_with_severity_or_verdict`, which never returns.
    """
    from .cli_buildsource import attach_evidence_metrics
    from .cli_compare_receipt import record_resolved_config

    record_resolved_config(result, resolved_cfg, evaluation_config)

    # P0.4 (P1 review, round 9): `DiffResult.requested_depth` -- the G30
    # report-identity field `analysis_assurance.compute_analysis_assurance`
    # reads to gate `depth_satisfied` -- was never actually populated by any
    # front end (see that field's own comment in checker_types.py), so an
    # explicit `compare --depth source` that never reached source evidence
    # (e.g. both sides lack a compile database, so the effective depth stays
    # `headers`) silently read `requested_depth=None`, `depth_satisfied=None`,
    # and could still report `status="complete"` under
    # `--require-complete-analysis`. `depth` here is `run_compare`'s own
    # raw, Click-validated `--depth` string (one of
    # `checker_types.EVIDENCE_DEPTH_VALUES`, `None` when the flag was
    # omitted) -- copy it onto the result before recomputing
    # `analysis_assurance` below so the requested-vs-effective gate actually
    # has something to check. Only ever set when the flag was explicitly
    # given, mirroring the same "explicit override, never inferred"
    # discipline `_resolve_compare_collect_mode` already applies to `depth`
    # itself for collect-mode resolution -- an inferred depth (from
    # `--sources`/`--build-info` alone, or `.abicheck.yml`'s `source.method`)
    # is not this comparison's stated request in the same on-the-record way
    # an explicit `--depth` flag is.
    if depth is not None:
        result.requested_depth = depth
    if layer_coverage_rows:
        result.layer_coverage = layer_coverage_rows
    # Pass all injected findings (probe-matrix + evidence) so artifact-backed
    # excludes them — none come from L0-L2 diffing.
    attach_evidence_metrics(result, evidence_metrics, extra_changes or [])

    # P0.4 (P1 review): recompute analysis_assurance now that the *real*
    # evidence pack behind this comparison's findings is known. An
    # out-of-band --old/new-build-info / --old/new-sources pack is now
    # attached onto old.build_source/new.build_source, already capped to
    # --depth, by run_compare's resolve-and-cap step above -- so reading it
    # straight off old/new here (not re-resolving the raw paths, which would
    # reload the uncapped pack and defeat the ceiling here) is correct at
    # any --depth (Codex review, PR #1020, second round).
    from .cli_dump_helpers import evidence_depth_label
    from .workflows.gate import compute_analysis_assurance

    old_pack = old.build_source
    new_pack = new.build_source
    result.analysis_assurance = compute_analysis_assurance(
        result, old, new, old_pack=old_pack, new_pack=new_pack,
    )
    # ADR-061 Phase 2 item 5 (post-render mutation): resolved here, before
    # any report is rendered, and attached directly onto `result` -- mirrors
    # `analysis_assurance` immediately above.
    # `reporter.to_json`'s JSON builders read these two fields straight off
    # `result` now, instead of `_fold_evidence_depth_into_json` re-parsing
    # this function's own already-rendered JSON text afterwards to splice
    # them in (see that field's own docstring in checker_types.py).
    result.old_evidence_depth = evidence_depth_label(old, old_pack)
    result.new_evidence_depth = evidence_depth_label(new, new_pack)

    if explain_patterns:
        echo_pattern_modulations(result)

    # used_by_old_input/used_by_new_input are the *original* library paths, captured before _embed_inline_source_sides may have rewritten old_input/new_input to a temporary embedded-snapshot .abi.json path (Codex review) -- passing the post-embed operands here would silently drop the same-binary coverage warning for a --old/new-sources or raw --build-info comparison even when the two real binaries are identical.
    _finalize_compare_result(
        result, used_by_old_input, used_by_new_input,
        show_redundant=show_redundant, show_filtered=show_filtered,
        severity_config=report_severity,
        contract_evaluation=contract_evaluation,
    )

    scoped_exit_code = _apply_scoped_gating(
        result, old, new, policy, pf,
        used_by_apps=used_by_apps, required_symbols=required_symbols,
        used_by_old_input=used_by_old_input, used_by_new_input=used_by_new_input,
        exit_code_scheme=resolved_cfg.exit_code_scheme, sev_config=sev_config,
        suppression=suppression,
    )

    # P0.4 (P2 review): fold the orthogonal coverage/analysis-assurance
    # floors into the scoped exit code *before* any report gets rendered
    # below, and write the folded value back onto `result.scoped_exit_code`
    # -- the exact field SARIF's `gateExitCode`/`scopedExitCode`, JUnit's
    # `abicheck.gate_exit_code`/`abicheck.scoped_exit_code`, and HTML's gate
    # banner all read directly (`getattr(result, "scoped_exit_code", ...)`,
    # no folding of their own). Previously this fold ran only right before
    # `sys.exit` below, *after* `_write_or_echo` had already serialized both
    # the primary and secondary reports from the pre-floor value -- so an
    # artifact could show a passing 0 gate while the process actually exited
    # 1 under `--require-complete-analysis` (Codex review, reproduced).
    # Folding here, and persisting the result back onto `result` rather than
    # only a local variable, means every renderer downstream -- present or
    # future -- reads the one authoritative, already-floored value with no
    # render-order dependency, mirroring how `_exit_with_severity_or_verdict`
    # (cli.py) applies both floors immediately after computing its own base
    # exit code and before returning control to its caller.
    if scoped_exit_code is not None:
        from .workflows.gate import (
            assurance_floor_diagnostic,
            fold_analysis_assurance_exit,
        )

        # `exit_decision.resolve_compare_exit_decision`'s own reasons need
        # the *pre-fold* scoped contribution, not the already-folded value
        # `result.scoped_exit_code` ends up holding below -- otherwise a
        # scoped gate floored by (say) `--require-complete-analysis` alone
        # reports `reasons: ["scoped_gate"]` even though the scoped gate
        # itself never contributed to that number (Codex review).
        result.scoped_compatibility_contribution = scoped_exit_code  # type: ignore[attr-defined]
        announce_coverage_floor(
            result,
            base_exit=scoped_exit_code,
            fmt=fmt,
            secondary_fmt=secondary_fmt,
        )
        scoped_exit_code = fold_coverage_exit(scoped_exit_code, result)
        diagnostic = assurance_floor_diagnostic(
            result,
            require_complete=require_complete_analysis,
            base_exit=scoped_exit_code,
        )
        if diagnostic is not None:
            click.echo(diagnostic, err=True)
        scoped_exit_code = fold_analysis_assurance_exit(
            scoped_exit_code, result, require_complete=require_complete_analysis
        )
        result.scoped_exit_code = scoped_exit_code  # type: ignore[attr-defined]

    if audit_suppressions:
        _attach_suppression_audit(result, suppression)

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
        from .reporter import _finding_id

        stamp_scoped_result_findings(result, finding_id=_finding_id)
    # Only the same-binary warning, not every pre-existing coverage_warnings entry ("no binary metadata"/detector-disabled reasons) -- those are deliberately absent from the one-line summary today (existing tests pin exactly zero extra lines).
    if fmt == ONELINE_FORMAT:
        echo_coverage_warnings([w for w in result.coverage_warnings if "byte-identical" in w])
    _write_or_echo(
        output,
        _render_compare_report(
            result, old, new, fmt=fmt,
            follow_deps=follow_deps, show_only=show_only, report_mode=report_mode,
            show_impact=show_impact, severity_config=report_severity,
            demangle=demangle,
            contract_evaluation=contract_evaluation,
            require_complete_analysis=require_complete_analysis,
        ),
    )

    if secondary_fmt is not None:
        # Always the full, unfiltered report — ignores --show-only
        # (which describes the *primary* format's display) and forces
        # report_mode="full" (not the primary's --report-mode leaf) so a
        # --secondary-* consumer (e.g. a CI action rendering a PR-comment
        # JSON from a markdown-format primary run) sees the complete change
        # set the gate actually acted on, not whatever the primary format
        # chose to filter or group down to. Reuses the same already-computed
        # `result` — no second comparison run.
        # Resolve demangle against secondary_fmt, not the primary-resolved
        # value above — otherwise a machine primary format (e.g. json) paired
        # with a markdown/review secondary format would wrongly inherit
        # demangle=False into the secondary render (Codex review, PR #557).
        _write_or_echo(
            secondary_output,
            _render_compare_report(
                result, old, new, fmt=secondary_fmt,
                follow_deps=follow_deps, show_only=None, report_mode="full",
                show_impact=show_impact,
                severity_config=report_severity,
                demangle=_resolve_demangle(secondary_fmt, demangle_explicit),
                contract_evaluation=contract_evaluation,
                require_complete_analysis=require_complete_analysis,
            ),
        )

    if scoped_exit_code is not None:
        # ADR-043: --used-by / --required-symbol(s) scope the primary verdict
        # to the application/plugin-host contract, floored at the worst
        # scoped result -- the full library verdict stays informational only.
        # ADR-049 §7's coverage axis and P0.4's analysis-assurance axis are
        # both orthogonal to that scoping. Both floors were already folded
        # into `scoped_exit_code` (and persisted onto `result.scoped_exit_code`)
        # above, before any report was rendered (P2 review) -- this is just
        # the terminal exit, not a second fold.
        sys.exit(scoped_exit_code)

    _announce_exit_scheme(resolved_cfg.exit_code_scheme, fmt=fmt)
    _exit_with_severity_or_verdict(
        result, sev_config, resolved_cfg.exit_code_scheme, fmt, secondary_fmt,
        require_complete_analysis=require_complete_analysis,
    )


def run_compare(
    ctx: click.Context,
    *,
    old_input: Path, new_input: Path,
    jobs: int, dso_only: bool, output_dir: Path | None,
    fail_on_removed: bool,
    debug_info1: Path | None, debug_info2: Path | None,
    devel_pkg1: Path | None, devel_pkg2: Path | None,
    include_private_dso: bool, keep_extracted: bool,
    manifest_path: Path | None, bundle_system_providers: str,
    bundle_cohorts: tuple[str, ...], no_bundle_analysis: bool, bundle_facts_out: Path | None,
    headers: tuple[Path, ...], includes: tuple[Path, ...], lang: str,
    header_backend: str,
    sysroot: Path | None, nostdinc: bool,
    # --gcc-options removed as a CLI flag (CLI audit PR 5/5); kept as an
    # internal-only, defaulted-None parameter -- see cli.py's dump_cmd for
    # why (never populated from the CLI anymore, only ever None here).
    gcc_options: str | None = None,
    compiler_path: str | None = None, compiler_prefix: str | None = None,
    compiler_option_tokens: tuple[str, ...] = (),
    old_header_backend: str | None, new_header_backend: str | None,
    old_headers_only: tuple[Path, ...], new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...], new_includes_only: tuple[Path, ...],
    old_version: str, new_version: str,
    fmt: str, demangle: bool | None, output: Path | None,
    suppress: Path | None,
    policy: str, policy_file_path: Path | None,
    pdb_path: Path | None, old_pdb_path: Path | None, new_pdb_path: Path | None,
    dwarf_only: bool,
    severity_preset: str | None,
    config: Path | None,
    exit_code_scheme: str | None,
    follow_deps: bool, search_paths: tuple[Path, ...], ld_library_path: str,
    include_dependencies: bool,
    show_only: str | None,
    scope_public_headers: bool, show_filtered: bool,
    post_manifest_path: Path | None,
    report_mode: str,
    debug_format_opt: str | None,
    debug_format: str | None,
    debug_roots: tuple[Path, ...],
    debug_roots_old: tuple[Path, ...],
    debug_roots_new: tuple[Path, ...],
    debuginfod: bool,
    debuginfod_url: str | None,
    pattern_verdicts: bool,
    explain_patterns: bool,
    surface_metrics: bool,
    reconcile_build_context: bool,
    env_matrix_path: Path | None,
    verbose: bool,
    use_cases_manifest: Path | None = None,
    old_build_info: Path | None = None, new_build_info: Path | None = None,
    old_sources: Path | None = None, new_sources: Path | None = None,
    depth: str | None = None,
    probe_matrix_old: Path | None = None,
    probe_matrix_new: Path | None = None,
    secondary_fmt: str | None = None,
    secondary_output: Path | None = None,
    dry_run: bool = False,
    used_by_apps: tuple[Path, ...] = (),
    required_symbols_opt: tuple[str, ...] = (),
    required_symbols_file: Path | None = None,
    diagnostic_comparison: bool = False,
    contract_mode: str | None = None,
    audit_suppressions: bool = False,
    pack_paths: tuple[Path, ...] = (),
    include_labels: dict[Path, str] | None = None,
    old_dump_manifest: Path | None = None,
    new_dump_manifest: Path | None = None,
    frontend_context: str = "host",
    require_complete_analysis: bool = False,
) -> None:
    """Run the single-pair (or set fan-out) ``compare`` flow and exit accordingly."""
    from .dry_run import reject_dry_run_with_output
    from .frontends.cli.commands.compare import _warn_unused_set_flags  # cycle

    reject_dry_run_with_output(dry_run, output)
    _reject_incoherent_compare_flags(
        dry_run=dry_run,
        output=output,
        secondary_output=secondary_output,
        secondary_fmt=secondary_fmt,
    )
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
    # discarded ..." known gap): --lang carries the same Click default
    # ("c++", indistinguishable from a genuine --lang c++) that dump_cmd's
    # own lang_explicit detection exists to resolve — mirrors the
    # already-established _frontend_explicit/_nostdinc_explicit pattern in
    # _embed_inline_source_sides below. Threaded through
    # _resolve_compare_snapshots -> CompareRequest.lang_explicit so a live
    # ELF/PE/Mach-O side's header-AST pass honors an explicit request on a
    # language-ambiguous header instead of silently auto-detecting past it.
    lang_explicit = (
        ctx.get_parameter_source("lang") == click.core.ParameterSource.COMMANDLINE
    )

    required_symbols, required_symbols_from_file, required_symbols_sha = (
        load_required_symbols(required_symbols_opt, required_symbols_file)
    )
    if used_by_apps and required_symbols:
        raise click.UsageError(
            "--used-by and --required-symbol/--required-symbols are mutually "
            "exclusive: scope the comparison to either application imports or "
            "an explicit required-symbol contract, not both."
        )
    policy, policy_selected_by, policy_selected_path, policy_selected_sha = (
        _resolve_required_symbol_policy(
            ctx, policy, required_symbols,
            required_symbols_from_file, required_symbols_file, required_symbols_sha,
        )
    )
    # ADR-037 D4: load the project config and merge CLI flags over it
    # (precedence CLI > config > built-in default) *before* dispatch, so both the
    # single-file and the directory/package fan-out paths share one resolution.
    cfg_path, project_cfg, resolved_cfg, cfg_sha = _resolve_compare_config(
        config=config,
        severity_preset=severity_preset,
        scope_public_headers=scope_public_headers,
        exit_code_scheme=exit_code_scheme,
        debug_format_opt=debug_format_opt,
        debug_format=debug_format,
        dwarf_only=dwarf_only,
        debuginfod=debuginfod,
        debuginfod_url=debuginfod_url,
    )
    sev_config = resolved_cfg.severity
    scope_public_headers = resolved_cfg.scope_public
    collapse_versioned_symbols = resolved_cfg.collapse_versioned_symbols
    strict_suppressions = resolved_cfg.strict_suppressions
    require_justification = resolved_cfg.require_justification
    # ADR-040 Lever 2: the demoted debug-resolution knobs are now resolved
    # (CLI > config > default); overwrite the raw flag locals so the rest of
    # the flow sees the merged values. The config-only knobs above
    # (collapse/strict/justification/show_redundant) have no flag left to
    # overwrite -- they are simply read off the resolved config.
    debug_format_opt = resolved_cfg.debug_format
    dwarf_only = resolved_cfg.dwarf_only
    debuginfod = resolved_cfg.debuginfod
    debuginfod_url = resolved_cfg.debuginfod_url
    show_redundant = resolved_cfg.show_redundant

    # P1.1 (Codex review): resolved ahead of the inline-embed block below (not
    # just before _resolve_compare_snapshots, where this used to live) so a raw
    # --old/new-sources tree's inline `dump` invocation also gets the per-side
    # debug roots — otherwise --debug-root + --old-sources together silently
    # dumped the inline side without detached DWARF.
    resolved_old_debug, resolved_new_debug = _resolve_debug_roots(
        debug_roots, debug_roots_old, debug_roots_new
    )

    # ADR-037 D7: input-type dispatch. The resolved config (scope/suppression/
    # severity) is forwarded so a set-input compare classifies the same way a
    # single-pair one would (ADR-037 D4).
    old_kind, new_kind = _classify_and_reject_operands(old_input, new_input)

    # A manifest that is resolved and then has its result dropped is the same
    # failure --use-cases is rejected for set inputs to avoid: an apparently
    # successful report with the requested data silently missing. Two ways to
    # land there -- sarif/junit/html render from `result` but never read
    # `DiffResult.use_case_impact`, and the internal one-line format (reached
    # only via the built-in `quick` --profile) promises one shape and one
    # only that the block would break for every CI consumer parsing it.
    #
    # Asked across *every* rendered output rather than the primary alone: the
    # secondary --write render reuses this same attributed result at
    # report_mode="full", so `--format html --write json=PATH` does deliver
    # the attribution and rejecting it was arbitrary (Codex review); the
    # primary-only message below had in fact been proposing that exact
    # arrangement as the fix. One output carrying the block is enough; only
    # when none does is the manifest genuinely resolved for nothing.
    if use_cases_manifest is not None and not (
        format_carries_use_case_impact(fmt)
        or format_carries_use_case_impact(secondary_fmt)
    ):
        if fmt == ONELINE_FORMAT:
            # `fmt` here is the internal-only "oneline" value (reachable only
            # via --profile quick's injected default) -- never a spelling
            # the user typed as --format, so the generic `rendered = f"
            # --format {fmt}"` branch below would name a flag value that
            # doesn't exist on the command line. Name --profile quick
            # instead, and still mention the secondary format when one is
            # ALSO ledgerless (--profile quick --write sarif=...), rather
            # than silently dropping that half of the picture (Codex
            # review, fresh evidence).
            also = (
                f" The --write {secondary_fmt}=... output does not carry it "
                "either." if secondary_fmt else ""
            )
            detail = (
                "--profile quick emits only a one-line summary, which the "
                "attribution block would not fit. Use a different profile "
                "or --format to get the use-case section, add --write "
                "json=PATH to carry it alongside the summary, or drop "
                "--use-cases." + also
            )
        else:
            rendered = f"--format {fmt}" + (
                f" and --write {secondary_fmt}=..." if secondary_fmt else ""
            )
            detail = (
                f"no output this run renders ({rendered}) carries use-case "
                "attribution, so the manifest would be resolved and its result "
                "dropped. Use --format json/markdown/review, or add --write "
                "json=PATH to get one output that carries it alongside the "
                f"{fmt} report."
            )
        raise click.UsageError(f"--use-cases is not supported here: {detail}")

    # CLI cleanup phase two, PR B slice 1: None for a single-pair compare or a
    # release/directory one with no --pack; resolved just below (ahead of the
    # --dry-run emit) otherwise, so a dry run and the real run agree.
    release_pack_application = None
    release_depth: str | None = None
    if {old_kind, new_kind} & {"directory", "package"}:
        release_depth = _reject_flags_unsupported_for_set_inputs(
            ctx,
            exit_code_scheme=exit_code_scheme,
            reconcile_build_context=reconcile_build_context,
            env_matrix_path=env_matrix_path,
            used_by_apps=used_by_apps, required_symbols=required_symbols,
            diagnostic_comparison=diagnostic_comparison,
            audit_suppressions=audit_suppressions,
            include_labels=include_labels,
            require_complete_analysis=require_complete_analysis,
            use_cases_manifest=use_cases_manifest,
        )
        if pack_paths:
            from .cli_compare_receipt import resolve_release_pack_application_from_ctx
            from .cli_options import RUN_PROFILE_META_KEY as _RUN_PROFILE_META_KEY

            release_pack_application = resolve_release_pack_application_from_ctx(
                ctx,
                contract_mode=contract_mode, scope_public_headers=scope_public_headers,
                policy=policy, policy_file_path=policy_file_path, suppress=suppress,
                require_justification=require_justification,
                exit_code_scheme=exit_code_scheme, severity_preset=severity_preset,
                pack_paths=pack_paths, contract_evaluation=contract_evaluation,
                project_cfg=project_cfg, project_path=cfg_path, project_sha256=cfg_sha,
                policy_option=policy_selected_by, policy_path=policy_selected_path,
                policy_sha256=policy_selected_sha,
                run_profile=ctx.meta.get(_RUN_PROFILE_META_KEY),
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
    # never going to work here at all, "not supported here" (the one-line
    # --profile quick case) is the useful message, not "your manifest is
    # malformed".
    #
    # The *other* --use-cases exit a dry run still cannot predict is "neither
    # side carries a source graph", and deliberately so: that is a property
    # of the resolved operands, not of the command line. It is knowable for a
    # snapshot operand and unknowable for a live binary, whose graph only
    # exists after a dump the dry run must not perform -- so checking it
    # would make the dry run's answer depend on which operand shape it was
    # given. Manifest validity has no such asymmetry.
    if use_cases_manifest is not None:
        from .errors import UseCaseManifestError
        from .impact.use_cases import load_use_case_manifest

        try:
            load_use_case_manifest(use_cases_manifest)
        except (UseCaseManifestError, OSError) as exc:
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
        audit_suppressions=audit_suppressions,
        suppress=suppress,
        pack_paths=pack_paths,
        policy_file_path=policy_file_path,
        contract_evaluation=contract_evaluation,
    )
    from .cli_compare_receipt import dry_run_scheme_label

    if dry_run:
        from .dry_run import emit_dry_run

        emit_dry_run(_render_compare_dry_run(
            old_input=old_input, new_input=new_input,
            old_kind=old_kind, new_kind=new_kind,
            depth=depth, source_method=resolved_cfg.source_method,
            headers=headers, includes=includes,
            old_headers_only=old_headers_only, new_headers_only=new_headers_only,
            old_sources=old_sources, new_sources=new_sources,
            old_build_info=old_build_info, new_build_info=new_build_info,
            cfg_path=cfg_path, fmt=fmt,
            exit_code_scheme=dry_run_scheme_label(resolved_cfg, pack_paths),
            header_backend=header_backend,
            used_by_apps=used_by_apps, required_symbols=required_symbols,
        ))

    if {old_kind, new_kind} & {"directory", "package"}:
        # Both-sides L2 compile context for the release fan-out -- see
        # resolve_directory_compile_context's own docstring.
        directory_compile_context, directory_includes = resolve_directory_compile_context(
            ctx,
            gcc_options=gcc_options, sysroot=sysroot, nostdinc=nostdinc,
            header_backend=header_backend, includes=includes,
            build_config=cfg_path, frontend_context=frontend_context,
            compiler_path=compiler_path, compiler_prefix=compiler_prefix,
            compiler_option_tokens=compiler_option_tokens,
        )
        # Dirs the config appended past the CLI -I roots (mirrors the single-pair
        # `config_includes` split below): must survive a per-library-pair
        # `--old/new-include` override, which otherwise replaces `includes`.
        directory_config_includes = tuple(directory_includes[len(includes) :])
        # Off the owner, never via ``abicheck.cli`` (see install_facade_guard).
        from .frontends.cli.commands.compare import _dispatch_release_compare
        _dispatch_release_compare(
            ctx,
            old_dir=old_input, new_dir=new_input,
            headers=headers, includes=directory_includes,
            old_headers_only=old_headers_only, new_headers_only=new_headers_only,
            old_includes_only=old_includes_only, new_includes_only=new_includes_only,
            old_version=old_version, new_version=new_version, lang=lang,
            fmt=fmt, output=output, output_dir=output_dir,
            suppress=suppress, strict_suppressions=strict_suppressions,
            require_justification=require_justification,
            policy=policy, policy_file_path=policy_file_path,
            dso_only=dso_only, jobs=jobs,
            fail_on_removed=fail_on_removed,
            debug_info1=debug_info1, debug_info2=debug_info2,
            devel_pkg1=devel_pkg1, devel_pkg2=devel_pkg2,
            include_private_dso=include_private_dso, keep_extracted=keep_extracted,
            manifest_path=manifest_path,
            bundle_system_providers=bundle_system_providers,
            bundle_cohorts=bundle_cohorts, no_bundle_analysis=no_bundle_analysis,
            bundle_facts_out=bundle_facts_out, scope_public_headers=scope_public_headers,
            include_dependencies=include_dependencies,
            severity_preset=resolved_cfg.merged_severity_preset,
            severity_abi_breaking=resolved_cfg.merged_severity_abi_breaking,
            severity_potential_breaking=resolved_cfg.merged_severity_potential_breaking,
            severity_quality_issues=resolved_cfg.merged_severity_quality_issues,
            severity_addition=resolved_cfg.merged_severity_addition,
            release_exit_code_scheme=resolved_cfg.exit_code_scheme,
            probe_matrix_old=probe_matrix_old, probe_matrix_new=probe_matrix_new,
            verbose=verbose,
            contract_evaluation=contract_evaluation,
            contract_mode=contract_mode,
            pack_application=release_pack_application,
            secondary_fmt=secondary_fmt, secondary_output=secondary_output,
            compile_context=directory_compile_context,
            config_includes=directory_config_includes,
            depth=release_depth,
        )
        return
    # Single-file/snapshot inputs: the set-only fan-out flags do not apply.
    _reject_bundle_facts_out_for_single_pair(bundle_facts_out)
    jobs_explicit = ctx.get_parameter_source("jobs") == click.core.ParameterSource.COMMANDLINE
    _warn_unused_set_flags(jobs_explicit=jobs_explicit, dso_only=dso_only, output_dir=output_dir)

    # Preserved before _normalize_compare_options resolves `demangle` against
    # the *primary* fmt below — the secondary render needs the same tri-state
    # input resolved against `secondary_fmt` instead (see its call site).
    demangle_explicit = demangle

    (
        collect_mode, headers, old_headers_only, new_headers_only,
        effective_debug_format, demangle, report_mode, show_impact,
    ) = _normalize_compare_options(
        resolved_cfg,
        depth=depth,
        headers=headers,
        old_headers_only=old_headers_only, new_headers_only=new_headers_only,
        debug_format_opt=debug_format_opt, debug_format=debug_format,
        demangle=demangle, fmt=fmt,
        report_mode=report_mode,
        old_sources=old_sources, new_sources=new_sources,
        old_build_info=old_build_info, new_build_info=new_build_info,
    )

    # L2 header compile context (compare↔dump↔scan parity, ADR-037 D3): the one
    # shared resolver folds the project's .abicheck.yml compile: block into the CLI
    # cross-toolchain/frontend flags (CLI > config) and appends config include_dirs
    # after the -I roots. It applies to both sides; a per-side --ast-frontend old=/new=
    # overrides still win for the frontend (threaded separately below). cfg_path is
    # the same config compare resolves everything else from (explicit --config or the
    # .abicheck.yml auto-discovered from cwd).
    import dataclasses

    compile_context, merged_includes = resolve_compile_context(
        ctx,
        gcc_options=gcc_options, sysroot=sysroot, nostdinc=nostdinc,
        header_backend=header_backend, includes=includes, build_config=cfg_path,
        frontend_context=frontend_context,
        compiler_path=compiler_path, compiler_prefix=compiler_prefix,
        compiler_option_tokens=compiler_option_tokens,
    )
    # The dirs the config appended past the CLI -I roots. These are documented as
    # applying to *both* sides, so they must survive a per-side --old/new-include
    # override (which replaces the both-sides -I for that side). Keep them separate
    # and re-append after per-side resolution rather than folding into the shared
    # tuple, else the overridden side would lose them (Codex review).
    config_includes = tuple(merged_includes[len(includes):])
    # The merged frontend flows to both sides through the explicit header_backend
    # (so --ast-frontend old=/new= can still override per side); neutralize the
    # frontend on the threaded context so run_dump's `compile.frontend` does NOT
    # outrank that per-side header_backend (it only carries the --gcc-*/--sysroot/
    # --nostdinc knobs for both sides).
    header_backend = compile_context.frontend
    side_compile_context = dataclasses.replace(compile_context, frontend="auto")

    old_h, new_h, old_inc, new_inc = _resolve_per_side_options(
        headers, includes, old_headers_only, new_headers_only,
        old_includes_only, new_includes_only,
    )
    _reject_manifest_header_conflicts(old_manifest_obj, new_manifest_obj, old_h, new_h)
    if config_includes:
        old_inc = list(old_inc) + list(config_includes)
        new_inc = list(new_inc) + list(config_includes)

    # Pair-wide C++20 dialect resolution (P0 fix) — see
    # cli_helpers_compare._pair_wide_dialect_override's docstring. Applied to
    # both `compile_context` (used by the inline-source-embed path below) and
    # `side_compile_context` (used by `_resolve_compare_snapshots`).
    compile_context, side_compile_context = _pair_wide_dialect_override(
        lang, old_h, new_h, compile_context, side_compile_context
    )

    # Preserve the original library paths from before any inline-embed rewrite
    # below, for --used-by/--required-symbol scoping (which needs the real
    # OLD/NEW binaries to parse app import/export requirements, not a rewritten
    # temporary .abi.json snapshot — Codex review).
    used_by_old_input, used_by_new_input = old_input, new_input

    # Inline source-tree collection (deep-compare folded into compare): when a
    # side's --old/new-sources points at a raw checkout, or --old/new-build-info
    # at a raw build dir / compile_commands.json (not a `collect` pack), dump that
    # side at --depth so its L3-L5 facts ride embedded in the snapshot, the way
    # the standalone deep-compare command used to. Pre-built packs fall through
    # unchanged to prepare_embedded_build_source below.
    if _needs_inline_embed(old_sources, new_sources, old_build_info, new_build_info):
        (
            old_input, old_sources, old_build_info,
            new_input, new_sources, new_build_info,
        ) = _embed_inline_source_sides(
            ctx,
            old_input=old_input, new_input=new_input,
            old_sources=old_sources, new_sources=new_sources,
            old_build_info=old_build_info, new_build_info=new_build_info,
            old_h=old_h, new_h=new_h, old_inc=old_inc, new_inc=new_inc,
            old_version=old_version, new_version=new_version, lang=lang,
            header_backend=header_backend,
            old_header_backend=old_header_backend,
            new_header_backend=new_header_backend,
            compile_context=compile_context,
            follow_deps=follow_deps, search_paths=search_paths,
            ld_library_path=ld_library_path,
            dwarf_only=dwarf_only, effective_debug_format=effective_debug_format,
            pdb_path=pdb_path, old_pdb_path=old_pdb_path, new_pdb_path=new_pdb_path,
            resolved_old_debug=resolved_old_debug,
            resolved_new_debug=resolved_new_debug,
            debuginfod=debuginfod, debuginfod_url=debuginfod_url,
            collect_mode=collect_mode, depth=depth,
            include_labels=include_labels,
            include_dependencies=include_dependencies,
        )

    # Follow GNU ld linker scripts up front so the resolved DSO (not the text
    # script) drives format detection, metadata, and dependency analysis.
    # Through the ``cli`` module so a monkeypatch on ``abicheck.cli._normalize_binary_input``
    # is honoured (pre-split resolution semantics); the name is re-exported there.
    old_input, old_fmt = cli_resolve._normalize_binary_input(old_input)
    new_input, new_fmt = cli_resolve._normalize_binary_input(new_input)
    # Same linker-script resolution for the paths --used-by/--required-symbol
    # scoping will parse — these were captured before the inline-embed rewrite
    # above may have replaced old_input/new_input with a temporary snapshot, so
    # they need their own normalization rather than inheriting it from old_input/
    # new_input (which, in that case, no longer point at the original library).
    used_by_old_input, _ = cli_resolve._normalize_binary_input(used_by_old_input)
    used_by_new_input, _ = cli_resolve._normalize_binary_input(used_by_new_input)
    _reject_manifest_non_elf(old_manifest_obj, new_manifest_obj, old_fmt, new_fmt)
    _reject_debug_format_for_non_elf(effective_debug_format, old_fmt, new_fmt)
    _warn_ignored_flags(
        old_fmt is not None, new_fmt is not None,
        headers, includes,
        old_headers_only, new_headers_only,
        old_includes_only, new_includes_only,
    )

    _log_debug_resolution(
        old_input, new_input,
        resolved_old_debug, resolved_new_debug,
        debuginfod=debuginfod, debuginfod_url=debuginfod_url,
    )

    old, new = _resolve_compare_snapshots(
        old_input, new_input, old_fmt, new_fmt,
        old_h, new_h, old_inc, new_inc,
        old_version, new_version, lang,
        pdb_path, old_pdb_path, new_pdb_path,
        dwarf_only, effective_debug_format,
        follow_deps, search_paths, ld_library_path,
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
    )

    # ADR-063 Phase 8's "--depth floor vs ceiling" gap (Codex review, PR
    # #1020): this native CLI path calls `compare_snapshots()` directly, not
    # `classify_compare_pair`, so it needs the identical capped view applied
    # here too -- before every subsequent reader of `old`/`new` below
    # (`fold_l0_hard_removals`, the build-source diff, `compare_snapshots`
    # itself). Imported from `service_compare_pipeline` (`workflows`), not
    # `.policy.depth_projection` directly: ADR-061 forbids `frontends ->
    # policy`, and this CLI module is `frontends`.
    from .service_compare_pipeline import project_pair_to_depth

    old, new = project_pair_to_depth(old, new, depth)

    suppression, pf = _load_suppression_and_policy(
        suppress, policy, policy_file_path,
        strict_suppressions=strict_suppressions,
        require_justification=require_justification,
    )
    # audit_suppressions=True implies suppress is not None (guarded earlier,
    # before the --dry-run emit above) -- _load_suppression_and_policy only
    # returns None here when suppress itself was None, so suppression is
    # guaranteed non-None at this point too.

    # One read for both consumers -- the live overlay and the ADR-049 receipt
    # that names this file with its digest (see the helper for why).
    force_public, symbols_list = resolve_force_public_scope(
        resolved_cfg.public_symbols, None
    )
    _warn_force_public_ignored(force_public, scope_public_headers)

    evaluation_config, pf, resolved_cfg = _resolve_evaluation_config(
        ctx,
        resolved_cfg=resolved_cfg, project_cfg=project_cfg, cfg_path=cfg_path,
        cfg_sha=cfg_sha, policy=policy, policy_file_path=policy_file_path,
        policy_file=pf, suppression=suppression, suppress=suppress,
        symbols_list=symbols_list,
        contract_mode=contract_mode, contract_evaluation=contract_evaluation,
        scope_public_headers=scope_public_headers,
        require_justification=require_justification,
        exit_code_scheme=exit_code_scheme, severity_preset=severity_preset,
        pack_paths=pack_paths,
        policy_selected_by=policy_selected_by,
        policy_selected_path=policy_selected_path,
        policy_selected_sha=policy_selected_sha,
    )
    # A gate pack may have moved a severity level; later consumers read it
    # off here, so re-derive rather than keep the pre-pack value.
    sev_config = resolved_cfg.severity

    extra_changes = _load_probe_matrix_changes(probe_matrix_old, probe_matrix_new)

    # A header-scoped compare can silently drop a function that's genuinely
    # exported but macro-gated out of the header AST on both sides (case97);
    # fold back any hard ELF-only removal the header pass can't see. Gated on
    # the *resolved* snapshots' own from_headers (not the raw -H CLI flags):
    # a dump-then-compare-JSON-snapshots workflow has no -H of its own to see
    # here, but the snapshot it loaded still remembers it was header-scoped.
    # A headerless (DWARF/symbols) compare already sees ELF-only removals
    # directly, so it's not worth the extra symbols-only re-resolve.
    if getattr(old, "from_headers", False) or getattr(new, "from_headers", False):
        extra_changes = fold_l0_hard_removals(old, new, lang, extra_changes)

    # ADR-063 Phase 8 "--depth" ceiling (Codex review, PR #1020, second
    # round): an out-of-band --old/new-sources/-build-info pack never lives
    # on old/new until prepare_embedded_build_source diffs it, so
    # project_pair_to_depth above can't cap it. Resolve + cap it ourselves,
    # attach onto old/new, then pass None below so resolve_side_pack falls
    # back to the now-capped embedded payload instead of the raw pack.
    from .cli_buildsource_helpers import _resolve_side_pack
    from .service_compare_pipeline import project_build_source_pack_to_depth

    old.build_source = project_build_source_pack_to_depth(
        _resolve_side_pack(old_build_info, old_sources, old), depth
    )
    new.build_source = project_build_source_pack_to_depth(
        _resolve_side_pack(new_build_info, new_sources, new), depth
    )

    # Build-info + source facts (ADR-028/033): the helper times inline diffing
    # for the D6/D9 metrics and returns coverage/metrics to attach post-compare.
    from .cli_buildsource import prepare_embedded_build_source
    extra_changes, layer_coverage_rows, evidence_metrics, _ev_changes = (
        prepare_embedded_build_source(
            old, new, collect_mode, extra_changes,
            None, None, None, None,
            policy_file=pf,
        )
    )

    # --post-manifest: scope the comparison to the POST manifest's committed
    # `pp_*`/ufunc-loop surface (private __pp_* kernel churn is demoted).
    post_manifest_allowlist = _resolve_post_manifest_allowlist(
        post_manifest_path, old, new
    )

    apply_patterns = pattern_verdicts or explain_patterns  # --explain implies on
    # Reporting reads the severity config only under the severity exit scheme;
    # resolved once here rather than re-spelled at each of the five consumers.
    report_severity = sev_config if resolved_cfg.exit_code_scheme == "severity" else None
    # One Semantic Pipeline plan, 4B: `evaluation_config` already carries D7's
    # resolved `contract.mode` -- use it, not the stale pre-resolution local.
    resolved_contract_mode = (
        evaluation_config.contract.mode
        if evaluation_config is not None
        else contract_mode
    )
    from .service import compare_snapshots, load_env_matrix
    try:
        env_matrix = load_env_matrix(env_matrix_path)
    except AbicheckError as exc:
        raise click.UsageError(str(exc)) from exc
    try:
        result = compare_snapshots(
            old, new, suppression=suppression, policy=policy, policy_file=pf,
            env_matrix=env_matrix,
            scope_to_public_surface=scope_public_headers,
            force_public_symbols=force_public,
            extra_changes=extra_changes,
            pattern_verdicts=apply_patterns,
            surface_metrics=surface_metrics,
            collapse_versioned_symbols=collapse_versioned_symbols,
            public_surface_allowlist=post_manifest_allowlist,
            reconcile_build_context=reconcile_build_context,
            diagnostic_comparison=diagnostic_comparison,
            contract_evaluation=contract_evaluation,
            contract_mode=resolved_contract_mode,
        )
    except (ProfileMismatchError, ScopeMismatchError) as exc:
        _report_not_comparable(exc, old, new, fmt=fmt, output=output)
        sys.exit(_EXIT_NOT_COMPARABLE)
    _report_compare_result(
        ctx, result, old, new,
        old_input=old_input, new_input=new_input,
        resolved_cfg=resolved_cfg, evaluation_config=evaluation_config,
        sev_config=sev_config, report_severity=report_severity,
        layer_coverage_rows=layer_coverage_rows,
        evidence_metrics=evidence_metrics, extra_changes=extra_changes,
        explain_patterns=explain_patterns,
        show_redundant=show_redundant, show_filtered=show_filtered,
        contract_evaluation=contract_evaluation,
        policy=policy, pf=pf,
        used_by_apps=used_by_apps, required_symbols=required_symbols,
        used_by_old_input=used_by_old_input, used_by_new_input=used_by_new_input,
        suppression=suppression,
        audit_suppressions=audit_suppressions,
        fmt=fmt, output=output, show_only=show_only, report_mode=report_mode,
        show_impact=show_impact,
        demangle=demangle, demangle_explicit=demangle_explicit,
        follow_deps=follow_deps,
        secondary_fmt=secondary_fmt, secondary_output=secondary_output,
        require_complete_analysis=require_complete_analysis,
        depth=depth,
        use_cases_manifest=use_cases_manifest,
    )
