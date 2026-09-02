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

"""Shared CLI runtime: verbosity, output, provenance, and the exit decision.

ADR-061 Phase 4. These are the pieces every command needs and none of them
owns -- setting up logging, writing or echoing a rendered report, stamping
provenance onto a snapshot, and turning a finished comparison into a process
exit status. They were ``cli.py``'s, which is why that file could not become a
registration facade while they lived there.

The exit path is the one worth reading: ``_exit_with_severity_or_verdict``
folds three orthogonal axes -- the compatibility verdict, the ADR-049
contract-coverage floor, and the assurance floor -- and reaches all three
through :mod:`abicheck.workflows.gate` rather than importing the policy layer
directly, so a command cannot pick up one axis and silently forget another.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

# rich-click renders the (large) option lists in named panels for progressive
# disclosure (G21.8 / collapse M1). We keep the plain ``click`` API (so the
# module type-checks against click's stubs) and only base the root group on
# ``RichGroup`` — that alone makes ``cls=_AbicheckGroup`` render the rich panels
# (and RichGroup.command produces RichCommand subcommands). Fall back to plain
# click.Group if rich-click is somehow unavailable so the CLI never hard-fails.
try:
    from rich_click import RichGroup as _RootGroupBase
except ImportError:  # pragma: no cover - rich-click is a declared dependency
    _RootGroupBase = click.Group  # type: ignore[assignment,misc]

# Both are defined in `checker_types` (the model ring); `checker` merely
# re-exports them, and reaching for them there was a `frontends -> compare`
# import for two value types.
from ...checker_types import DiffResult, LibraryMetadata
from ...cli_audit import echo_filtered_surface, echo_reconciled
from ...cli_helpers_compare import (  # noqa: F401  — re-exported to keep cli import sites stable
    _build_match_map as _build_match_map,
    _canonical_library_key as _canonical_library_key,
    _collect_additions as _collect_additions,
    _collect_force_public_symbols as _collect_force_public_symbols,
    _collect_release_inputs as _collect_release_inputs,
    _merge_gcc_options as _merge_gcc_options,
    _merge_redundant_changes as _merge_redundant_changes,
    _provenance_timestamp as _provenance_timestamp,
    _resolve_build_context_flags as _resolve_build_context_flags,
    _resolve_per_side_options as _resolve_per_side_options,
    _resolve_severity as _resolve_severity,
    _version_sort_key as _version_sort_key,
    _warn_ignored_flags as _warn_ignored_flags,
)
from ...cli_resolve import (
    _detect_binary_format,
    _sniff_text_format,
)
from ...frontends.cli import help as cli_help
from .options.params import (
    _load_suppression_and_policy as _load_suppression_and_policy,  # noqa: F401  — re-exported to keep cli import sites (test suite) stable
)

if TYPE_CHECKING:
    from ...checker_types import Change
    from ...workflows.extraction import DebugArtifact
    from ...workflows.gate import SeverityConfig

from ...model import AbiSnapshot

_logger = logging.getLogger("abicheck")

# Marker attribute (P3, CLI-audit) stamped on every handler `_setup_verbosity`
# installs, so a repeated call within one process (the `compare-release`
# fan-out invokes each library comparison's CLI entry point in-process, and
# any test harness that calls a command function more than once in the same
# session does too) can find and remove its own prior handler before adding a
# new one. Without this, `_logger.addHandler` accumulates one duplicate
# `StreamHandler(sys.stderr)` per call, so every `-v`/warning message after
# the first invocation prints once per accumulated handler.
_VERBOSITY_HANDLER_MARKER = "_abicheck_verbosity_handler"


def _setup_verbosity(verbose: bool) -> None:
    """Configure logging verbosity for native commands.

    Idempotent (P3, CLI-audit): removes any handler this function previously
    installed (identified via ``_VERBOSITY_HANDLER_MARKER``) before adding a
    new one, so calling it more than once in the same process — as the
    ``compare-release`` fan-out and some test harnesses do — never
    accumulates duplicate stderr handlers that would each re-emit the same
    log line.
    """
    for existing in list(_logger.handlers):
        if getattr(existing, _VERBOSITY_HANDLER_MARKER, False):
            _logger.removeHandler(existing)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    setattr(handler, _VERBOSITY_HANDLER_MARKER, True)
    _logger.addHandler(handler)
    _logger.setLevel(logging.DEBUG if verbose else logging.WARNING)


def _safe_write_output(output: Path, text: str) -> None:
    """Write *text* to *output*, creating parent directories as needed."""
    try:
        parent = output.parent
        if not parent.exists():
            click.echo(f"Creating output directory: {parent}", err=True)
            parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"Cannot write to {output}: {exc}") from exc


def _stamp_provenance(
    snap: AbiSnapshot,
    *,
    git_tag: str | None,
    build_id: str | None,
    no_git: bool,
) -> None:
    """Fill provenance metadata on a snapshot (mutates in place).

    ``created_at`` honours ``SOURCE_DATE_EPOCH`` (the reproducible-builds
    standard): when set to a Unix timestamp, that fixed time is used instead of
    the wall clock, so two dumps of an identical library are byte-identical —
    enabling content-addressable caching and reproducible-build verification.
    An unset or malformed value falls back to the current time.
    """
    import os
    import subprocess

    snap.created_at = _provenance_timestamp(os.environ.get("SOURCE_DATE_EPOCH"))
    snap.git_tag = git_tag
    snap.build_id = build_id

    if not no_git:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if result.returncode == 0:
                snap.git_commit = result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # git not available or not a repo — leave as None


def _collect_metadata(path: Path) -> LibraryMetadata | None:
    """Compute SHA-256 and file size for a library artifact, or ``None`` for a text-based snapshot/manifest (JSON, Perl dump, ``Module.symvers``) -- not a binary, so a same-binary comparison must never claim it."""
    text_fmt = _sniff_text_format(path)
    if text_fmt in ("json", "perl", "symvers"):
        return None

    import hashlib

    data = path.read_bytes()
    return LibraryMetadata(
        path=str(path),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


# Exit code for an invalid invocation (bad arguments, unknown option, invalid
# option value, unreadable/unrecognised input path). Chosen as sysexits.h
# ``EX_USAGE`` so it sits *outside* the compare/compat result space
# {0, 1, 2, 4} — a CI script can therefore tell "you called me wrong" apart
# from a real ABI verdict. Click defaults ``UsageError`` to exit 2, which
# collides with ``compare``'s documented "2 = source break"; this remaps it.
_EXIT_USAGE_ERROR = 64

# Exit code for a single-library ``compare`` whose OLD/NEW snapshots failed
# ADR-050 D2's comparability gate (a ProfileMismatchError/ScopeMismatchError
# — the two snapshots were not extracted under a comparable contract, so no
# verdict was ever produced). Identical across the legacy (0/2/4) and
# severity-aware (0/1/2/4) single-library schemes, since the gate runs before
# either classification, and deliberately not ``8`` — that already means
# ``--fail-on-removed-library`` in the release/multi-library table. ``16``
# continues that table's own doubling pattern one step further and sits
# outside every existing compare exit code in all three tables.
_EXIT_NOT_COMPARABLE = 16

# Exit code for an invalid invocation (bad arguments, unknown option, invalid
# option value, unreadable/unrecognised input path). Chosen as sysexits.h
# ``EX_USAGE`` so it sits *outside* the compare/compat result space
# {0, 1, 2, 4} — a CI script can therefore tell "you called me wrong" apart
# from a real ABI verdict. Click defaults ``UsageError`` to exit 2, which
# collides with ``compare``'s documented "2 = source break"; this remaps it.
_EXIT_USAGE_ERROR = 64

# Exit code for a single-library ``compare`` whose OLD/NEW snapshots failed
# ADR-050 D2's comparability gate (a ProfileMismatchError/ScopeMismatchError
# — the two snapshots were not extracted under a comparable contract, so no
# verdict was ever produced). Identical across the legacy (0/2/4) and
# severity-aware (0/1/2/4) single-library schemes, since the gate runs before
# either classification, and deliberately not ``8`` — that already means
# ``--fail-on-removed-library`` in the release/multi-library table. ``16``
# continues that table's own doubling pattern one step further and sits
# outside every existing compare exit code in all three tables.
_EXIT_NOT_COMPARABLE = 16


class _AbicheckGroup(_RootGroupBase):
    """Root group that maps Click *usage* errors to a dedicated exit code.

    Click exits 2 for ``UsageError`` / ``BadParameter`` (bad arguments, unknown
    options, invalid option values, missing/unreadable input paths), which
    collides with ``compare``'s documented ``2 = source break`` result. Remap
    just that code to ``_EXIT_USAGE_ERROR`` so an invalid invocation is never
    mistaken for an ABI verdict. Other ``ClickException``s (exit 1, used for
    operational failures such as malformed input or an expired strict waiver),
    verdict exits (``SystemExit`` 2/4), and the ``compat`` error scheme (3–11)
    are deliberately left untouched.
    """

    def main(self, *args: Any, standalone_mode: bool = True, **kwargs: Any) -> Any:  # type: ignore[override]
        """Run the group, remapping only Click's usage exit per the class note."""
        # Call plain click's main (not rich-click's RichGroup.main, our direct
        # super), because rich-click's main renders and exits on a ClickException
        # itself — which would bypass the usage-error→64 remap below. Help still
        # renders richly: that goes through RichCommand.format_help, invoked by
        # click's main during --help handling regardless of which main runs.
        if not standalone_mode:
            return click.Group.main(self, *args, standalone_mode=False, **kwargs)  # type: ignore[call-overload]
        try:
            click.Group.main(self, *args, standalone_mode=False, **kwargs)  # type: ignore[call-overload]
        except click.exceptions.Abort:
            click.echo("Aborted!", err=True)
            sys.exit(1)
        except click.exceptions.ClickException as exc:
            exc.show()
            # Only Click's usage-error code (2) collides with a compare verdict.
            sys.exit(_EXIT_USAGE_ERROR if exc.exit_code == 2 else exc.exit_code)
        else:
            sys.exit(0)


cli_help.configure_rich_help()  # register --help option-group panels (G21.8 / M1)


def _resolve_debug_artifact(
    so_path: Path,
    debug_roots: tuple[Path, ...],
    debuginfod: bool,
    debuginfod_url: str | None,
) -> DebugArtifact | None:
    """Resolve optional separate debug artifacts for dump."""
    from ...workflows.extraction import resolve_debug_info

    return resolve_debug_info(
        so_path,
        debug_roots=list(debug_roots) or None,
        enable_debuginfod=debuginfod,
        debuginfod_urls=[debuginfod_url] if debuginfod_url else None,
    )


def _validate_show_only(
    ctx: click.Context, param: click.Parameter, value: str | None,
) -> str | None:
    """Eagerly validate --show-only tokens so invalid ones surface early."""
    if value is None:
        return None
    from ...reporter import ShowOnlyFilter
    try:
        ShowOnlyFilter.parse(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc
    return value


def _render_output(
    fmt: str,
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot | None = None,
    *,
    follow_deps: bool = False,
    show_only: str | None = None,
    report_mode: str = "full",
    show_impact: bool = False,
    severity_config: SeverityConfig | None = None,
    demangle: bool = False,
    contract_evaluation: bool = False,
    require_complete_analysis: bool = False,
) -> str:
    """Render comparison result in the requested output format.

    No ``stat``/``show_recommendation`` parameters (CLI cleanup phase two,
    PR 1): the one-line summary is reached only via ``fmt ==
    service_render.ONELINE_FORMAT`` (the built-in ``quick`` --profile's own
    injection). The release recommendation is unconditional for every CLI
    invocation -- achieved by explicitly passing ``show_recommendation=True``
    below, not by changing :func:`service.render_output`'s own default
    (which stays ``False``, the pre-removal Tier-2 Python API default, per
    Codex review, fresh evidence -- a direct caller that omits the keyword
    must keep getting the behaviour it always got).
    """
    from ...service import render_output
    return render_output(
        fmt, result, old, new,
        follow_deps=follow_deps, show_only=show_only,
        report_mode=report_mode, show_impact=show_impact,
        severity_config=severity_config,
        demangle=demangle,
        contract_evaluation=contract_evaluation,
        show_recommendation=True,
        require_complete_analysis=require_complete_analysis,
    )


def _load_probe_matrix_changes(
    probe_matrix_old: Path | None, probe_matrix_new: Path | None,
) -> list[Change] | None:
    """Load build-config matrix snapshots and return diff_matrix() findings.

    These findings (CXX_STANDARD_FLOOR_RAISED, API_DEPENDS_ON_CONSUMER_ENV,
    BEHAVIOURAL_DEFAULT_CHANGED) need multi-configuration inputs the plain
    compare() does not have, so they are computed here and merged in (G2).
    """
    if probe_matrix_old is None and probe_matrix_new is None:
        return None
    if probe_matrix_old is None or probe_matrix_new is None:
        raise click.UsageError(
            "--probe-matrix needs both sides: --probe-matrix old=… --probe-matrix new=…"
        )
    from ...workflows.findings import diff_matrix, load_matrix_snapshot

    old_matrix = load_matrix_snapshot(probe_matrix_old)
    new_matrix = load_matrix_snapshot(probe_matrix_new)
    return list(diff_matrix(old_matrix, new_matrix))


# ---------------------------------------------------------------------------
# Shared helpers for CLI commands
# ---------------------------------------------------------------------------


def _warn_all_suppressed(result: DiffResult) -> None:
    """Warn if a suppression file swallowed all changes."""
    total_changes = len(result.changes) + result.suppressed_count
    if result.suppression_file_provided and total_changes > 0 and len(result.changes) == 0:
        click.echo(
            "Warning: all ABI changes were suppressed by the suppression file. "
            "Verify your suppression rules are not too broad.",
            err=True,
        )






def _write_or_echo(output: Path | None, text: str) -> None:
    """Write text to file or echo to stdout."""
    if output:
        _safe_write_output(output, text)
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(text)


def _announce_exit_scheme(
    scheme: str,
    *, fmt: str = "markdown",
) -> None:
    """Announce (on stderr) which exit-code scheme the compare command uses.

    The scheme is now explicit (ADR-037 D12 / D4: ``--exit-code-scheme`` or the
    config's ``exit_code_scheme``, with ``auto`` already resolved to ``legacy`` or
    ``severity`` by the time we get here). Kept on stderr so it never pollutes the
    report on stdout, and only for the human-readable formats — machine formats
    (json/sarif/junit) and the internal one-line format (``service_render.
    ONELINE_FORMAT``, the built-in ``quick`` --profile's sole surviving use of
    ``--stat``'s old one-line output) are consumed by tooling that treats the
    whole captured stream as data, so the banner is suppressed for those too;
    the ``fmt not in {...}`` check below already covers it without a separate
    boolean, since it isn't one of the three human-readable format names.
    """
    if fmt not in {"markdown", "html", "review"}:
        return
    if scheme == "severity":
        click.echo(
            "Exit-code scheme: severity-aware (per-category severity settings).",
            err=True,
        )
    else:
        click.echo(
            "Exit-code scheme: legacy verdict (0=compatible, 2=API break, 4=ABI break; "
            "with --contract, 1=incomplete contract coverage; with "
            "--require-complete-analysis, 1=incomplete analysis assurance -- both "
            "orthogonal axes that never lower a 2/4). "
            "Pass --exit-code-scheme severity (or a severity setting) for the "
            "severity-aware scheme.",
            err=True,
        )


def _exit_with_severity_or_verdict(
    result: DiffResult, sev_config: SeverityConfig | None, scheme: str,
    fmt: str | None = None, secondary_fmt: str | None = None,
    *, require_complete_analysis: bool = False,
) -> None:
    """Exit with the appropriate code for the resolved exit-code scheme.

    ADR-049 Phase 7 / P0.4: the contract-coverage and analysis-assurance axes
    are folded in here rather than at each call site, so a command cannot
    acquire a compatibility exit and forget an orthogonal one. Since CLI
    cleanup phase two PR G1, the fold itself is
    `exit_decision.resolve_compare_exit_decision` -- the one canonical
    resolver, rather than three separately-called `max` folds -- so a
    caller building the report's `exit` block (`reporter_contract_blocks.
    add_contract_context`) and this function's own process exit read the
    identical decision. The diagnostics below still read each axis's own
    contribution straight off the resolved `ExitDecision`, so their wording
    (and the final exit code) are unchanged from before this delegation.
    """
    # ADR-061 Phase 4 item 4: one workflow-layer surface for the whole
    # process response, not three policy imports a frontend could fold two of
    # and forget the third. See `workflows/gate.py`.
    from ...workflows.gate import (
        announce_coverage_floor,
        assurance_floor_diagnostic,
        resolve_compare_exit_decision,
    )

    decision = resolve_compare_exit_decision(
        result, sev_config, scheme,
        require_complete_analysis=require_complete_analysis,
    )
    announce_coverage_floor(
        result, base_exit=decision.compatibility_contribution,
        fmt=fmt, secondary_fmt=secondary_fmt,
    )
    # The pre-assurance exit is what the diagnostic's own wording describes
    # ("floored to"/"contributes, below the compatibility axis's own exit"),
    # so it excludes the assurance contribution itself rather than reading
    # `decision.code` (which, when assurance is the winning axis, would be
    # self-referential).
    pre_assurance_exit = max(
        decision.compatibility_contribution, decision.contract_coverage_contribution,
    )
    diagnostic = assurance_floor_diagnostic(
        result, require_complete=require_complete_analysis, base_exit=pre_assurance_exit
    )
    if diagnostic is not None:
        click.echo(diagnostic, err=True)
    if decision.code != 0:
        sys.exit(decision.code)


def _log_one_side_debug(
    label: str, binary: Path, droots: list[Path],
    *,
    debuginfod: bool, debuginfod_url: str | None,
) -> None:
    """Resolve and log debug info for a single binary side, if applicable."""
    if _detect_binary_format(binary) is None or not (droots or debuginfod):
        return
    from ...workflows.extraction import resolve_debug_info

    artifact = resolve_debug_info(
        binary,
        debug_roots=droots or None,
        enable_debuginfod=debuginfod,
        debuginfod_urls=[debuginfod_url] if debuginfod_url else None,
    )
    if artifact:
        click.echo(f"Debug info ({label}): {artifact.source}", err=True)


def _log_debug_resolution(
    old_input: Path, new_input: Path,
    resolved_old_debug: list[Path], resolved_new_debug: list[Path],
    *,
    debuginfod: bool, debuginfod_url: str | None,
) -> None:
    """Resolve and log per-side debug info (debug roots / debuginfod), if any."""
    if not (resolved_old_debug or resolved_new_debug or debuginfod):
        return
    _log_one_side_debug(
        "old", old_input, resolved_old_debug,
        debuginfod=debuginfod, debuginfod_url=debuginfod_url,
    )
    _log_one_side_debug(
        "new", new_input, resolved_new_debug,
        debuginfod=debuginfod, debuginfod_url=debuginfod_url,
    )


def _finalize_compare_result(
    result: DiffResult, metadata_old_input: Path, metadata_new_input: Path,
    *,
    show_redundant: bool, show_filtered: bool,
    severity_config: SeverityConfig | None = None,
    contract_evaluation: bool = False,
) -> None:
    """Attach metadata and emit redundancy/filter/suppression output.

    ``metadata_old_input``/``metadata_new_input`` must be the *original*
    library paths -- resolved through any GNU ld linker-script chain, but
    from *before* ``_embed_inline_source_sides`` may have rewritten
    ``old_input``/``new_input`` to a temporary embedded-snapshot ``.abi.json``
    path (Codex review). ``_collect_metadata`` returns ``None`` for a
    JSON/Perl-text path, so passing the post-embed operands here would
    silently drop ``note_if_same_binary_compared``'s byte-identical-binaries
    warning for any ``--old/new-sources``/raw ``--build-info`` comparison,
    even when the two underlying native binaries really are identical.
    Callers already resolve this same pre-embed pair for
    ``--used-by``/``--required-symbol`` scoping (``used_by_old_input``/
    ``used_by_new_input``) -- reuse that pair here rather than threading a
    third copy of the same resolution through.
    """
    # Routed through `workflows.extraction`/`workflows.gate`, not
    # `binary_utils`/`confidence` directly, and not through `service` --
    # this module is `frontends` layer under ADR-061, which may import
    # `workflows` but must never import back through the `service`/`cli`
    # compatibility facades (abicheck/frontends/AGENTS.md).
    # `note_if_same_binary_compared` lives in `workflows.gate` rather than
    # `workflows.extraction` (Codex review): it decides part of the process
    # response a completed comparison returns, not an operation performed on
    # an input, which is what `extraction.py`'s own docstring scopes it to.
    from ...workflows.extraction import resolve_linker_script_chain
    from ...workflows.gate import note_if_same_binary_compared

    def _hashable_path(p: Path) -> Path:  # a text snapshot/manifest can coincidentally match the INPUT()/GROUP() probe -- skip linker-script resolution for it (Codex review)
        return p if _sniff_text_format(p) in ("json", "perl", "symvers") else resolve_linker_script_chain(p)

    result.old_metadata = _collect_metadata(_hashable_path(metadata_old_input))
    result.new_metadata = _collect_metadata(_hashable_path(metadata_new_input))
    note_if_same_binary_compared(result)

    if show_redundant and result.redundant_changes:
        _merge_redundant_changes(result)
    if show_filtered and result.out_of_surface_changes:
        echo_filtered_surface(result, contract_evaluation=contract_evaluation)
    if show_filtered and result.reconciled_changes:
        echo_reconciled(result, contract_evaluation=contract_evaluation)

    # The scoping fallback warning goes to stderr so it never corrupts the
    # machine-readable payload on stdout (which carries scope_resolved /
    # manual_review_required for programmatic consumers).
    if result.scope_to_public_surface and not result.scope_resolved:
        click.echo(
            "Warning: --scope-public-headers could not resolve the public "
            "surface (no header-derived public symbols); fell back to the full "
            "export table. Compatibility is UNCONFIRMED — treat this result as "
            "manual-review-required, not a clean public surface.",
            err=True,
        )

    _warn_all_suppressed(result)
    # CLI cleanup phase two, PR E removed --annotate/--annotate-additions,
    # the flag that used to gate a $GITHUB_STEP_SUMMARY write here as a side
    # effect. Making that write unconditional-in-CI instead (an earlier
    # revision of this comment) was itself a real regression (Codex review,
    # fresh evidence): when this command runs through the composite Action,
    # the subprocess inherits GITHUB_ACTIONS=true/GITHUB_STEP_SUMMARY from
    # the Action's own job, so an unconditional write here double-writes
    # against action/run.sh's own, richer, INPUT_ADD_JOB_SUMMARY-gated job
    # summary (or writes one even when a caller explicitly set
    # add-job-summary: false). The CLI no longer writes a step summary on
    # its own at all -- annotations_step_summary.emit_github_step_summary
    # stays available as a public primitive for a caller invoking the CLI
    # directly outside the composite Action to call itself.


# ── ADR-037 D7: input-type dispatch for `compare` ────────────────────────────
# `compare` accepts a single .so / snapshot, a directory, or a package. Set
# inputs (directory/package) fan out to a per-library comparison (the former
# `compare-release`); an application/PIE operand is rejected with a hint at
# `appcompat`. The set-only fan-out flags are a no-op-with-warning on single
# inputs.
