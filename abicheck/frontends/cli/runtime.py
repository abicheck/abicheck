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

"""Shared CLI runtime: verbosity, output, and the exit decision.

ADR-061 Phase 4. These are the pieces every command needs and none of them
owns -- setting up logging, writing or echoing a rendered report, and turning
a finished comparison into a process exit status. They were ``cli.py``'s,
which is why that file could not become a registration facade while they
lived there. Work that is not a terminal concern lives with its owner, so the
typed API reaches the same code: snapshot provenance in
:mod:`abicheck.workflows.provenance`, operand metadata and the same-binary
note in :mod:`abicheck.workflows.compare_classify`.

The exit path is the one worth reading: ``_exit_with_severity_or_verdict``
folds three orthogonal axes -- the compatibility verdict, the ADR-049
contract-coverage floor, and the assurance floor -- and reaches all three
through :mod:`abicheck.workflows.gate` rather than importing the policy layer
directly, so a command cannot pick up one axis and silently forget another.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Sequence
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
from ...checker_types import DiffResult
from ...cli_audit import echo_filtered_surface, echo_reconciled
from ...cli_helpers_compare import _merge_redundant_changes
from ...cli_resolve import _detect_binary_format
from ...frontends.cli import help as cli_help
from .options.export import ExportSet
from .options.params import (
    _load_suppression_and_policy as _load_suppression_and_policy,  # noqa: F401  — re-exported to keep cli import sites (test suite) stable
)

if TYPE_CHECKING:
    from ...model.change import Change
    from ...workflows.gate import EffectiveGate, SeverityConfig


_logger = logging.getLogger("abicheck")
# `extract.progress`'s logger, by name: frontends may not import `extract`
# (ADR-061), so the name is pinned equal by tests/test_progress_output.py.
_progress_logger = logging.getLogger("abicheck.progress")

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
    for logger in (_logger, _progress_logger):
        for existing in list(logger.handlers):
            if getattr(existing, _VERBOSITY_HANDLER_MARKER, False):
                logger.removeHandler(existing)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    setattr(handler, _VERBOSITY_HANDLER_MARKER, True)
    _logger.addHandler(handler)
    _logger.setLevel(logging.DEBUG if verbose else logging.WARNING)
    # Progress lines (abicheck/progress.py) are on by default -- a long
    # phase must never look like a hang -- on stderr only, with their own
    # prefix and no propagation, so they neither reach stdout nor repeat
    # through the handler above. ABICHECK_PROGRESS=0 silences them.
    progress_handler = logging.StreamHandler(sys.stderr)
    progress_handler.setFormatter(logging.Formatter("abicheck: %(message)s"))
    setattr(progress_handler, _VERBOSITY_HANDLER_MARKER, True)
    _progress_logger.addHandler(progress_handler)
    _progress_logger.propagate = False
    # INFO: progress.py emits unless ABICHECK_PROGRESS is off; DEBUG (-v):
    # it emits regardless. The switch is read there, via env_flags.
    _progress_logger.setLevel(logging.DEBUG if verbose else logging.INFO)


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
# ``gate.fail_on_removed_library`` in the release/multi-library table. ``16``
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
# ``gate.fail_on_removed_library`` in the release/multi-library table. ``16``
# continues that table's own doubling pattern one step further and sits
# outside every existing compare exit code in all three tables.
_EXIT_NOT_COMPARABLE = 16


#: Retired root commands whose replacement the error message names.
#: ``scan`` (ADR-068 Phase 6) split across ``compare``'s baseline and
#: no-baseline modes.
RETIRED_ROOT_COMMANDS: dict[str, str] = {
    "scan": (
        "`scan` was removed -- use `compare` (with a stored baseline) or "
        "`compare --no-baseline` (audit-only, no stored baseline) instead."
    ),
}


class _AbicheckGroup(_RootGroupBase):
    """Root group that maps Click *usage* errors to a dedicated exit code.

    Click exits 2 for ``UsageError`` / ``BadParameter`` (bad arguments, unknown
    options, invalid option values, missing/unreadable input paths), which
    collides with ``compare``'s documented ``2 = source break`` result. Remap
    just that code to ``_EXIT_USAGE_ERROR`` so an invalid invocation is never
    mistaken for an ABI verdict. Other ``ClickException``s (exit 1, used for
    operational failures such as malformed input or an expired strict waiver),
    and verdict exits (``SystemExit`` 2/4) are deliberately left untouched.
    """

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        """Give a retired root command a named-replacement error.

        A retired command is removed outright (no alias, no deprecation
        period); most fall through to Click's plain ``No such command``
        usage error. The ones in :data:`RETIRED_ROOT_COMMANDS` have a
        replacement that is not obvious from the name alone, so the error
        names it. Still surfaces the literal phrase ``No such command`` so it
        remains indistinguishable from every other retired command under
        ``test_cli_root_surface.py``'s generic parametrized check, and still
        exits 64 via ``_AbicheckGroup.main``'s usage-error remap below (a
        ``UsageError`` defaults to Click exit code 2).
        """
        if args and args[0] in RETIRED_ROOT_COMMANDS:
            raise click.UsageError(
                f"No such command '{args[0]}'. {RETIRED_ROOT_COMMANDS[args[0]]}"
            )
        return super().resolve_command(ctx, args)  # type: ignore[no-any-return]

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


def _load_probe_matrix_changes(
    probe_matrix_old: Path | None,
    probe_matrix_new: Path | None,
) -> list[Change] | None:
    """:func:`~abicheck.workflows.pair_evidence.load_probe_matrix_changes`, with
    a one-sided matrix reported as a CLI usage error."""
    from ...errors import ValidationError
    from ...workflows.pair_evidence import load_probe_matrix_changes

    try:
        return load_probe_matrix_changes(probe_matrix_old, probe_matrix_new)
    except ValidationError as exc:
        raise click.UsageError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Shared helpers for CLI commands
# ---------------------------------------------------------------------------


def _warn_all_suppressed(result: DiffResult) -> None:
    """Warn if a suppression file swallowed all changes."""
    total_changes = len(result.changes) + result.suppressed_count
    if (
        result.suppression_file_provided
        and total_changes > 0
        and len(result.changes) == 0
    ):
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


def emit_export_set(exports: ExportSet, render: Callable[[str], str]) -> None:
    """Write every document export in *exports*, rendering each format once.

    Plan slice 7m's emit half. Two properties are structural here rather
    than tested per command:

    * *render* is called at most once per **format**, and its result is
      reused for every target naming that format -- so two exports of one
      format are byte-identical because they are literally the same string,
      not because two renders happened to agree (plan §7 F-19).
    * targets are written in request order, and a stdout target goes through
      the identical ``_write_or_echo`` path a file target does, so "which
      destination" changes nothing but the destination.

    Directory (per-component fan-out) targets are **not** emitted here --
    they are the release fan-out's own writes, driven by the command that
    owns that fan-out.
    """
    rendered: dict[str, str] = {}
    for target in exports.documents:
        text = rendered.get(target.fmt)
        if text is None:
            text = render(target.fmt)
            rendered[target.fmt] = text
        _write_or_echo(target.destination, text)


#: The human-readable renderings the exit-code-scheme banner is written
#: alongside. It goes to stderr, so a machine format whose whole captured
#: stream is data must not receive it.
#:
#: ``terminal`` is here because it is what an ordinary `compare` now prints:
#: without it the banner explaining which exit-code scheme produced the
#: status vanished from the default output, which is the one place a reader
#: most needs it. Deliberately not `cli_compare_fold.TEXT_REPORT_FORMATS` --
#: these are different questions with different answers (``html`` takes the
#: banner and is not text-shaped; ``text`` is not a format `compare -o`
#: offers), so they are named separately rather than shared into a set that
#: would be wrong for one of them.
_EXIT_SCHEME_BANNER_FORMATS = frozenset({"markdown", "html", "review", "terminal"})


def _announce_exit_scheme(
    scheme: str,
    *,
    fmt: str = "markdown",
) -> None:
    """Announce (on stderr) which exit-code scheme the compare command uses.

    The scheme is fully automatic (ADR-064 / CLI cleanup phase two PR G2 --
    there is no manual override any more): ``severity`` whenever a severity
    setting is in effect from any source, ``legacy`` otherwise, resolved
    once by the time we get here. Kept on stderr so it never pollutes the
    report on stdout, and only for the human-readable formats — machine formats
    (json/sarif/junit) and the one-line format (``service_render.
    ONELINE_FORMAT``, ``-o oneline=...``, ``--stat``'s sole surviving
    replacement) are consumed by tooling that treats the
    whole captured stream as data, so the banner is suppressed for those too;
    the ``fmt not in {...}`` check below already covers it without a separate
    boolean, since it isn't one of the three human-readable format names.
    """
    if fmt not in _EXIT_SCHEME_BANNER_FORMATS:
        return
    if scheme == "severity":
        click.echo(
            "Exit-code scheme: severity-aware (per-category severity settings).",
            err=True,
        )
    else:
        click.echo(
            "Exit-code scheme: legacy verdict (0=compatible, 2=API break, 4=ABI break; "
            "1=the contract domain could not be closed on the evidence; with "
            ".abicheck.yml's assurance.require_complete: true, 1=incomplete analysis "
            "assurance -- both orthogonal axes that never lower a 2/4). "
            "Set a severity setting (--severity-preset, a --pack, or "
            ".abicheck.yml's severity: block) for the severity-aware scheme -- "
            "there is no manual scheme selector any more.",
            err=True,
        )


def _exit_with_severity_or_verdict(
    result: DiffResult,
    gate: EffectiveGate,
    fmt: str | None = None,
    secondary_fmts: Sequence[str] = (),
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
    `one-comparison-product.md` P3 layered `resolve_compare_exit_decision_
    with_abort_axes` on top of that same resolver (`evidence_contract_error`/
    `budget_overflow`) -- this function calls the wrapper so its own real
    process exit stays in lockstep with the report's `exit` block for those
    two axes too, exactly as it already was for the three PR G1 axes.
    """
    # ADR-061 Phase 4 item 4: one workflow-layer surface for the whole
    # process response, not three policy imports a frontend could fold two of
    # and forget the third. See `workflows/gate.py`.
    from ...workflows.gate import (
        announce_coverage_floor,
        assurance_floor_diagnostic,
        resolve_compare_exit_decision_with_abort_axes,
    )

    decision = resolve_compare_exit_decision_with_abort_axes(result, gate)
    announce_coverage_floor(
        result,
        base_exit=decision.compatibility_contribution,
        fmt=fmt,
        secondary_fmts=secondary_fmts,
    )
    # The pre-assurance exit is what the diagnostic's own wording describes
    # ("floored to"/"contributes, below the compatibility axis's own exit"),
    # so it excludes the assurance contribution itself rather than reading
    # `decision.code` (which, when assurance is the winning axis, would be
    # self-referential).
    pre_assurance_exit = max(
        decision.compatibility_contribution,
        decision.contract_coverage_contribution,
    )
    diagnostic = assurance_floor_diagnostic(
        result,
        require_complete=gate.require_complete_analysis,
        base_exit=pre_assurance_exit,
    )
    if diagnostic is not None:
        click.echo(diagnostic, err=True)
    # ADR-067 D6's additions-review axis is one of the decision's own
    # contributions, so the process exit and the report's `exit.code` are
    # the same number.
    if decision.code != 0:
        sys.exit(decision.code)


def _log_one_side_debug(
    label: str,
    binary: Path,
    droots: list[Path],
    *,
    debuginfod: bool,
    debuginfod_url: str | None,
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
    old_input: Path,
    new_input: Path,
    resolved_old_debug: list[Path],
    resolved_new_debug: list[Path],
    *,
    debuginfod: bool,
    debuginfod_url: str | None,
) -> None:
    """Resolve and log per-side debug info (debug roots / debuginfod), if any."""
    if not (resolved_old_debug or resolved_new_debug or debuginfod):
        return
    _log_one_side_debug(
        "old",
        old_input,
        resolved_old_debug,
        debuginfod=debuginfod,
        debuginfod_url=debuginfod_url,
    )
    _log_one_side_debug(
        "new",
        new_input,
        resolved_new_debug,
        debuginfod=debuginfod,
        debuginfod_url=debuginfod_url,
    )


def _finalize_compare_result(
    result: DiffResult,
    *,
    show_redundant: bool,
    severity_config: SeverityConfig | None = None,
    contract_evaluation: bool = False,
) -> None:
    """Emit redundancy/filter/suppression output for a classified result.

    Operand metadata and the same-binary note are attached by the shared
    classification (``workflows.compare_classify``), from the pre-embed
    operand paths the CLI hands it.
    """
    if show_redundant and result.redundant_changes:
        _merge_redundant_changes(result)
    # Plan slice 7o: both ledgers are unconditional. They used to be gated
    # on `--view filtered`, which made an exclusion invisible unless a token
    # was typed -- ADR-067's record-before-disposing accounting rule is that
    # a disposition is part of the result, not a display preference. Each
    # still renders nothing at all when its own collection is empty, so a
    # run with no scoping and no reconciliation is unchanged.
    #
    # Both ledgers demangle per row, and this runs *before*
    # `_render_compare_report` reaches `build_report_document`'s own batch,
    # so that later cache fill is too late to help here: without this one
    # batched call a host with no in-process `cxxfilt` forks a `c++filt`
    # per distinct symbol in the loops below (Codex review, PR #1284).
    if result.out_of_surface_changes or result.reconciled_changes:
        from ...reporter import prewarm_change_demangling

        prewarm_change_demangling(result)
    if result.out_of_surface_changes:
        echo_filtered_surface(result, contract_evaluation=contract_evaluation)
    if result.reconciled_changes:
        echo_reconciled(result, contract_evaluation=contract_evaluation)

    # The scoping fallback warning goes to stderr so it never corrupts the
    # machine-readable payload on stdout (which carries scope_resolved /
    # manual_review_required for programmatic consumers).
    if result.scope_to_public_surface and not result.scope_resolved:
        click.echo(
            "Warning: public-header scoping could not resolve the public "
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
    # its own at all; the Action owns the job summary.


# ── ADR-037 D7: input-type dispatch for `compare` ────────────────────────────
# `compare` accepts a single .so / snapshot, a directory, or a package. Set
# inputs (directory/package) fan out to a per-library comparison (the former
# `compare-release`); an application/PIE operand is rejected with a hint at
# `appcompat`. The set-only fan-out flags are a no-op-with-warning on single
# inputs.
