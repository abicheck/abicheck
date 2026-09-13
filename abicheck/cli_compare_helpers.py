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
AI-readiness file-size cap. Not the leaf helper module ``cli_helpers_compare``
(plain, cli-independent utilities): ``run_compare`` drives the full
single-pair compare flow and reuses the option-parsing/render/exit helpers
still in :mod:`abicheck.cli` (imported back below -- the by-design sibling
cycle, allow-listed in ``check_ai_readiness``). Verdict routing stays through
the Tier-2 service (``service.compare_snapshots``), never a direct
``checker.compare`` call (cli-contract, ADR-037 D10.1).
"""

from __future__ import annotations

import math
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
    _reject_debug_format_for_non_elf,
    _resolve_debug_roots,
    _resolve_demangle,
    _warn_force_public_ignored,
    echo_coverage_warnings,
)
from .cli_dump_helpers import resolve_dump_debug_format, resolve_dump_depth
from .cli_helpers_compare import (
    _app_compat_summary as _app_compat_summary,
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
    LANG_DEFAULT,
    _shared_frontend_explicit,
    resolve_compile_context,
    resolve_contract_domain,
    resolve_contract_evaluation,
)
from .cli_resolve import (
    _resolve_compare_snapshots,
    classify_compare_operand,
    resolve_directory_compile_context,
)
from .contract_scoped_promotion import stamp_scoped_result_findings
from .errors import PolicyError, ProfileMismatchError, ScopeMismatchError
from .frontends.cli import compare_enrichment as _enrichment
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
    _attach_suppression_audit as _attach_suppression_audit,
    _reject_flags_unsupported_for_set_inputs,
    _render_compare_report as _render_compare_report,
)
from .frontends.cli.compare_use_cases import reject_use_cases_without_carrying_output
from .frontends.cli.options.params import _load_suppression_and_policy
from .frontends.cli.runtime import (
    _announce_exit_scheme,
    _exit_with_severity_or_verdict,
    _finalize_compare_result,
    _load_probe_matrix_changes,
    _log_debug_resolution,
    _setup_verbosity,
    _write_or_echo,
)
from .serialization import run_scoped_digest_cache
from .service_render import ONELINE_FORMAT
from .workflows.public_header_boundary import (
    project_config_public_header_dirs,
)

if TYPE_CHECKING:
    from .cli_helpers_compare import ResolvedCompareConfig
    from .model import AbiSnapshot
    from .model.consumer_spec import ConsumerAppInput
    from .workflows.extraction import DumpManifest
    from .workflows.policy_file import PolicyFile


def _resolve_compare_config(
    *,
    config: Path | None,
    severity_preset: str | None,
    scope_public_headers: bool,
) -> tuple[Path | None, object, ResolvedCompareConfig, str | None]:
    """This module's long-standing name for `frontends.cli.project_config.
    resolve_project_compare_config`.

    The implementation moved so the ``--no-baseline`` audit -- which
    dispatches before this module runs -- can reach the same resolution
    without closing an import cycle back through it. Call sites unchanged.
    """
    from .frontends.cli.project_config import resolve_project_compare_config

    return resolve_project_compare_config(
        config=config,
        severity_preset=severity_preset,
        scope_public_headers=scope_public_headers,
    )


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
        from .model.evidence_depth_levels import SourceMethod, method_to_collect_mode

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
        depth,
        resolved_cfg.source_method,
        old_sources,
        new_sources,
        old_build_info,
        new_build_info,
    )
    if depth == "binary":
        headers, old_headers_only, new_headers_only = (), (), ()

    # An explicit "auto" returns to auto-detection (None); any other value is
    # passed through verbatim.
    effective_debug_format = resolve_dump_debug_format(debug_format_opt)

    demangle_resolved = _resolve_demangle(fmt, demangle)

    # --report-mode impact is sugar for a "full" report with the impact table
    # on -- the one way to ask for that table (the separate --show-impact flag
    # it used to duplicate is gone).
    show_impact = report_mode == "impact"
    if show_impact:
        report_mode = "full"

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


def _needs_inline_embed(
    old_sources: Path | None,
    new_sources: Path | None,
    old_build_info: Path | None,
    new_build_info: Path | None,
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
        raise click.UsageError(f"--post-manifest {post_manifest_path}: {exc}") from exc
    return contract_scope_allowlist(manifest, old, new)


def _classify_and_reject_operands(
    old_input: Path,
    new_input: Path,
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


#: ADR-068 §3 #19: mirrors ``cli_scan._DURATION_UNITS`` (kept as its own
#: copy, not imported from there -- ``scan`` is scheduled for deletion).
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

    # Manifest validity, ahead of the --dry-run emit for the same reason as
    # the two guards above -- see the helper for what deliberately does *not*
    # move here.
    from .cli_compare_receipt import validate_pack_manifests
    from .errors import PackManifestError as _PackManifestError

    try:
        validate_pack_manifests(
            pack_paths,
            policy_file_path=policy_file_path,
            contract_evaluation=contract_evaluation,
        )
    except _PackManifestError as exc:
        raise click.UsageError(str(exc)) from exc
    return old_manifest_obj, new_manifest_obj


def _resolve_required_symbol_policy(
    ctx: click.Context,
    policy: str,
    required_symbols: tuple[str, ...],
    required_symbols_from_file: tuple[str, ...],
    required_symbols_path: str | None,
    required_symbols_sha: str | None,
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
    merely which was passed. ADR-068 D5 / plan Phase 7h folded the old, separate
    ``--required-symbols FILE`` flag into ``--required-symbol @FILE``, so both
    forms now share one option name for the receipt -- but a file that parsed
    to nothing still selected nothing (Codex review, two rounds, generalized):
    the ``@FILE`` form wins the receipt's path/digest only when it actually
    contributed a symbol.

    Returns ``(policy, selected_by, selected_path, selected_sha)``.
    """
    if not required_symbols or (
        ctx.get_parameter_source("policy") == click.core.ParameterSource.COMMANDLINE
    ):
        return policy, None, None, None
    if required_symbols_from_file:
        return (
            "plugin_abi",
            "--required-symbol",
            Path(required_symbols_path) if required_symbols_path else None,
            required_symbols_sha,
        )
    return "plugin_abi", "--required-symbol", None, None


def _reject_manifest_header_conflicts(
    old_manifest_obj: Any,
    new_manifest_obj: Any,
    old_h: Any,
    new_h: Any,
) -> None:
    """``--dump-manifest <side>=`` and that side's ``-H`` are mutually exclusive."""
    for manifest, side_headers, side in (
        (old_manifest_obj, old_h, "old"),
        (new_manifest_obj, new_h, "new"),
    ):
        if manifest is not None and side_headers:
            raise click.UsageError(
                f"--dump-manifest {side}=... and a header for the {side} side "
                "(-H/--header) are mutually exclusive -- declare the "
                f"{side} side's public surface in the manifest's own base "
                "profile instead."
            )


def _reject_manifest_non_elf(
    old_manifest_obj: Any,
    new_manifest_obj: Any,
    old_fmt: str | None,
    new_fmt: str | None,
) -> None:
    """``--dump-manifest`` extraction is wired for ELF only (ADR-050 D3)."""
    for manifest, fmt, side in (
        (old_manifest_obj, old_fmt, "old"),
        (new_manifest_obj, new_fmt, "new"),
    ):
        if manifest is not None and fmt != "elf":
            raise click.UsageError(
                f"--dump-manifest {side}=... requires the {side} input to be an "
                f"ELF binary (ADR-050 D3); got {fmt or 'a non-binary input'}."
            )


def _embed_inline_source_sides(
    ctx: click.Context,
    *,
    old_input: Path,
    new_input: Path,
    old_sources: Path | None,
    new_sources: Path | None,
    old_build_info: Path | None,
    new_build_info: Path | None,
    old_h: Any,
    new_h: Any,
    old_inc: Any,
    new_inc: Any,
    old_version: str,
    new_version: str,
    lang: str,
    lang_explicit: bool = False,
    header_backend: str,
    old_header_backend: str | None,
    new_header_backend: str | None,
    compile_context: Any,
    follow_deps: bool,
    search_paths: tuple[Path, ...],
    ld_library_path: str,
    dwarf_only: bool,
    effective_debug_format: str | None,
    pdb_path: Path | None,
    old_pdb_path: Path | None,
    new_pdb_path: Path | None,
    resolved_old_debug: Any,
    resolved_new_debug: Any,
    debuginfod: bool,
    debuginfod_url: str | None,
    collect_mode: str,
    depth: str | None,
    include_labels: dict[Path, str] | None,
    include_dependencies: bool,
    build_config: Path | None = None,
    changed_paths: tuple[str, ...] = (),  # ADR-068 Phase 2c: ADR-043 D7 POI scoping
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
        ctx.get_parameter_source("nostdinc") == click.core.ParameterSource.COMMANDLINE
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
    # G31 Phase C follow-up: --lang had the identical
    # ctx.invoke-loses-COMMANDLINE-source problem as --ast-frontend/
    # --nostdinc immediately above -- without forwarding the caller's
    # already-resolved explicitness, a raw `--old/new-sources` tree side
    # would silently resolve `lang_explicit=False` in the nested
    # `dump_cmd` invocation below regardless of what the caller actually
    # requested, discarding the explicit request on a language-ambiguous
    # header. Phase 7 removed `--lang` from `compare`'s CLI entirely, so
    # the only remaining source of an explicit request is `compile.lang`
    # in `.abicheck.yml` -- already resolved by the caller (run_compare)
    # into *lang_explicit* above, since `ctx.get_parameter_source("lang")`
    # can never report COMMANDLINE any more.
    _lang_explicit = lang_explicit

    _src_tmp = tempfile.mkdtemp(prefix="abicheck-compare-src-")
    # Cleanup on context teardown so the temp dir never leaks, even if an
    # inline dump or _resolve_compare_snapshots raises before we return.
    ctx.call_on_close(lambda: shutil.rmtree(_src_tmp, ignore_errors=True))
    old_input, old_sources, old_build_info = _embed_inline_source_side(
        ctx,
        input_path=old_input,
        sources=old_sources,
        headers=old_h,
        includes=old_inc,
        version=old_version,
        lang=lang,
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
        follow_deps=follow_deps,
        search_paths=search_paths,
        ld_library_path=ld_library_path,
        dwarf_only=dwarf_only,
        debug_format=effective_debug_format,
        pdb_path=old_pdb_path or pdb_path,
        debug_roots=tuple(resolved_old_debug),
        debuginfod=debuginfod,
        debuginfod_url=debuginfod_url,
        collect_mode=collect_mode,
        out_dir=Path(_src_tmp),
        label="old",
        depth=depth,
        include_labels=include_labels,
        changed_paths=changed_paths,
        include_dependencies=include_dependencies,
        build_config=build_config,
    )
    new_input, new_sources, new_build_info = _embed_inline_source_side(
        ctx,
        input_path=new_input,
        sources=new_sources,
        headers=new_h,
        includes=new_inc,
        version=new_version,
        lang=lang,
        lang_explicit=_lang_explicit,
        header_backend=new_header_backend or header_backend,
        compile_context=compile_context,
        frontend_explicit=_frontend_explicit or new_header_backend is not None,
        nostdinc_explicit=_nostdinc_explicit or compile_context.nostdinc,
        build_info=new_build_info,
        follow_deps=follow_deps,
        search_paths=search_paths,
        debug_roots=tuple(resolved_new_debug),
        debuginfod=debuginfod,
        debuginfod_url=debuginfod_url,
        ld_library_path=ld_library_path,
        dwarf_only=dwarf_only,
        debug_format=effective_debug_format,
        pdb_path=new_pdb_path or pdb_path,
        collect_mode=collect_mode,
        out_dir=Path(_src_tmp),
        label="new",
        depth=depth,
        include_labels=include_labels,
        changed_paths=changed_paths,
        include_dependencies=include_dependencies,
        build_config=build_config,
    )
    return (
        old_input,
        old_sources,
        old_build_info,
        new_input,
        new_sources,
        new_build_info,
    )


def _resolve_evaluation_config(
    ctx: click.Context,
    *,
    resolved_cfg: Any,
    project_cfg: Any,
    cfg_path: Path | None,
    cfg_sha: str | None,
    policy: str,
    policy_file_path: Path | None,
    policy_file: PolicyFile | None,
    suppression: Any,
    suppress: Path | None,
    symbols_list: Any,
    contract_mode: str | None,
    contract_evaluation: bool,
    scope_public_headers: bool,
    require_justification: bool,
    severity_preset: str | None,
    pack_paths: tuple[Path, ...],
    policy_selected_by: str | None,
    policy_selected_path: Path | None,
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
    except PolicyError as exc:
        # A malformed `.abicheck.yml` `policy.overrides` entry (finding 1).
        raise click.BadParameter(str(exc), param_hint="--policy") from exc
    return evaluation_config, pf, resolved_cfg


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
    sev_config: Any,
    report_severity: Any,
    layer_coverage_rows: Any,
    evidence_metrics: Any,
    extra_changes: Any,
    explain_patterns: bool,
    show_redundant: bool,
    show_filtered: bool,
    contract_evaluation: bool,
    policy: str,
    pf: PolicyFile | None,
    used_by_apps: tuple[ConsumerAppInput, ...],
    required_symbols: tuple[str, ...],
    used_by_old_input: Path,
    used_by_new_input: Path,
    suppression: Any,
    audit_suppressions: bool,
    fmt: str,
    output: Path | None,
    show_only: str | None,
    report_mode: str,
    show_impact: bool,
    demangle_explicit: bool | None,
    follow_deps: bool,
    secondary_writes: tuple[tuple[str, Path], ...],
    require_complete_analysis: bool = False,
    require_complete_analysis_stated: bool = False,
    depth: str | None = None,
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
    ``_resolve_compare_config`` loaded for this same invocation) -- forwarded
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
    from .cli_buildsource import attach_evidence_metrics
    from .cli_compare_receipt import record_resolved_config

    record_resolved_config(
        result,
        resolved_cfg,
        evaluation_config,
        project_config_path=project_config_path,
        project_config_sha256=project_config_sha256,
        require_complete_analysis_stated=require_complete_analysis_stated,
    )

    # P0.4 (P1 review, round 9): `DiffResult.requested_depth` -- the G30
    # report-identity field `analysis_assurance.compute_analysis_assurance`
    # reads to gate `depth_satisfied` -- was never actually populated by any
    # front end (see that field's own comment in checker_types.py), so an
    # explicit `compare --depth source` that never reached source evidence
    # (e.g. both sides lack a compile database, so the effective depth stays
    # `headers`) silently read `requested_depth=None`, `depth_satisfied=None`,
    # and could still report `status="complete"` under
    # `assurance.require_complete`. `depth` here is `run_compare`'s own
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
    from functools import partial

    from .cli_dump_helpers import evidence_depth_label
    from .workflows.gate import compute_analysis_assurance, same_persisted_content

    old_pack = old.build_source
    new_pack = new.build_source
    result.analysis_assurance = compute_analysis_assurance(
        result,
        old,
        new,
        old_pack=old_pack,
        new_pack=new_pack,
        same_content=partial(same_persisted_content, old, new),
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
        result,
        used_by_old_input,
        used_by_new_input,
        show_redundant=show_redundant,
        show_filtered=show_filtered,
        severity_config=report_severity,
        contract_evaluation=contract_evaluation,
        old_snapshot=old,
        new_snapshot=new,
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
        exit_code_scheme=resolved_cfg.exit_code_scheme,
        sev_config=sev_config,
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
    if suppression is not None:
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
    # `demangle` stays resolved *per format* (`_resolve_demangle`) -- that is
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
                demangle=_resolve_demangle(target_fmt, demangle_explicit),
                contract_evaluation=contract_evaluation,
                require_complete_analysis=require_complete_analysis,
                audit_suppressions=audit_suppressions,
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
    _exit_with_severity_or_verdict(
        result,
        sev_config,
        resolved_cfg.exit_code_scheme,
        fmt,
        [f for f, _ in secondary_writes],
        require_complete_analysis=require_complete_analysis,
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
    old_header_backend: str | None = None,
    new_header_backend: str | None = None,
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...],
    new_includes_only: tuple[Path, ...],
    old_version: str,
    new_version: str,
    fmt: str,
    demangle: bool | None,
    output: Path | None,
    suppress: Path | None,
    policy: str,
    policy_file_path: Path | None,
    severity_preset: str | None,
    config: Path | None,
    follow_deps: bool,
    search_paths: tuple[Path, ...],
    ld_library_path: str,
    include_dependencies: bool,
    show_only: str | None,
    scope_public_headers: bool,
    show_filtered: bool,
    post_manifest_path: Path | None,
    report_mode: str,
    debug_roots: tuple[Path, ...],
    debug_roots_old: tuple[Path, ...],
    debug_roots_new: tuple[Path, ...],
    # ADR-068 D4/Phase 5: --pattern-verdicts/--surface-metrics are gone --
    # both run unconditionally now (compare_snapshots() passes True for
    # both below). explain_patterns renders the always-on ledger via
    # `--view patterns`, same as show_filtered/audit_suppressions below.
    explain_patterns: bool,
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
    audit_suppressions: bool = False,
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
    from .frontends.cli.commands.compare import _warn_unused_set_flags  # cycle

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
        _resolve_required_symbol_policy(
            ctx,
            policy,
            required_symbols,
            required_symbols_from_file,
            required_symbols_path,
            required_symbols_sha,
        )
    )
    # ADR-037 D4: load the project config and merge CLI flags over it
    # (precedence CLI > config > built-in default) *before* dispatch, so both the
    # single-file and the directory/package fan-out paths share one resolution.
    cfg_path, project_cfg, resolved_cfg, cfg_sha = _resolve_compare_config(
        config=config,
        severity_preset=severity_preset,
        scope_public_headers=scope_public_headers,
    )
    sev_config = resolved_cfg.severity
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
    # ADR-068 D5 / Phase 7a: --dwarf-only/--debuginfod/--debuginfod-url/
    # --debug-format are gone as CLI flags (hidden, already config-backed
    # duplicates) -- config-only now, read straight off the resolved config
    # with no raw CLI local to overwrite, same as collapse/strict/
    # justification/show_redundant above.
    debug_format_opt = resolved_cfg.debug_format
    dwarf_only = resolved_cfg.dwarf_only
    debuginfod = resolved_cfg.debuginfod
    debuginfod_url = resolved_cfg.debuginfod_url
    # Phase 7 (§4.1 CONFIG row): --pdb-path is config-only; see compare_pdb_config's docstring.
    from .frontends.cli.compare_pdb_config import resolve_and_reject_shared_pdb_path

    pdb_path = resolve_and_reject_shared_pdb_path(
        resolved_cfg.pdb_path, old_input=old_input, new_input=new_input
    )
    old_pdb_path: Path | None = None
    new_pdb_path: Path | None = None
    show_redundant = resolved_cfg.show_redundant
    # Phase 7 (one-comparison-product.md §4.1): `--lang` has no CLI flag
    # left either; `compile.lang` is its only source, defaulting to the
    # same `LANG_DEFAULT` ("c++") the removed flag carried so an
    # unconfigured project's behavior is unchanged. `lang` is always None
    # here (compare_cmd's own kwargs never carry a "lang" key any more --
    # the stored-bundle-facts dispatch path resolves its own `lang`/
    # `lang_explicit` upstream in compare.py and never reaches run_compare
    # at all), so `resolved_cfg.compile_lang` is the sole source of both
    # the resolved value and its explicitness.
    if lang is None:
        lang = resolved_cfg.compile_lang or LANG_DEFAULT
    lang_explicit = resolved_cfg.compile_lang is not None
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
            audit_suppressions=audit_suppressions,
            include_labels=include_labels,
            use_cases_manifest=use_cases_manifest,
            suppress=suppress,
            budget=budget,
            pdb_path=pdb_path,
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
        from .frontends.cli.commands.compare import (
            reject_release_incompatible_view_mode,
        )

        reject_release_incompatible_view_mode(
            report_mode,
            show_filtered=show_filtered,
        )
        # Resolved unconditionally, not only under `--pack` (finding 1,
        # round 4): a no-pack run's own project-backed `.abicheck.yml`
        # `policy.overrides` still needs to reach a real config for
        # `record_release_resolved_config` -- see that resolver's docstring.
        from .cli_compare_receipt import resolve_release_pack_application_from_ctx

        release_pack_application = resolve_release_pack_application_from_ctx(
            ctx,
            contract_mode=contract_mode,
            scope_public_headers=scope_public_headers,
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
        pack_paths=pack_paths,
        policy_file_path=policy_file_path,
        contract_evaluation=contract_evaluation,
    )
    from .cli_compare_receipt import dry_run_scheme_label

    # Round 5 finding 1: validate policy.overrides before --dry-run exits.
    from .pack_application import preflight_validate_project_policy_overrides

    preflight_validate_project_policy_overrides(project_cfg, cfg_path)
    if dry_run:
        from .dry_run import emit_dry_run
        from .frontends.cli.compare_dry_run import build_compare_dry_run_result

        # ADR-043 D4's dry-run report must reflect the *effective* depth, not
        # just echo `--depth` back -- the same resolution `_render_compare_
        # dry_run` used to compute internally before it moved to
        # `frontends/cli/compare_dry_run.py` (see that module's own
        # docstring for why it now takes the resolved pair as parameters
        # instead of resolving them itself).
        collect_mode_dr, effective_depth_label_dr = _resolve_compare_collect_mode(
            depth,
            resolved_cfg.source_method,
            old_sources,
            new_sources,
            old_build_info,
            new_build_info,
        )
        emit_dry_run(
            build_compare_dry_run_result(
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
            )
        )

    if {old_kind, new_kind} & {"directory", "package"}:
        # Both-sides L2 compile context for the release fan-out -- see
        # resolve_directory_compile_context's own docstring.
        directory_compile_context, directory_includes = (
            resolve_directory_compile_context(
                ctx,
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
                # `cfg_path` is explicit --config OR an auto-discovered
                # .abicheck.yml, not the raw CLI value merge_compile_config's
                # `build_config is not None` inference expects -- without this
                # an auto-discovered `compile.compiler` bypasses the untrusted-
                # executable-selection gate (Codex review, PR #1154).
                config_explicit=(config is not None),
            )
        )
        # Dirs the config appended past the CLI -I roots (mirrors the single-pair
        # `config_includes` split below): must survive a per-library-pair
        # `--old/new-include` override, which otherwise replaces `includes`.
        directory_config_includes = tuple(directory_includes[len(includes) :])
        # Off the owner, never via ``abicheck.cli`` (install_facade_guard);
        # ADR-068 §3 #23: also thread policy.overrides to the release fan-out.
        from .frontends.cli.commands.compare import _dispatch_release_compare
        from .pack_application import resolve_release_project_policy_overrides

        _dispatch_release_compare(
            ctx,
            old_dir=old_input,
            new_dir=new_input,
            project_policy_overrides=resolve_release_project_policy_overrides(
                project_cfg, cfg_path
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
            demangle=demangle,
            explain_patterns=explain_patterns,
            # Forwarded so _dispatch_release_compare can reject (no per-library ledger yet).
            show_filtered=show_filtered,
            audit_suppressions=audit_suppressions,
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

    # Preserved before _normalize_compare_options resolves `demangle` against
    # the *primary* fmt below — the secondary render needs the same tri-state
    # input resolved against `secondary_fmt` instead (see its call site).
    demangle_explicit = demangle

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
        demangle=demangle,
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
        # `cfg_path` is `config` (explicit --config) OR the cwd-upward
        # auto-discovered .abicheck.yml -- see the identical note on the
        # directory/package `resolve_directory_compile_context` call above
        # (Codex review, fresh evidence -- real finding on PR #1154).
        config_explicit=(config is not None),
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
    header_backend = compile_context.frontend
    side_compile_context = dataclasses.replace(compile_context, frontend="auto")

    old_h, new_h, old_inc, new_inc = _resolve_per_side_options(
        headers,
        includes,
        old_headers_only,
        new_headers_only,
        old_includes_only,
        new_includes_only,
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
    from . import deadline

    # ADR-068 §3 #28 (CodeRabbit review): captured before the embed below
    # rewrites old_input/new_input to a temp .abi.json -- a raw --sources/
    # --build-info side is genuinely live-extracted, so it stays blamable.
    old_had_raw_evidence = _needs_inline_embed(old_sources, None, old_build_info, None)
    new_had_raw_evidence = _needs_inline_embed(None, new_sources, None, new_build_info)

    if _needs_inline_embed(old_sources, new_sources, old_build_info, new_build_info):
        try:
            (
                old_input,
                old_sources,
                old_build_info,
                new_input,
                new_sources,
                new_build_info,
            ) = _embed_inline_source_sides(
                ctx,
                old_input=old_input,
                new_input=new_input,
                old_sources=old_sources,
                new_sources=new_sources,
                old_build_info=old_build_info,
                new_build_info=new_build_info,
                old_h=old_h,
                new_h=new_h,
                old_inc=old_inc,
                new_inc=new_inc,
                old_version=old_version,
                new_version=new_version,
                lang=lang,
                lang_explicit=lang_explicit,
                header_backend=header_backend,
                old_header_backend=old_header_backend,
                new_header_backend=new_header_backend,
                compile_context=compile_context,
                follow_deps=follow_deps,
                search_paths=search_paths,
                ld_library_path=ld_library_path,
                dwarf_only=dwarf_only,
                effective_debug_format=effective_debug_format,
                pdb_path=pdb_path,
                old_pdb_path=old_pdb_path,
                new_pdb_path=new_pdb_path,
                resolved_old_debug=resolved_old_debug,
                resolved_new_debug=resolved_new_debug,
                debuginfod=debuginfod,
                debuginfod_url=debuginfod_url,
                collect_mode=collect_mode,
                depth=depth,
                include_labels=include_labels,
                changed_paths=_enrich.changed_paths,
                include_dependencies=include_dependencies,
                build_config=config,
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

    # Follow GNU ld linker scripts up front so the resolved DSO (not the text
    # script) drives format detection, metadata, and dependency analysis.
    # ``cli_resolve`` owns this name; patch it there. (The comment here used to
    # claim the call went through ``abicheck.cli`` for monkeypatch reasons -- it
    # did not, and that alias table no longer exists.)
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

    # ADR-068 §3 #19: `--budget`'s deadline is already ambient (entered once,
    # early, by `enter_budget_scope` above) -- `deadline.check()`/
    # `bounded_timeout()` consult it however deep the call stack; the explicit
    # `deadline.check()` below closes the gap for a stored-snapshot-only pair,
    # which touches neither (Codex review, PR #1178). Only a fresh try/except
    # is needed, not a second `deadline_scope` entry (which would reset it).
    try:
        deadline.check()
        old, new = _resolve_compare_snapshots(
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
        suppress,
        policy,
        policy_file_path,
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
        resolved_cfg=resolved_cfg,
        project_cfg=project_cfg,
        cfg_path=cfg_path,
        cfg_sha=cfg_sha,
        policy=policy,
        policy_file_path=policy_file_path,
        policy_file=pf,
        suppression=suppression,
        suppress=suppress,
        symbols_list=symbols_list,
        contract_mode=contract_mode,
        contract_evaluation=contract_evaluation,
        scope_public_headers=scope_public_headers,
        require_justification=require_justification,
        severity_preset=severity_preset,
        pack_paths=pack_paths,
        policy_selected_by=policy_selected_by,
        policy_selected_path=policy_selected_path,
        policy_selected_sha=policy_selected_sha,
    )
    # ADR-068 §3 #23 / ADR-049 D7: fold `.abicheck.yml`'s `policy.overrides`
    # in at PROJECT_CONFIG tier, after the pack fold above (see
    # `apply_lower_precedence_overrides`'s docstring for why ordering matters).
    from .workflows import policy_file as _policy_file_mod

    _pf0 = pf
    try:
        pf = _policy_file_mod.merge_project_config_policy_overrides(
            pf, base_policy=policy, project_cfg=project_cfg, project_path=cfg_path
        )
    except PolicyError as e:
        raise click.BadParameter(str(e), param_hint="--policy") from e
    for w in _policy_file_mod.project_config_policy_downgrade_warnings(  # round 9/10
        _pf0, pf, project_path=cfg_path
    ):
        click.echo(f"Warning: {w}", err=True)
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
            old,
            new,
            collect_mode,
            extra_changes,
            None,
            None,
            None,
            None,
            policy_file=pf,
        )
    )

    # ADR-068 D3 (Phase 2d): the candidate-only --abi3 audit rides the same extra_changes channel every other externally-produced finding uses, so policy/suppression/verdict score it (security review of PR #1123).
    extra_changes, _abi3_failure = _enrichment.fold_abi3_into_extra_changes(
        extra_changes, new, _enrich.abi3_floor, new_input.name
    )

    # --post-manifest: scope the comparison to the POST manifest's committed
    # `pp_*`/ufunc-loop surface (private __pp_* kernel churn is demoted).
    post_manifest_allowlist = _resolve_post_manifest_allowlist(
        post_manifest_path, old, new
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
    # Reporting reads the severity config only under the severity exit scheme;
    # resolved once here rather than re-spelled at each of the five consumers.
    report_severity = (
        sev_config if resolved_cfg.exit_code_scheme == "severity" else None
    )
    # One Semantic Pipeline plan, 4B: use evaluation_config's resolved
    # contract.mode -- but only under contract_evaluation itself, since a
    # --pack-only run resolves a non-None config with a concrete mode too.
    resolved_contract_mode = (
        evaluation_config.contract.mode
        if evaluation_config is not None and contract_evaluation
        else contract_mode
    )
    from .service import compare_snapshots

    # ADR-020b / ADR-068 D5: `deployment:` (former `--env-matrix FILE`) is
    # config-only now -- `resolved_cfg.deployment` is the single already-
    # resolved `EnvironmentMatrix`, sourced straight from `.abicheck.yml`,
    # no CLI override, no separate file to load here.
    env_matrix = resolved_cfg.deployment
    try:
        deadline.check()  # same boundary check as the resolve stage above
        result = compare_snapshots(
            old,
            new,
            suppression=suppression,
            policy=policy,
            policy_file=pf,
            env_matrix=env_matrix,
            scope_to_public_surface=scope_public_headers,
            force_public_symbols=force_public,
            extra_changes=extra_changes,
            pattern_verdicts=True,
            collapse_versioned_symbols=collapse_versioned_symbols,
            public_surface_allowlist=post_manifest_allowlist,
            diagnostic_comparison=diagnostic_comparison,
            contract_evaluation=contract_evaluation,
            contract_mode=resolved_contract_mode,
        )
    except (ProfileMismatchError, ScopeMismatchError) as exc:
        report_not_comparable_and_exit(
            exc, old, new, fmt=fmt, output=output, secondary_writes=secondary_writes
        )
    except deadline.DeadlineExceeded as exc:
        # ADR-068 §3 #19: budget expired during classification itself (the
        # automatic cross-source/pattern/preprocessor scans, ADR-068 D4/D5,
        # can still run `clang -E`) rather than during resolution above --
        # same axis, same exit code, caught separately only because `old`/
        # `new` already resolved by this point and resolution's own except
        # above cannot see this call.
        _exit_on_budget_overflow(
            exc,
            budget,
            f"'{old.library}'",
            old.library,
            old.version,
            new.version,
            fmt=fmt,
            output=output,
            secondary_writes=secondary_writes,
        )
    _enrichment.report_abi3_evidence_contract_error(
        result, _abi3_failure
    )  # ADR-068 exit-7 axis
    # ADR-068 §3 #28's floor; `side_is_live` owns "did this run extract it?"
    # for both `compare` forms -- its docstring has the whole account.
    from .workflows.input_resolution import side_is_live

    _enrichment.report_depth_evidence_contract_error(
        result,
        depth,
        old,
        new,
        old_is_live=side_is_live(old_input, had_raw_evidence=old_had_raw_evidence),
        new_is_live=side_is_live(new_input, had_raw_evidence=new_had_raw_evidence),
    )
    _report_compare_result(
        ctx,
        result,
        old,
        new,
        old_input=old_input,
        new_input=new_input,
        resolved_cfg=resolved_cfg,
        evaluation_config=evaluation_config,
        sev_config=sev_config,
        report_severity=report_severity,
        layer_coverage_rows=layer_coverage_rows,
        evidence_metrics=evidence_metrics,
        extra_changes=extra_changes,
        explain_patterns=explain_patterns,
        show_redundant=show_redundant,
        show_filtered=show_filtered,
        contract_evaluation=contract_evaluation,
        policy=policy,
        pf=pf,
        used_by_apps=used_by_apps,
        required_symbols=required_symbols,
        used_by_old_input=used_by_old_input,
        used_by_new_input=used_by_new_input,
        suppression=suppression,
        audit_suppressions=audit_suppressions,
        fmt=fmt,
        output=output,
        show_only=show_only,
        report_mode=report_mode,
        show_impact=show_impact,
        demangle_explicit=demangle_explicit,
        follow_deps=follow_deps,
        secondary_writes=secondary_writes,
        require_complete_analysis=require_complete_analysis,
        require_complete_analysis_stated=require_complete_analysis_stated,
        depth=depth,
        use_cases_manifest=use_cases_manifest,
        project_config_path=cfg_path,
        project_config_sha256=cfg_sha,
    )
