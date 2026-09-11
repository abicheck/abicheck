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

"""``abicheck compare --no-baseline NEW`` -- the CLI-layer half (ADR-068 D2,
plan §6 Phase 2e).

Dispatched from :func:`abicheck.frontends.cli.commands.compare.compare_cmd`
*before* any of the two-sided pipeline runs, so a normal `compare OLD NEW`
invocation never touches this module at all (every existing two-sided
invocation stays bit-for-bit unchanged). All the candidate-side work --
resolving NEW, self-diffing it, recording OLD's `declared_absent`
acquisition state -- lives in :mod:`abicheck.workflows.no_baseline_compare`;
the report shape lives in :mod:`abicheck.report.no_baseline`. This module
only translates CLI options in, and the resulting report out.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from ....report.no_baseline import (
    NO_BASELINE_SUPPORTED_FORMATS,
    NO_BASELINE_UNSUPPORTED_FORMATS,
    audit_gate_enabled_for_severity_preset,
    render_no_baseline,
)
from ....workflows.no_baseline_compare import (
    NoBaselineCompareResult,
    candidate_is_live_artifact,
    candidate_is_stored_snapshot,
    public_header_sets_for_candidate,
    resolve_no_baseline_candidate,
    run_no_baseline_compare,
)
from ..options.contract import resolve_contract_domain, resolve_contract_evaluation
from ..options.params import _load_suppression_and_policy
from ..runtime import _safe_write_output, _write_or_echo
from .no_baseline_rulings import (
    _INERT_DESTS as _INERT_DESTS,  # noqa: F401  — re-exported: the options test asserts against these tables
    _OLD_ONLY_DESTS as _OLD_ONLY_DESTS,  # noqa: F401
    _SIDED_LABEL_DESTS as _SIDED_LABEL_DESTS,  # noqa: F401
    _SIDED_SINGLE_DESTS as _SIDED_SINGLE_DESTS,  # noqa: F401
    _UNSUPPORTED_OPTIONS as _UNSUPPORTED_OPTIONS,  # noqa: F401
    _VIEW_DEFAULTS as _VIEW_DEFAULTS,  # noqa: F401
    _reject_context_stashed_options,
    _reject_old_sided_inputs,
    _reject_unsupported_options,
    _reject_view_tokens_for_no_baseline,
    _was_given as _was_given,  # noqa: F401
)

__all__ = ["maybe_dispatch_no_baseline_compare"]

#: Formats a ``--no-baseline`` audit renders, and the two that stay a usage
#: error. Both sets, and the ruling behind the split, are owned by
#: :mod:`abicheck.report.no_baseline` -- the report layer decides what it
#: can project; this module only translates that into a Click error.
_SUPPORTED_FORMATS = NO_BASELINE_SUPPORTED_FORMATS


@dataclass(frozen=True)
class _OutputPlan:
    """Where this invocation's report goes, and in what shape."""

    fmt: str
    output: Path | None
    dry_run: bool
    secondary_writes: tuple[tuple[str, Path], ...]
    require_complete_analysis: bool
    #: ADR-068 2026-09-10 amendment: whether ``--severity-preset`` opted this
    #: audit into the audit-gate exit axis. ``False`` for every invocation
    #: that omits the flag, which is what keeps every pre-existing
    #: ``--no-baseline`` invocation's exit code unchanged.
    audit_gate_enabled: bool


@dataclass(frozen=True)
class _HeaderInputs:
    """The candidate's header surface, split for provenance tagging."""

    headers: list[Path]
    includes: list[Path]
    public_headers: list[Path]
    public_header_dirs: list[Path]


@dataclass(frozen=True)
class _EvidenceInputs:
    """The L3-L5 evidence this run may collect, and the depth pinned over it."""

    sources: Path | None
    build_info: Path | None
    build_config: Path | None
    depth: str | None


@dataclass(frozen=True)
class _CompileChoices:
    """How the candidate's own declarations are parsed and scoped."""

    lang: str
    lang_explicit: bool
    include_dependencies: bool
    #: ``--version new=``/bare, and ``--debug-root``. Both are per-side inputs
    #: `normalize_sided_options` produces and this path used to drop silently.
    version: str
    debug_roots: list[Path]
    #: ``--include new=label:path``'s own per-path labels, which
    #: `normalize_sided_options` splits out and this path used to drop.
    include_labels: dict[Path, str] | None


@dataclass(frozen=True)
class _ContractChoices:
    """ADR-049's two resolved answers: evaluate at all, and against what."""

    mode: str | None
    evaluation: bool


@dataclass(frozen=True)
class _ScopeChoices:
    """What the audit scopes and folds, after `.abicheck.yml` is merged in.

    Resolved through the *same* ``_resolve_compare_config`` a two-sided
    ``compare`` uses, at the same precedence (CLI > config > built-in
    default). This path used to run before that resolution and never reach
    it, so an auto-discovered project config was invisible here: a malformed
    one exited 0 where ordinary ``compare`` exits 64, and a valid
    ``scope.public: false`` was silently dropped -- auditing a *different
    surface* than the same directory's ``compare`` would (Codex review, P1).
    """

    scope_to_public_surface: bool
    collapse_versioned_symbols: bool


@dataclass(frozen=True)
class _SuppressionChecks:
    """The two `.abicheck.yml` `suppression:` settings that decide whether a
    suppression *document* is acceptable at all, as opposed to which findings
    it matches.

    Carried on the resolved invocation rather than read at the load site
    because both loads -- `--dry-run`'s validation-only one and the real
    run's -- must apply them, and a preview that accepts a document the run
    rejects approves a run that cannot start. They were dropped entirely
    here until now, so `suppression.require_justification: true` was
    enforced by two-sided `compare` and silently ignored by the audit, which
    then suppressed the finding and exited 0 (Codex review, P1).
    """

    strict_suppressions: bool
    require_justification: bool


@dataclass(frozen=True)
class _ResolvedInvocation:
    """One ``--no-baseline`` invocation, already validated and resolved.

    Exists so the command body below reads as the four phases it actually
    has -- validate, resolve, run, report -- rather than as one 160-line
    sequence in which a guard and a resolution step look alike. Frozen, and
    holding only values (no ``kwargs`` dict), so a later phase cannot reach
    back for a raw parameter the validation phase already ruled on.

    Grouped by *lifecycle* rather than kept as one flat record: each nested
    struct is consumed by a different phase (headers and evidence by the
    resolve step, contract and compile choices by the run, the output plan
    by the report), which is also what keeps any one of them small enough
    to read at a glance.
    """

    output: _OutputPlan
    headers: _HeaderInputs
    evidence: _EvidenceInputs
    compile: _CompileChoices
    contract: _ContractChoices
    scope: _ScopeChoices
    suppression: _SuppressionChecks


def maybe_dispatch_no_baseline_compare(
    ctx: click.Context, kwargs: dict[str, Any]
) -> bool:
    """Validate and run a ``--no-baseline`` invocation if *kwargs* asked for
    one; returns ``True`` when it did (the caller must stop -- the whole
    two-sided pipeline below is skipped), ``False`` for an ordinary
    two-operand ``compare OLD NEW``.

    ADR-068 D2: ``--no-baseline`` is an explicit declaration, never inferred
    from arity -- ``compare NEW`` (one operand, no flag) and
    ``compare --no-baseline OLD NEW`` (the flag plus two operands) are both
    usage errors (exit 64), raised here rather than left to Click's own
    argument arity (which cannot express "required unless a flag is set").
    """
    no_baseline = kwargs.pop("no_baseline", False)
    if not no_baseline:
        if kwargs.get("new_input") is None:
            raise click.UsageError("Missing argument 'NEW_INPUT'.")
        return False
    if kwargs.get("new_input") is not None:
        raise click.UsageError(
            "--no-baseline takes exactly one operand (the candidate build); "
            "OLD is declared absent, so a second path is not accepted. Run "
            "`abicheck compare OLD NEW` (without --no-baseline) to compare "
            "against a real baseline."
        )
    candidate = kwargs.pop("old_input")
    kwargs.pop("new_input", None)
    if candidate.is_dir() and not candidate_is_stored_snapshot(candidate):
        # Narrowed from a blanket `is_dir()` check: a directory-backed
        # storage-v2 `ProjectSnapshot` package *is* a single artifact --
        # `resolve_input` decodes one into exactly one `AbiSnapshot`, and a
        # two-sided `compare` accepts it -- so rejecting it made the same
        # stored snapshot acceptable as a `.abi.json` file and refused in the
        # repository's own package form (Codex review, P2). What stays
        # rejected is a *release* directory of several libraries, which needs
        # the per-library fan-out this path does not have yet (plan row F-23).
        raise click.UsageError(
            "--no-baseline does not support a directory of libraries yet "
            "-- pass a single artifact (a binary, a stored snapshot, or a "
            "ProjectSnapshot package directory)."
        )
    _reject_view_tokens_for_no_baseline(kwargs)
    _run_no_baseline_compare_cmd(ctx, candidate, **kwargs)
    return True


#: The values `--view` resolves into, and each one's "nothing requested"
#: default -- anything else means a real `--view` token was given.
def _validate_no_baseline_invocation(
    ctx: click.Context, kwargs: dict[str, Any]
) -> None:
    """Refuse every invocation this path cannot honour, before any work.

    Ordered so the cheapest, most specific message wins: an unsupported
    ``--format`` names the ruling, then the two writer-coherence rules, then
    the sided-input and unimplemented-option guards. All of them are usage
    errors (exit 64) rather than silent no-ops -- see
    :data:`_UNSUPPORTED_OPTIONS` for why that inversion is the rule here.
    """
    from ....dry_run import reject_dry_run_with_output
    from ..options import reject_incoherent_secondary_writes

    fmt = kwargs.get("fmt") or "markdown"
    if fmt not in _SUPPORTED_FORMATS:
        raise click.UsageError(_unsupported_format_message(fmt))

    output = kwargs.get("output")
    dry_run = bool(kwargs.get("dry_run", False))
    # ADR-068 D4: "a complete machine-readable result must always be
    # obtainable without a second run" -- `--write` was accepted and silently
    # dropped on this path (the same silently-inert-flag class as
    # `--contract` and the evidence options), so a job asking for a JSON
    # artifact alongside a human-readable one got nothing and no warning.
    # The two coherence rules (`--dry-run` writes nothing; a secondary PATH
    # may not collide with `-o`) come from the shared guard both `compare`
    # forms use, not a second copy.
    secondary_writes = tuple(kwargs.get("secondary_writes") or ())
    reject_dry_run_with_output(dry_run, output)
    reject_incoherent_secondary_writes(
        dry_run=dry_run, output=output, secondary_writes=secondary_writes
    )
    for write_fmt, _path in secondary_writes:
        if write_fmt not in _SUPPORTED_FORMATS:
            raise click.UsageError(
                _unsupported_format_message(write_fmt, flag="--write")
            )
    _reject_old_sided_inputs(kwargs)
    _reject_unsupported_options(kwargs)
    _reject_context_stashed_options(ctx)


def _resolve_no_baseline_invocation(
    ctx: click.Context, kwargs: dict[str, Any]
) -> _ResolvedInvocation:
    """Turn already-validated CLI values into the one resolved object.

    Resolution only -- every refusal has happened in
    :func:`_validate_no_baseline_invocation` by the time this runs, so
    nothing here has to decide whether an input is allowed, only what it
    means.
    """
    headers = list(kwargs.get("headers") or ()) + list(
        kwargs.get("new_headers_only") or ()
    )
    includes = list(kwargs.get("includes") or ()) + list(
        kwargs.get("new_includes_only") or ()
    )
    public_headers, public_header_dirs = public_header_sets_for_candidate(
        headers,
        list(kwargs.get("public_headers") or ()),
        list(kwargs.get("public_header_dirs") or ()),
    )
    lang_src = ctx.get_parameter_source("lang") if ctx is not None else None

    # The project config, resolved exactly as a two-sided `compare` resolves
    # it -- same function, same auto-discovery, same CLI > config > default
    # precedence. Running before this resolution (and never reaching it) is
    # what let a malformed auto-discovered `.abicheck.yml` exit 0 here while
    # ordinary `compare` exits 64, and a valid `scope.public: false` be
    # dropped, auditing a different surface than the same directory's
    # `compare` (Codex review, P1). `_resolve_compare_config` raises
    # `click.UsageError` on a malformed document, so the loud half needs no
    # code of its own here.
    from ..project_config import resolve_project_compare_config

    _cfg_path, _project_cfg, resolved_cfg, _cfg_sha = resolve_project_compare_config(
        config=kwargs.get("config"),
        severity_preset=None,
        # The flag's own value, not a coalesced default: `_cli_flag`
        # inside consults Click's parameter *source* to tell "typed" from
        # "defaulted", which is what lets the config win when it was not
        # typed. Passing None here would read as "typed nothing".
        scope_public_headers=bool(kwargs.get("scope_public_headers", True)),
    )
    scope = _ScopeChoices(
        scope_to_public_surface=bool(resolved_cfg.scope_public),
        # CLI wins when the flag was typed; otherwise the config's own value,
        # which `resolve_compare_config` has already folded in.
        collapse_versioned_symbols=bool(
            kwargs.get("collapse_versioned_symbols")
            or resolved_cfg.collapse_versioned_symbols
        ),
    )

    # ADR-049: `--contract VALUE` is what activates the evaluator on the CLI,
    # and `auto` maps back to "no explicit domain stated" so D7's lower tiers
    # decide -- resolved through the same two helpers
    # `cli_compare_helpers.run_compare` calls, so an equivalent one-sided and
    # two-sided invocation activate identically. Before this, `--contract`
    # was parsed and documented on this path but never read at all: the
    # contract-coverage ledger never populated, so the flag was a silently
    # inert gate (a CI job relying on it got no warning that it never ran).
    contract_mode_raw = kwargs.get("contract_mode")

    return _ResolvedInvocation(
        output=_OutputPlan(
            fmt=kwargs.get("fmt") or "markdown",
            output=kwargs.get("output"),
            dry_run=bool(kwargs.get("dry_run", False)),
            secondary_writes=tuple(kwargs.get("secondary_writes") or ()),
            # rulings.py deferred-option followup: the former
            # --require-complete-analysis CLI flag is gone; .abicheck.yml's
            # assurance.require_complete (already folded into resolved_cfg
            # above, same as scope/collapse) is its only source now.
            require_complete_analysis=bool(resolved_cfg.require_complete_analysis),
            audit_gate_enabled=audit_gate_enabled_for_severity_preset(
                kwargs.get("severity_preset")
            ),
        ),
        headers=_HeaderInputs(
            headers=headers,
            includes=includes,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
        ),
        evidence=_EvidenceInputs(
            # A bare/`both=` --sources/--build-info lands on *both* per-side
            # dests (`cli_options._split_sided_single`); the validation phase
            # has already rejected an explicitly OLD-scoped one, so reading
            # the NEW dest here is exactly "the candidate's evidence".
            sources=kwargs.get("new_sources"),
            build_info=kwargs.get("new_build_info"),
            build_config=kwargs.get("build_config"),
            depth=kwargs.get("depth"),
        ),
        compile=_CompileChoices(
            lang=kwargs.get("lang") or "c++",
            lang_explicit=lang_src == click.core.ParameterSource.COMMANDLINE,
            include_dependencies=bool(kwargs.get("include_dependencies", False)),
            version=kwargs.get("new_version") or "",
            debug_roots=list(kwargs.get("debug_roots") or ())
            + list(kwargs.get("debug_roots_new") or ()),
            include_labels=kwargs.get("include_labels") or None,
        ),
        scope=scope,
        contract=_ContractChoices(
            mode=resolve_contract_domain(contract_mode_raw, ctx),
            evaluation=resolve_contract_evaluation(contract_mode_raw),
        ),
        suppression=_SuppressionChecks(
            strict_suppressions=bool(resolved_cfg.strict_suppressions),
            require_justification=bool(resolved_cfg.require_justification),
        ),
    )


def _emit_no_baseline_report(
    result: NoBaselineCompareResult, inv: _ResolvedInvocation
) -> None:
    """Render the audit in every requested format, then exit accordingly."""
    text, exit_code = render_no_baseline(
        result,
        inv.output.fmt,
        require_complete_analysis=inv.output.require_complete_analysis,
        audit_gate_enabled=inv.output.audit_gate_enabled,
    )
    _write_or_echo(inv.output.output, text)
    for write_fmt, write_path in inv.output.secondary_writes:
        # Rendered from the *same* result, never a second run -- ADR-068 D4's
        # "presentation never changes analysis" applies here exactly as it
        # does to the two-sided path, and the exit code is the primary
        # render's, identical by construction since both project one
        # document. Written through the same writer `-o/--output` uses, which
        # creates a missing parent directory and turns a write failure into a
        # clean Click error rather than a traceback on an otherwise-complete
        # run (Codex review, P2).
        rendered, _ = render_no_baseline(
            result,
            write_fmt,
            require_complete_analysis=inv.output.require_complete_analysis,
            audit_gate_enabled=inv.output.audit_gate_enabled,
        )
        _safe_write_output(write_path, rendered)
    if exit_code != 0:
        sys.exit(exit_code)


def _resolve_candidate_or_fail(candidate: Path, inv: _ResolvedInvocation) -> Any:
    """Resolve the candidate, translating extraction failure at the boundary.

    ``resolve_no_baseline_candidate`` raises framework-free errors
    (``SnapshotError`` for an unreadable/unparseable artifact, a header set
    that will not parse, a missing frontend), which is correct for a workflow
    -- but this is the CLI boundary, and without translation the exception
    escapes as a full traceback. The two-sided path already converts the same
    failures (``cli_resolve``'s own ``run_dump`` wrapper), so an identical
    input produced a clean ``Error: ...`` there and a stack trace here (Codex
    review, P2). ``ValidationError`` maps to a usage error (exit 64) and
    ``SnapshotError`` to a plain operational failure, matching that wrapper
    exactly rather than inventing a second mapping.
    """
    from ....errors import SnapshotError, ValidationError

    try:
        return resolve_no_baseline_candidate(
            candidate,
            headers=inv.headers.headers,
            includes=inv.headers.includes,
            lang=inv.compile.lang,
            lang_explicit=inv.compile.lang_explicit,
            public_headers=inv.headers.public_headers,
            public_header_dirs=inv.headers.public_header_dirs,
            sources=inv.evidence.sources,
            build_info=inv.evidence.build_info,
            build_config=inv.evidence.build_config,
            depth=inv.evidence.depth,
            version=inv.compile.version,
            debug_roots=inv.compile.debug_roots,
            include_labels=inv.compile.include_labels,
            include_dependencies=inv.compile.include_dependencies,
        )
    except ValidationError as exc:
        raise click.UsageError(str(exc)) from exc
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


def _run_no_baseline_compare_cmd(
    ctx: click.Context, candidate: Path, **kwargs: Any
) -> None:
    """Run and report a ``compare --no-baseline`` audit of *candidate*.

    Four phases, one each: refuse what this path cannot honour, resolve what
    it can, run the audit, report it.
    """
    _validate_no_baseline_invocation(ctx, kwargs)
    inv = _resolve_no_baseline_invocation(ctx, kwargs)

    if inv.output.dry_run:
        from ....dry_run import emit_dry_run
        from ..no_baseline_dry_run import build_no_baseline_dry_run_result

        # Load the read-only policy documents *before* previewing. `--dry-run`
        # promises to resolve and validate the invocation, and these are part
        # of it: a malformed `--suppress`/`--policy-file` is exit 64 on the
        # real run, so a preview that exits 0 approves a run that cannot
        # start (Codex review, P2). Cheap and side-effect-free -- reading two
        # config files is not the analysis `--dry-run` exists to skip -- and
        # the result is discarded, since the preview needs the *validation*,
        # not the rules.
        _load_suppression_and_policy(
            kwargs.get("suppress"),
            kwargs.get("policy") or "strict_abi",
            kwargs.get("policy_file_path"),
            strict_suppressions=inv.suppression.strict_suppressions,
            require_justification=inv.suppression.require_justification,
        )
        emit_dry_run(
            build_no_baseline_dry_run_result(
                candidate=candidate,
                depth=inv.evidence.depth,
                headers=tuple(inv.headers.headers),
                includes=tuple(inv.headers.includes),
                public_header_dirs=tuple(inv.headers.public_header_dirs),
                sources=inv.evidence.sources,
                build_info=inv.evidence.build_info,
                fmt=inv.output.fmt,
                contract_mode=kwargs.get("contract_mode"),
                # The same carve-out the real run applies below, from the
                # same helper -- so the preview and the run can never
                # disagree about whether the depth floor bites.
                candidate_is_live=candidate_is_live_artifact(
                    candidate,
                    sources=inv.evidence.sources,
                    build_info=inv.evidence.build_info,
                ),
            )
        )

    new_snapshot = _resolve_candidate_or_fail(candidate, inv)

    suppression, policy_file_obj = _load_suppression_and_policy(
        kwargs.get("suppress"),
        kwargs.get("policy") or "strict_abi",
        kwargs.get("policy_file_path"),
        strict_suppressions=inv.suppression.strict_suppressions,
        require_justification=inv.suppression.require_justification,
    )

    result = run_no_baseline_compare(
        new_snapshot,
        suppression=suppression,
        policy=kwargs.get("policy") or "strict_abi",
        policy_file=policy_file_obj,
        scope_to_public_surface=inv.scope.scope_to_public_surface,
        # ADR-068 D4/Phase 5: pattern-verdict modulation is unconditional on
        # every `compare` path now (no `--pattern-verdicts` flag exists any
        # more) -- this audit-only path gets the identical treatment.
        pattern_verdicts=True,
        collapse_versioned_symbols=inv.scope.collapse_versioned_symbols,
        contract_evaluation=inv.contract.evaluation,
        contract_mode=inv.contract.mode,
        # ADR-064's exit-7 axis: a pinned --depth build/source that this
        # run's evidence did not reach. Recorded by the workflow (after
        # classification, the same point the two-sided native CLI path
        # records it) rather than here, so a front end cannot pick up the
        # audit and forget the orthogonal axis.
        depth=inv.evidence.depth,
        candidate_is_live=candidate_is_live_artifact(
            candidate,
            sources=inv.evidence.sources,
            build_info=inv.evidence.build_info,
        ),
    )

    _emit_no_baseline_report(result, inv)


def _unsupported_format_message(fmt: str, *, flag: str = "--format") -> str:
    """The usage error for a ``--format`` this audit does not render.

    *flag* names the option the rejected value came from (``--format`` or
    ``--write``), so the message quotes what the user typed.

    Names the ruling rather than promising a later phase: ``html`` and
    ``review`` are narrative renderings of a *comparison* (a verdict badge,
    an OLD -> NEW headline, a release recommendation), none of which a
    single-build audit has -- see :data:`abicheck.report.no_baseline.
    NO_BASELINE_UNSUPPORTED_FORMATS` for the full reasoning.
    """
    supported = "/".join(sorted(_SUPPORTED_FORMATS))
    if fmt in NO_BASELINE_UNSUPPORTED_FORMATS:
        return (
            f"{flag} {fmt} is not available with --no-baseline: it renders a "
            "compatibility comparison (verdict, OLD -> NEW counts, release "
            "recommendation), and a single-build audit has none of those "
            f"(ADR-068 D2). Use one of {supported} instead; oneline is the "
            "closest equivalent to a review digest."
        )
    return (
        f"--no-baseline does not support {flag} {fmt} -- only {supported} "
        "are available for an audit report."
    )
