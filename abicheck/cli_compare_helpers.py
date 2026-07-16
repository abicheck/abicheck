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
from typing import TYPE_CHECKING, Any, NamedTuple

import click

from . import cli
from .cli import (
    _announce_exit_scheme,
    _embed_inline_source_side,
    _exit_with_severity_or_verdict,
    _finalize_compare_result,
    _load_probe_matrix_changes,
    _log_debug_resolution,
    _reject_application_operand,
    _render_output,
    _setup_verbosity,
    _source_is_pack,
    _warn_unused_set_flags,
    _write_or_echo,
)
from .cli_audit import echo_pattern_modulations
from .cli_dump_helpers import resolve_dump_depth
from .cli_helpers_compare import (
    _collect_force_public_symbols,
    _resolve_per_side_options,
    _warn_ignored_flags,
    fold_l0_hard_removals,
)
from .cli_options import resolve_compile_context
from .cli_params import _load_suppression_and_policy
from .cli_resolve import (
    _reject_compile_context_for_set_inputs,
    _reject_evidence_flags_for_set_inputs,
    _resolve_compare_snapshots,
    classify_compare_operand,
)
from .errors import AbicheckError

if TYPE_CHECKING:
    from .cli_helpers_compare import ResolvedCompareConfig
    from .model import AbiSnapshot
    from .policy_file import PolicyFile


def _cli_flag(name: str, value: bool) -> bool | None:
    """Return *value* only when *name* actually came from the command line.

    So a flag default (e.g. ``--scope-public-headers``'s True) doesn't mask config.
    """
    src = click.get_current_context().get_parameter_source(name)
    return value if src == click.core.ParameterSource.COMMANDLINE else None


def _param_from_cli(name: str) -> bool:
    """True when parameter *name*'s value came from the command line (not default)."""
    src = click.get_current_context().get_parameter_source(name)
    return bool(src == click.core.ParameterSource.COMMANDLINE)


def _merge_cli_debug_format(
    debug_format_opt: str | None,
    legacy_debug_format: str | None,
    *,
    legacy_from_cli: bool,
) -> str | None:
    """Effective *command-line* debug format across all CLI spellings (ADR-040 L2).

    ``--debug-format`` (``debug_format_opt``) is the primary selector; the hidden
    compatibility flags ``--btf``/``--ctf``/``--dwarf`` write the ``debug_format``
    dest. Either, when typed, must beat a ``.abicheck.yml`` ``debug.format`` — so
    fold a *command-line-sourced* legacy flag in here (the flag's own default is
    ``None``, so ``legacy_from_cli`` distinguishes "typed" from "unset"). Returns
    ``None`` when no format was given on the command line, letting config win.
    """
    if debug_format_opt is not None:
        return debug_format_opt
    if legacy_from_cli:
        return legacy_debug_format
    return None


def _resolve_compare_config(
    *,
    config: Path | None,
    severity_preset: str | None,
    severity_abi_breaking: str | None,
    severity_potential_breaking: str | None,
    severity_quality_issues: str | None,
    severity_addition: str | None,
    scope_public_headers: bool,
    collapse_versioned_symbols: bool,
    public_symbols: tuple[str, ...],
    strict_suppressions: bool,
    require_justification: bool,
    exit_code_scheme: str | None,
    debug_format_opt: str | None,
    debug_format: str | None,
    dwarf_only: bool,
    debuginfod: bool,
    debuginfod_url: str | None,
    show_redundant: bool,
) -> tuple[Path | None, object, ResolvedCompareConfig]:
    """Load the project config and merge CLI flags over it (CLI > config > default).

    ADR-037 D4: resolved *before* dispatch so both the single-file and the
    directory/package fan-out paths share one resolution. Auto-discovered from the
    current directory upward, overridable with ``--config``.
    """
    from .buildsource.inline import load_build_config
    from .cli_helpers_compare import discover_project_config, resolve_compare_config

    cfg_path = config if config is not None else discover_project_config()
    try:
        project_cfg = load_build_config(cfg_path) if cfg_path is not None else None
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    resolved_cfg = resolve_compare_config(
        project_cfg,
        cli_severity_preset=severity_preset,
        cli_severity_abi_breaking=severity_abi_breaking,
        cli_severity_potential_breaking=severity_potential_breaking,
        cli_severity_quality_issues=severity_quality_issues,
        cli_severity_addition=severity_addition,
        cli_scope_public=_cli_flag("scope_public_headers", scope_public_headers),
        cli_collapse_versioned_symbols=_cli_flag(
            "collapse_versioned_symbols", collapse_versioned_symbols
        ),
        cli_public_symbols=public_symbols,
        cli_strict_suppressions=_cli_flag("strict_suppressions", strict_suppressions),
        cli_require_justification=_cli_flag(
            "require_justification", require_justification
        ),
        cli_exit_code_scheme=exit_code_scheme,
        # ADR-040 Lever 2: debug-resolution + show-redundant demoted to config.
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
        cli_show_redundant=_cli_flag("show_redundant", show_redundant),
    )
    return cfg_path, project_cfg, resolved_cfg


def _reject_set_input_flags(
    exit_code_scheme: str | None,
    reconcile_build_context: bool,
    env_matrix_path: Path | None,
    secondary_fmt: str | None = None,
    used_by_apps: tuple[Path, ...] = (),
    required_symbols: tuple[str, ...] = (),
) -> None:
    """Reject single-pair-only flags on a directory/package (release) compare.

    The per-library fan-out has no public CLI support for these, so reject them
    loudly rather than silently ignore them (ADR-037 D12).
    """
    if exit_code_scheme is not None:
        raise click.UsageError(
            "--exit-code-scheme is not supported for directory/package "
            "(release) comparisons: the per-library fan-out uses the legacy "
            "verdict scheme, or severity-aware when severity is configured in "
            ".abicheck.yml. Compare libraries individually for explicit "
            "scheme control."
        )
    if reconcile_build_context:
        raise click.UsageError(
            "--reconcile-build-context is not supported for directory/package "
            "(release) comparisons; it applies to single-file / snapshot "
            "inputs. Compare the libraries individually to use it."
        )
    if env_matrix_path is not None:
        raise click.UsageError(
            "--env-matrix is not supported for directory/package (release) "
            "comparisons yet; it applies to single-file / snapshot inputs. "
            "Compare the libraries individually to use it."
        )
    if secondary_fmt is not None:
        raise click.UsageError(
            "--secondary-format is not supported for directory/package "
            "(release) comparisons yet; it applies to single-file / snapshot "
            "inputs. Compare the libraries individually to use it."
        )
    if used_by_apps:
        raise click.UsageError(
            "--used-by is not supported for directory/package (release) "
            "comparisons: the per-library fan-out has no per-app scoping. "
            "Compare the specific library individually with --used-by."
        )
    if required_symbols:
        raise click.UsageError(
            "--required-symbol/--required-symbols is not supported for "
            "directory/package (release) comparisons: the per-library "
            "fan-out has no plugin-host-contract scoping. Compare the "
            "specific library individually with --required-symbol."
        )


class _NormalizedCompareOptions(NamedTuple):
    collect_mode: str
    headers: tuple[Path, ...]
    old_headers_only: tuple[Path, ...]
    new_headers_only: tuple[Path, ...]
    effective_debug_format: str | None
    demangle: bool
    report_mode: str
    show_impact: bool


def _resolve_demangle(fmt: str, demangle: bool | None) -> bool:
    """Resolve the tri-state ``--demangle`` flag against a specific format.

    Default ON for the text formats whose renderer post-processes symbols
    through ``demangle_text`` (markdown/review), OFF for machine formats
    (json/sarif/junit) and HTML — the HTML renderer emits symbols
    structurally and demangling its string would inject unescaped
    ``<``/``>``/``&`` from C++ names and corrupt the markup. An explicit
    flag always wins over the per-format default.

    Shared by the primary render (:func:`_normalize_compare_options`) and
    the ``--secondary-format`` render in :func:`run_compare`, each resolved
    against its own format — a machine primary format paired with a text
    secondary format (or vice versa) must not inherit the other's default.
    """
    return fmt in {"markdown", "review"} if demangle is None else demangle


def _normalize_compare_options(
    resolved_cfg: ResolvedCompareConfig,
    *,
    depth: str | None,
    annotate: bool,
    annotate_additions: bool,
    headers: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    debug_format_opt: str | None,
    debug_format: str | None,
    demangle: bool | None,
    fmt: str,
    report_mode: str,
    show_impact: bool,
) -> _NormalizedCompareOptions:
    """Fold the compare option flags into their resolved, dispatch-ready values."""
    if annotate_additions and not annotate:
        raise click.UsageError("--annotate-additions requires --annotate")

    # Fold the --depth dial into the internal collect mode (ADR-037 D5), the
    # same way `dump` does. With no preset, compare reads at "off"; --depth
    # binary suppresses the L2 header AST (symbols-only).
    collect_mode = resolve_dump_depth(depth, "off")
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

    # --report-mode impact is sugar for "full" report with the impact table on.
    if report_mode == "impact":
        report_mode = "full"
        show_impact = True

    # ADR-037 D4: the precise S-axis lives in config (source.method). It sets the
    # collection depth only when the user gave no explicit --depth signal
    # (CLI > config).
    if resolved_cfg.source_method and depth is None:
        from .buildsource.scan_levels import SourceMethod, method_to_collect_mode
        try:
            collect_mode = method_to_collect_mode(
                SourceMethod(resolved_cfg.source_method)
            )
        except ValueError:
            raise click.UsageError(
                f"source.method in .abicheck.yml is invalid: "
                f"{resolved_cfg.source_method!r} (expected s0..s6 or auto)."
            ) from None

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
    def _raw_evidence(p: Path | None) -> bool:
        return p is not None and not _source_is_pack(p)

    return any(
        _raw_evidence(p)
        for p in (old_sources, new_sources, old_build_info, new_build_info)
    )


def _reject_debug_format_for_non_elf(
    effective_debug_format: str | None,
    old_fmt: str | None,
    new_fmt: str | None,
) -> None:
    """Reject --debug-format / legacy --btf/--ctf/--dwarf for PE/Mach-O inputs.

    They force an ELF debug format and are silently ignored by the PE/Mach-O dump
    paths, so reject them up front (mirrors dump_cmd). JSON-snapshot / dump inputs
    have ``*_fmt == None`` and are unaffected.
    """
    if effective_debug_format is None:
        return
    for side, bfmt in (("old", old_fmt), ("new", new_fmt)):
        if bfmt in ("pe", "macho"):
            raise click.BadParameter(
                f"--debug-format {effective_debug_format} is only supported "
                f"for ELF binaries, but the {side} input is {bfmt.upper()}."
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
    old_kind = classify_compare_operand(old_input)
    new_kind = classify_compare_operand(new_input)
    if old_kind == "app" or new_kind == "app":
        _reject_application_operand(old_input, new_input, old_kind, new_kind)
    return old_kind, new_kind


def _resolve_debug_roots(
    debug_roots: tuple[Path, ...],
    debug_roots_old: tuple[Path, ...],
    debug_roots_new: tuple[Path, ...],
) -> tuple[list[Path], list[Path]]:
    """Per-side debug roots: --debug-root old=/new= override the both-sides value."""
    resolved_old = list(debug_roots_old) if debug_roots_old else list(debug_roots)
    resolved_new = list(debug_roots_new) if debug_roots_new else list(debug_roots)
    return resolved_old, resolved_new


def _warn_force_public_ignored(
    force_public: object, scope_public_headers: bool,
) -> None:
    """Warn that --public-symbol overlays need --scope-public-headers to apply."""
    if force_public and not scope_public_headers:
        click.echo(
            "Warning: --public-symbol/--public-symbols-list only take effect with "
            "--scope-public-headers; ignoring the widening overlay.",
            err=True,
        )


def _app_compat_summary(result: object) -> dict[str, Any]:
    """Project an :class:`appcompat.AppCompatResult` into a small JSON-safe dict."""
    return {
        "app": result.app_path,  # type: ignore[attr-defined]
        "verdict": result.verdict.value,  # type: ignore[attr-defined]
        "required_symbol_count": result.required_symbol_count,  # type: ignore[attr-defined]
        "missing_symbols": result.missing_symbols,  # type: ignore[attr-defined]
        "missing_versions": result.missing_versions,  # type: ignore[attr-defined]
        "relevant_change_count": len(result.breaking_for_app),  # type: ignore[attr-defined]
        "symbol_coverage": round(result.symbol_coverage, 1),  # type: ignore[attr-defined]
    }


def _plugin_contract_summary(result: object) -> dict[str, Any]:
    """Project a :class:`appcompat.PluginHostContractResult` into a small dict."""
    return {
        "verdict": result.verdict.value,  # type: ignore[attr-defined]
        "required_entrypoints": sorted(result.required_entrypoints),  # type: ignore[attr-defined]
        "missing_entrypoints": result.missing_entrypoints,  # type: ignore[attr-defined]
        "relevant_change_count": len(result.breaking_for_host),  # type: ignore[attr-defined]
        "coverage": round(result.coverage, 1),  # type: ignore[attr-defined]
    }


def _verdict_exit_code(verdict: object) -> int:
    """Map a scoped-comparison Verdict to its floor exit code (ADR-043)."""
    value = getattr(verdict, "value", verdict)
    if value == "BREAKING":
        return 4
    if value == "API_BREAK":
        return 2
    return 0


def _apply_used_by_scoping(
    result: Any, used_by_apps: tuple[Path, ...],
    old_input: Path, new_input: Path,
    policy: str, policy_file: PolicyFile | None,
) -> int:
    """Scope *result* to each ``--used-by`` app; worst-wins (ADR-043).

    Requires OLD/NEW to be real library binaries (not JSON snapshots) since an
    application's actual imports/required symbol versions can only be checked
    against a real export table. Attaches a JSON-safe summary to
    ``result.used_by`` for the renderer and returns the worst app's exit code.
    """
    from .appcompat import scope_diff_to_app
    from .service import detect_binary_format

    for path, label in ((old_input, "OLD"), (new_input, "NEW")):
        if detect_binary_format(path) is None:
            raise click.UsageError(
                f"--used-by requires OLD/NEW to be real library binaries "
                f"(not JSON snapshots); {label} ({path}) is not a recognized "
                f"binary format."
            )

    summaries = []
    worst_exit = 0
    worst_verdict = None
    for app in used_by_apps:
        scoped = scope_diff_to_app(
            result, app, old_input, new_input,
            policy=policy, policy_file=policy_file,
        )
        summaries.append(_app_compat_summary(scoped))
        exit_code = _verdict_exit_code(scoped.verdict)
        # `>=` (not `>`): the first app must always seed worst_verdict, even
        # when its exit code is 0 (COMPATIBLE) -- a strict `>` against the 0
        # starting point meant an all-COMPATIBLE --used-by set left
        # scoped_verdict unset (None) forever, so the JSON verdict swap and
        # the scoped-vs-full-verdict text banner both silently no-op'd for
        # the (very common) fully-compatible case (Codex review).
        if worst_verdict is None or exit_code >= worst_exit:
            worst_exit = exit_code
            worst_verdict = scoped.verdict
    result.used_by = summaries  # type: ignore[attr-defined]
    result.scoped_verdict = worst_verdict  # type: ignore[attr-defined]
    return worst_exit


def _apply_required_symbol_scoping(
    result: Any, required_symbols: tuple[str, ...],
    old: Any, new: Any,
    policy: str, policy_file: PolicyFile | None,
) -> int:
    """Scope *result* to an explicit ``--required-symbol(s)`` contract (ADR-043)."""
    from .appcompat import scope_diff_to_required_symbols

    scoped = scope_diff_to_required_symbols(
        result, old, new, required_symbols,
        policy=policy, policy_file=policy_file,
    )
    result.required_symbols = _plugin_contract_summary(scoped)  # type: ignore[attr-defined]
    result.scoped_verdict = scoped.verdict  # type: ignore[attr-defined]
    return _verdict_exit_code(scoped.verdict)


def _load_required_symbols(
    symbols: tuple[str, ...], symbols_file: Path | None,
) -> tuple[str, ...]:
    """Combine ``--required-symbol`` values with a ``--required-symbols`` file.

    The file format is one symbol per line; blank lines and ``#`` comments are
    ignored (ADR-043, folds the removed ``plugin-check`` command's manifest).
    """
    combined = list(symbols)
    if symbols_file is not None:
        for line in symbols_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                combined.append(stripped)
    # De-duplicate while preserving first-seen order.
    return tuple(dict.fromkeys(combined))


def _render_compare_dry_run(
    *,
    old_input: Path, new_input: Path,
    old_kind: str, new_kind: str,
    depth: str | None,
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
    result.add(
        "Resolved depth and source scope",
        f"requested depth: {depth or '(auto)'}",
        "source scope: target on each side (compare has no PR change seed)"
        if depth == "source" else None,
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
        f"exit-code scheme: {exit_code_scheme or 'legacy (0/2/4)'}",
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
    bundle_cohorts: tuple[str, ...], no_bundle_analysis: bool,
    headers: tuple[Path, ...], includes: tuple[Path, ...], lang: str,
    header_backend: str,
    gcc_path: str | None, gcc_prefix: str | None, gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...], sysroot: Path | None, nostdinc: bool,
    old_header_backend: str | None, new_header_backend: str | None,
    old_headers_only: tuple[Path, ...], new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...], new_includes_only: tuple[Path, ...],
    old_version: str, new_version: str,
    fmt: str, demangle: bool | None, output: Path | None,
    suppress: Path | None, strict_suppressions: bool, require_justification: bool,
    policy: str, policy_file_path: Path | None,
    pdb_path: Path | None, old_pdb_path: Path | None, new_pdb_path: Path | None,
    dwarf_only: bool,
    severity_preset: str | None,
    severity_abi_breaking: str | None,
    severity_potential_breaking: str | None,
    severity_quality_issues: str | None,
    severity_addition: str | None,
    config: Path | None,
    exit_code_scheme: str | None,
    follow_deps: bool, search_paths: tuple[Path, ...], ld_library_path: str,
    show_redundant: bool, show_only: str | None, stat: bool,
    scope_public_headers: bool, collapse_versioned_symbols: bool, show_filtered: bool,
    public_symbols: tuple[str, ...], public_symbols_list: Path | None,
    post_manifest_path: Path | None,
    report_mode: str, show_impact: bool,
    recommend: bool,
    debug_format_opt: str | None,
    debug_format: str | None,
    annotate: bool,
    annotate_additions: bool,
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
    old_build_info: Path | None = None, new_build_info: Path | None = None,
    old_sources: Path | None = None, new_sources: Path | None = None,
    depth: str | None = None,
    probe_matrix_old: Path | None = None,
    probe_matrix_new: Path | None = None,
    header_graph: bool = False,
    header_graph_includes: bool = False,
    secondary_fmt: str | None = None,
    secondary_output: Path | None = None,
    dry_run: bool = False,
    used_by_apps: tuple[Path, ...] = (),
    required_symbols_opt: tuple[str, ...] = (),
    required_symbols_file: Path | None = None,
) -> None:
    """Run the single-pair (or set fan-out) ``compare`` flow and exit accordingly."""
    from .dry_run import reject_dry_run_with_output

    reject_dry_run_with_output(dry_run, output)
    _setup_verbosity(verbose)

    if secondary_fmt is not None and secondary_output is None:
        raise click.UsageError(
            "--secondary-format requires --secondary-output: writing two "
            "output formats to the same stream would be ambiguous."
        )
    if secondary_output is not None and secondary_fmt is None:
        raise click.UsageError(
            "--secondary-output requires --secondary-format: with no format "
            "given there is nothing to render, and the path would be silently "
            "ignored."
        )
    if (
        secondary_output is not None
        and output is not None
        and secondary_output.resolve() == output.resolve()
    ):
        raise click.UsageError(
            "--secondary-output must differ from --output/-o: writing both "
            "formats to the same file would silently overwrite the primary "
            "report with the secondary one."
        )

    required_symbols = _load_required_symbols(required_symbols_opt, required_symbols_file)
    if used_by_apps and required_symbols:
        raise click.UsageError(
            "--used-by and --required-symbol/--required-symbols are mutually "
            "exclusive: scope the comparison to either application imports or "
            "an explicit required-symbol contract, not both."
        )
    # Required-symbol contracts default to the plugin-oriented policy unless the
    # user explicitly picked one -- an explicit --policy always wins (ADR-043).
    if required_symbols and ctx.get_parameter_source("policy") != click.core.ParameterSource.COMMANDLINE:
        policy = "plugin_abi"

    # ADR-037 D4: load the project config and merge CLI flags over it
    # (precedence CLI > config > built-in default) *before* dispatch, so both the
    # single-file and the directory/package fan-out paths share one resolution.
    cfg_path, project_cfg, resolved_cfg = _resolve_compare_config(
        config=config,
        severity_preset=severity_preset,
        severity_abi_breaking=severity_abi_breaking,
        severity_potential_breaking=severity_potential_breaking,
        severity_quality_issues=severity_quality_issues,
        severity_addition=severity_addition,
        scope_public_headers=scope_public_headers,
        collapse_versioned_symbols=collapse_versioned_symbols,
        public_symbols=public_symbols,
        strict_suppressions=strict_suppressions,
        require_justification=require_justification,
        exit_code_scheme=exit_code_scheme,
        debug_format_opt=debug_format_opt,
        debug_format=debug_format,
        dwarf_only=dwarf_only,
        debuginfod=debuginfod,
        debuginfod_url=debuginfod_url,
        show_redundant=show_redundant,
    )
    sev_config = resolved_cfg.severity
    scope_public_headers = resolved_cfg.scope_public
    collapse_versioned_symbols = resolved_cfg.collapse_versioned_symbols
    strict_suppressions = resolved_cfg.strict_suppressions
    require_justification = resolved_cfg.require_justification
    # ADR-040 Lever 2: the demoted debug-resolution + show-redundant knobs are now
    # resolved (CLI > config > default); overwrite the raw flag locals so the rest
    # of the flow sees the merged values.
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

    if dry_run:
        from .dry_run import emit_dry_run

        emit_dry_run(_render_compare_dry_run(
            old_input=old_input, new_input=new_input,
            old_kind=old_kind, new_kind=new_kind,
            depth=depth, headers=headers, includes=includes,
            old_headers_only=old_headers_only, new_headers_only=new_headers_only,
            old_sources=old_sources, new_sources=new_sources,
            old_build_info=old_build_info, new_build_info=new_build_info,
            cfg_path=cfg_path, fmt=fmt, exit_code_scheme=exit_code_scheme,
            header_backend=header_backend,
            used_by_apps=used_by_apps, required_symbols=required_symbols,
        ))

    if {old_kind, new_kind} & {"directory", "package"}:
        # The per-library fan-out (`compare-release` backend) consumes the
        # resolved scheme from config but has no public CLI support for these
        # single-pair-only flags on set inputs — reject them loudly (ADR-037 D12).
        _reject_set_input_flags(
            exit_code_scheme, reconcile_build_context, env_matrix_path, secondary_fmt,
            used_by_apps=used_by_apps, required_symbols=required_symbols,
        )
        _reject_compile_context_for_set_inputs(ctx, project_cfg)
        _reject_evidence_flags_for_set_inputs(ctx)
        # Resolved through the ``cli`` module (not a by-name import) so a test that
        # monkeypatches ``abicheck.cli._dispatch_release_compare`` before invoking
        # ``compare`` is honoured — matching the pre-split resolution semantics.
        cli._dispatch_release_compare(
            ctx,
            old_dir=old_input, new_dir=new_input,
            headers=headers, includes=includes,
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
            scope_public_headers=scope_public_headers,
            severity_preset=resolved_cfg.merged_severity_preset,
            severity_abi_breaking=resolved_cfg.merged_severity_abi_breaking,
            severity_potential_breaking=resolved_cfg.merged_severity_potential_breaking,
            severity_quality_issues=resolved_cfg.merged_severity_quality_issues,
            severity_addition=resolved_cfg.merged_severity_addition,
            release_exit_code_scheme=resolved_cfg.exit_code_scheme,
            probe_matrix_old=probe_matrix_old, probe_matrix_new=probe_matrix_new,
            annotate=annotate, annotate_additions=annotate_additions,
            verbose=verbose,
        )
        return
    # Single-file/snapshot inputs: the set-only fan-out flags do not apply.
    jobs_explicit = (
        ctx.get_parameter_source("jobs") == click.core.ParameterSource.COMMANDLINE
    )
    _warn_unused_set_flags(
        jobs_explicit=jobs_explicit, dso_only=dso_only, output_dir=output_dir
    )

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
        annotate=annotate, annotate_additions=annotate_additions,
        headers=headers,
        old_headers_only=old_headers_only, new_headers_only=new_headers_only,
        debug_format_opt=debug_format_opt, debug_format=debug_format,
        demangle=demangle, fmt=fmt,
        report_mode=report_mode, show_impact=show_impact,
    )

    # L2 header compile context (compare↔dump↔scan parity, ADR-037 D3): the one
    # shared resolver folds the project's .abicheck.yml compile: block into the CLI
    # cross-toolchain/frontend flags (CLI > config) and appends config include_dirs
    # after the -I roots. It applies to both sides; the per-side --old/new-ast-frontend
    # overrides still win for the frontend (threaded separately below). cfg_path is
    # the same config compare resolves everything else from (explicit --config or the
    # .abicheck.yml auto-discovered from cwd).
    import dataclasses

    compile_context, merged_includes = resolve_compile_context(
        ctx,
        gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens, sysroot=sysroot, nostdinc=nostdinc,
        header_backend=header_backend, includes=includes, build_config=cfg_path,
    )
    # The dirs the config appended past the CLI -I roots. These are documented as
    # applying to *both* sides, so they must survive a per-side --old/new-include
    # override (which replaces the both-sides -I for that side). Keep them separate
    # and re-append after per-side resolution rather than folding into the shared
    # tuple, else the overridden side would lose them (Codex review).
    config_includes = tuple(merged_includes[len(includes):])
    # The merged frontend flows to both sides through the explicit header_backend
    # (so --old/new-ast-frontend can still override per side); neutralize the
    # frontend on the threaded context so run_dump's `compile.frontend` does NOT
    # outrank that per-side header_backend (it only carries the --gcc-*/--sysroot/
    # --nostdinc knobs for both sides).
    header_backend = compile_context.frontend
    side_compile_context = dataclasses.replace(compile_context, frontend="auto")

    old_h, new_h, old_inc, new_inc = _resolve_per_side_options(
        headers, includes, old_headers_only, new_headers_only,
        old_includes_only, new_includes_only,
    )
    if config_includes:
        old_inc = list(old_inc) + list(config_includes)
        new_inc = list(new_inc) + list(config_includes)

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
        if header_graph:
            # The inline dump below runs through `dump_cmd`, which has no
            # header_graph/header_graph_includes parameter of its own (the L2
            # graph is only wired into compare's own resolve_input calls, not
            # this internal ctx.invoke(dump_cmd, ...) path) — threading it
            # through would mean adding the flag to dump_cmd/perform_elf_dump/
            # handle_non_elf_dump too, deliberately out of scope for this
            # bounded slice (same ADR-041-deferred wiring noted on
            # --header-graph itself). Reject loudly rather than silently
            # dropping the requested graph (Codex review).
            raise click.UsageError(
                "--header-graph/--header-graph-includes is not supported "
                "together with a raw --old-sources/--new-sources tree or a "
                "raw --old-build-info/--new-build-info (build dir / "
                "compile_commands.json): that combination is dumped inline "
                "via a path that does not build the L2 graph, so it would be "
                "silently dropped. Compare the binary directly (with -H/-I "
                "instead of --sources/--build-info) to use --header-graph."
            )
        import shutil
        import tempfile

        # CLI-over-config explicitness read from compare's *real* ctx (where
        # --ast-frontend/--nostdinc are genuine COMMANDLINE params); the inline
        # dump runs under ctx.invoke where that signal is lost, so we compute it
        # here and thread it through (Codex review). A per-side --old/new-ast-frontend
        # is itself an explicit frontend for that side.
        _nostdinc_explicit = (
            ctx.get_parameter_source("nostdinc")
            == click.core.ParameterSource.COMMANDLINE
        )
        _frontend_explicit = (
            ctx.get_parameter_source("header_backend")
            == click.core.ParameterSource.COMMANDLINE
        )

        _src_tmp = tempfile.mkdtemp(prefix="abicheck-compare-src-")
        # Cleanup on context teardown so the temp dir never leaks, even if an
        # inline dump or _resolve_compare_snapshots raises before we return.
        ctx.call_on_close(lambda: shutil.rmtree(_src_tmp, ignore_errors=True))
        old_input, old_sources, old_build_info = _embed_inline_source_side(
            ctx, input_path=old_input, sources=old_sources,
            headers=old_h, includes=old_inc, version=old_version, lang=lang,
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
        )
        new_input, new_sources, new_build_info = _embed_inline_source_side(
            ctx, input_path=new_input, sources=new_sources,
            headers=new_h, includes=new_inc, version=new_version, lang=lang,
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
        )

    # Follow GNU ld linker scripts up front so the resolved DSO (not the text
    # script) drives format detection, metadata, and dependency analysis.
    # Through the ``cli`` module so a monkeypatch on ``abicheck.cli._normalize_binary_input``
    # is honoured (pre-split resolution semantics); the name is re-exported there.
    old_input, old_fmt = cli._normalize_binary_input(old_input)
    new_input, new_fmt = cli._normalize_binary_input(new_input)
    # Same linker-script resolution for the paths --used-by/--required-symbol
    # scoping will parse — these were captured before the inline-embed rewrite
    # above may have replaced old_input/new_input with a temporary snapshot, so
    # they need their own normalization rather than inheriting it from old_input/
    # new_input (which, in that case, no longer point at the original library).
    used_by_old_input, _ = cli._normalize_binary_input(used_by_old_input)
    used_by_new_input, _ = cli._normalize_binary_input(used_by_new_input)
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
        header_graph=header_graph,
        header_graph_includes=header_graph_includes,
    )

    suppression, pf = _load_suppression_and_policy(
        suppress, policy, policy_file_path,
        strict_suppressions=strict_suppressions,
        require_justification=require_justification,
    )

    force_public = _collect_force_public_symbols(
        resolved_cfg.public_symbols, public_symbols_list
    )
    _warn_force_public_ignored(force_public, scope_public_headers)

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

    # Build-info + source facts (ADR-028/033): the helper times inline diffing
    # for the D6/D9 metrics and returns coverage/metrics to attach post-compare.
    from .cli_buildsource import attach_evidence_metrics, prepare_embedded_build_source
    extra_changes, layer_coverage_rows, evidence_metrics, _ev_changes = (
        prepare_embedded_build_source(
            old, new, collect_mode, extra_changes,
            old_build_info, new_build_info, old_sources, new_sources,
            policy_file=pf,
        )
    )

    # --post-manifest: scope the comparison to the POST manifest's committed
    # `pp_*`/ufunc-loop surface (private __pp_* kernel churn is demoted).
    post_manifest_allowlist = _resolve_post_manifest_allowlist(
        post_manifest_path, old, new
    )

    apply_patterns = pattern_verdicts or explain_patterns  # --explain implies on
    from .service import compare_snapshots, load_env_matrix
    try:
        env_matrix = load_env_matrix(env_matrix_path)
    except AbicheckError as exc:
        raise click.UsageError(str(exc)) from exc
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
    )
    if layer_coverage_rows:
        result.layer_coverage = layer_coverage_rows
    # Pass all injected findings (probe-matrix + evidence) so artifact-backed
    # excludes them — none come from L0-L2 diffing.
    attach_evidence_metrics(result, evidence_metrics, extra_changes or [])

    if explain_patterns:
        echo_pattern_modulations(result)

    _finalize_compare_result(
        result, old_input, new_input,
        show_redundant=show_redundant, show_filtered=show_filtered,
        annotate=annotate, annotate_additions=annotate_additions,
        severity_config=sev_config if resolved_cfg.exit_code_scheme == "severity" else None,
    )

    scoped_exit_code: int | None = None
    if used_by_apps:
        scoped_exit_code = _apply_used_by_scoping(
            result, used_by_apps, used_by_old_input, used_by_new_input, policy, pf,
        )
    elif required_symbols:
        scoped_exit_code = _apply_required_symbol_scoping(
            result, required_symbols, old, new, policy, pf,
        )

    text = _render_output(
        fmt, result, old, new,
        follow_deps=follow_deps,
        show_only=show_only, report_mode=report_mode,
        show_impact=show_impact, stat=stat,
        severity_config=sev_config if resolved_cfg.exit_code_scheme == "severity" else None,
        show_recommendation=recommend,
        demangle=demangle,
    )
    text = _fold_scoped_compat_into_text(text, fmt, result)

    _write_or_echo(output, text)

    if secondary_fmt is not None:
        # Always the full, unfiltered report — ignores --show-only/--stat
        # (which describe the *primary* format's display) and forces
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
        secondary_demangle = _resolve_demangle(secondary_fmt, demangle_explicit)
        secondary_text = _render_output(
            secondary_fmt, result, old, new,
            follow_deps=follow_deps,
            show_only=None, report_mode="full",
            show_impact=show_impact, stat=False,
            severity_config=sev_config if resolved_cfg.exit_code_scheme == "severity" else None,
            show_recommendation=recommend,
            demangle=secondary_demangle,
        )
        secondary_text = _fold_scoped_compat_into_text(secondary_text, secondary_fmt, result)
        _write_or_echo(secondary_output, secondary_text)

    if scoped_exit_code is not None:
        # ADR-043: --used-by / --required-symbol(s) scope the primary verdict
        # to the application/plugin-host contract, floored at the worst
        # scoped result -- the full library verdict stays informational only
        # (already folded into the rendered report above), never gating an
        # invocation explicitly scoped this way.
        sys.exit(scoped_exit_code)

    _announce_exit_scheme(resolved_cfg.exit_code_scheme, fmt=fmt, stat=stat)
    _exit_with_severity_or_verdict(result, sev_config, resolved_cfg.exit_code_scheme)


def _fold_scoped_compat_into_text(text: str, fmt: str, result: Any) -> str:
    """Fold ``--used-by``/``--required-symbol(s)`` summaries into the rendered text.

    JSON gets the summaries as real keys (round-tripped through the existing
    payload); other text-based formats get a small appended section. Binary/
    structured formats (sarif, junit, html) are left untouched -- the full
    verdict they already carry stays authoritative for those consumers.
    """
    used_by = getattr(result, "used_by", None)
    required_symbols = getattr(result, "required_symbols", None)
    if used_by is None and required_symbols is None:
        return text
    scoped_verdict = getattr(result, "scoped_verdict", None)
    scoped_verdict_value = getattr(scoped_verdict, "value", scoped_verdict)

    if fmt == "json":
        import json

        try:
            payload = json.loads(text)
        except ValueError:
            return text
        payload["full_verdict"] = payload.get("verdict")
        if scoped_verdict_value is not None:
            payload["verdict"] = scoped_verdict_value
        if used_by is not None:
            payload["used_by"] = used_by
        if required_symbols is not None:
            payload["required_symbol_contract"] = required_symbols
        return json.dumps(payload, indent=2)

    if fmt in ("markdown", "text", "review"):
        full_verdict_value = getattr(getattr(result, "verdict", None), "value", None)
        header: list[str] = []
        if (
            scoped_verdict_value is not None
            and full_verdict_value is not None
            and scoped_verdict_value != full_verdict_value
        ):
            # The exit code reflects the *scoped* verdict (ADR-043 worst-wins),
            # which can disagree with the full-library verdict this report's own
            # headline already rendered above -- state which one is authoritative
            # for CI instead of leaving the two to silently disagree (Codex review).
            header = [
                f"**Scoped verdict: {scoped_verdict_value}** "
                f"(this is what the exit code reflects; the full library "
                f"verdict above is {full_verdict_value}).",
                "",
            ]
        lines = [*header, text, ""]
        if used_by is not None:
            lines.append("## Scoped to --used-by applications")
            for summary in used_by:
                lines.append(
                    f"- {summary['app']}: {summary['verdict']} "
                    f"(missing {len(summary['missing_symbols'])} symbol(s), "
                    f"{len(summary['missing_versions'])} version(s), "
                    f"{summary['relevant_change_count']} relevant change(s))"
                )
        if required_symbols is not None:
            lines.append("## Scoped to --required-symbol(s) contract")
            lines.append(
                f"- verdict: {required_symbols['verdict']} "
                f"(missing {len(required_symbols['missing_entrypoints'])} of "
                f"{len(required_symbols['required_entrypoints'])} required "
                f"entrypoint(s))"
            )
        return "\n".join(lines)

    return text
