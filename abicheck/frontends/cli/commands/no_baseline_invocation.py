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
"""One ``compare --no-baseline`` invocation, validated and resolved.

Shared by both shapes of the audit -- the scalar ``compare --no-baseline
FILE`` (:mod:`.compare_no_baseline`) and the N-library ``compare
--no-baseline DIR`` (:mod:`.compare_no_baseline_set`) -- so the two cannot
read the same flag, the same ``.abicheck.yml``, or the same policy documents
differently. Moved out of ``compare_no_baseline.py`` rather than imported
back from it: the scalar dispatch routes a directory operand to the set
module, and the set module importing the dispatch back would form an import
cycle.

:func:`audit_inputs` is the one translation from the resolved invocation to
the framework-free :class:`~abicheck.workflows.no_baseline_compare.
NoBaselineAuditInputs` every candidate -- a lone file or each member of a
set -- is audited with.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from ....compile_context import CompileContext
    from ....environment_matrix import EnvironmentMatrix

from ....cli_resolve import _click_notify
from ....model.sided_inputs import compose_sided_paths
from ....report.no_baseline import (
    NO_BASELINE_SUPPORTED_FORMATS,
    NO_BASELINE_UNSUPPORTED_FORMATS,
    audit_gate_enabled_for_severity_preset,
)
from ....workflows.no_baseline_compare import (
    NoBaselineAuditInputs,
    public_header_sets_for_candidate,
)
from ..options.contract import resolve_contract_domain, resolve_contract_evaluation
from ..options.params import _load_suppression_and_policy
from .no_baseline_rulings import (
    _reject_old_sided_inputs,
    _reject_unsupported_options,
)

__all__ = [
    "ResolvedNoBaselineInvocation",
    "audit_inputs",
    "load_policy_documents",
    "resolve_no_baseline_invocation",
    "scalar_unsupported_format_message",
    "validate_no_baseline_invocation",
]


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
    #: ``--exclude-header`` patterns, applied to *this* run's own resolved
    #: header list. Carried here rather than dropped: an option that reaches
    #: ``--no-baseline`` unaccounted for is silently ignored, so a user who
    #: excluded an unparseable header would get the parse failure back with
    #: no indication their flag did nothing (``test_compare_no_baseline_
    #: options.py`` exists to catch exactly that).
    exclude_headers: tuple[str, ...]
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
    #: The collect mode the same rule two-sided ``compare`` uses resolves to:
    #: ``--depth`` > ``source.method`` > inferred from the inputs > off.
    collect_mode: str


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
    #: The L2 header compile context, resolved through the same
    #: `cli_options.resolve_compile_context` every other command routes
    #: through -- so ADR-074's `-D/--define` reaches this audit's own header
    #: parse, and, with it, the `.abicheck.yml` `compile:` block this path
    #: passed as `compile=None` and therefore ignored entirely.
    context: CompileContext | None
    #: The ``debug:`` block (``compare_config_settings``): applied to the
    #: candidate exactly as two-sided ``compare`` applies it to each side.
    pdb: Path | None
    debuginfod: bool
    debuginfod_url: str | None
    dwarf_only: bool
    debug_format: str | None


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
    #: ``scope.public_symbols``: declarations forced public, the same overlay
    #: two-sided ``compare`` applies (``resolve_force_public_scope``).
    force_public_symbols: frozenset[str] = frozenset()
    #: ADR-065 D6's ``scope.on_incomplete`` and the two ``release.*``
    #: discovery settings -- config-only, read off the same resolved config.
    #: Only an N-library audit has a member scope for them to act on; a
    #: scalar audit's one-member record is complete by construction.
    on_incomplete: str = "warn"
    dso_only: bool = False
    include_private_dso: bool = False


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
class ResolvedNoBaselineInvocation:
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
    #: ADR-020b / ADR-068 D5: the `.abicheck.yml` `deployment:`-resolved
    #: `EnvironmentMatrix`, threaded through so the candidate-only
    #: declared-runtime-floor/wheel-packaging checks run on this audit too
    #: (`workflows.env_matrix_audit`) -- the same already-resolved value the
    #: two-sided `compare` path reads as `resolved_cfg.deployment` (Codex
    #: review: previously this driver had no channel for it at all).
    env_matrix: EnvironmentMatrix | None


def validate_no_baseline_invocation(
    kwargs: dict[str, Any],
    *,
    supported_formats: Collection[str] = NO_BASELINE_SUPPORTED_FORMATS,
    format_message: Callable[[str], str] | None = None,
    operand_is_set: bool = False,
) -> None:
    """Refuse every invocation this path cannot honour, before any work.

    Ordered so the cheapest, most specific message wins: an unsupported
    ``--format`` names the ruling, then the sided-input and
    unimplemented-option guards. All of them are usage errors (exit 64)
    rather than silent no-ops -- see :data:`.no_baseline_rulings.
    _UNSUPPORTED_OPTIONS` for why that inversion is the rule here.

    *supported_formats*/*format_message* are the operand shape's own: a
    scalar audit and an N-library one render different format sets.
    *operand_is_set* admits the options only a directory/package operand
    honours (``--select``/``--select-required``). The ``--variant`` ruling
    is the caller's, since it reads the Click context rather than *kwargs*.
    """
    message = format_message or scalar_unsupported_format_message
    fmt = kwargs.get("fmt") or "markdown"
    if fmt not in supported_formats:
        raise click.UsageError(message(fmt))

    # ADR-068 D4: "a complete machine-readable result must always be
    # obtainable without a second run" -- a second artifact was once accepted
    # and silently dropped on this path (the same silently-inert-flag class
    # as `--contract` and the evidence options), so a job asking for a JSON
    # artifact alongside a human-readable one got nothing and no warning.
    # Plan slice 7m: the two coherence rules that used to be re-checked here
    # (`--dry-run` writes nothing; two exports may not collide) are now
    # properties of the one export request, enforced while it is parsed and
    # at `compare`'s own callback boundary -- so what is left here is the one
    # thing only this path knows: which formats it can render.
    secondary_writes = tuple(kwargs.get("secondary_writes") or ())
    for write_fmt, _path in secondary_writes:
        if write_fmt not in supported_formats:
            raise click.UsageError(message(write_fmt))
    _reject_old_sided_inputs(kwargs)
    _reject_unsupported_options(kwargs, operand_is_set=operand_is_set)


def resolve_no_baseline_invocation(
    ctx: click.Context, kwargs: dict[str, Any]
) -> ResolvedNoBaselineInvocation:
    """Turn already-validated CLI values into the one resolved object.

    Resolution only -- every refusal has happened in
    :func:`_validate_no_baseline_invocation` by the time this runs, so
    nothing here has to decide whether an input is allowed, only what it
    means.
    """
    # NEW-side-specific first, then the both-sides value -- one rule across
    # every front end (`model.sided_inputs`).
    headers = compose_sided_paths(
        kwargs.get("headers") or (), kwargs.get("new_headers_only") or ()
    )
    includes = compose_sided_paths(
        kwargs.get("includes") or (), kwargs.get("new_includes_only") or ()
    )
    exclude_headers = tuple(kwargs.get("exclude_headers") or ())
    # The one shared L2 compile-context resolution (ADR-037 D3), folding the
    # project `compile:` block and ADR-074's own `-D/--define` by macro name.
    # `--no-baseline` previously built no context at all and passed
    # `compile=None`, so neither reached the candidate's header parse.
    #
    # `build_config` must be the *discovered* path, not the raw `--config`
    # kwarg: `merge_compile_config`'s own fallback is
    # `discover_build_config(sources)`, which is anchored to a `--sources`
    # tree and returns None without one -- so passing the bare kwarg would
    # apply `compile:` only when `--config` was typed, and silently ignore
    # the cwd-upward `.abicheck.yml` that every other `compare` shape honors
    # (CodeRabbit review). `config_explicit` stays tied to the raw kwarg, so
    # a discovered config still does not clear the `compile.compiler`
    # untrusted-executable gate.
    from ....cli_options import resolve_compile_context
    from ....config_paths import resolve_project_config

    _discovered_config = resolve_project_config(
        kwargs.get("config"), search_from=Path.cwd()
    ).path
    compile_context, merged_includes = resolve_compile_context(
        ctx,
        sysroot=None,
        nostdinc=False,
        header_backend="auto",
        includes=tuple(includes),
        build_config=_discovered_config,
        defines=tuple(kwargs.get("defines") or ()),
        config_explicit=kwargs.get("config") is not None,
    )
    includes = list(merged_includes)
    public_headers, public_header_dirs = public_header_sets_for_candidate(
        headers,
        list(kwargs.get("public_headers") or ()),
        list(kwargs.get("public_header_dirs") or ()),
    )
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
    )
    from ..contract_overlays import note_unapplied_post_manifest

    note_unapplied_post_manifest(
        _project_cfg,
        route="a --no-baseline audit",
        reason="an overlay scopes a contract across two sides",
    )
    # The config-only settings, read by the one function two-sided `compare`
    # reads them through, so the two shapes cannot differ. `--lang`, the
    # debug flags and `--source-method` have no CLI spelling left; reading
    # the removed `lang` kwarg here audited a `compile.lang: c` project as C++.
    from ....cli_compare_helpers import _resolve_compare_collect_mode
    from ....cli_compare_options import _warn_force_public_ignored
    from ....cli_helpers_compare import resolve_force_public_scope
    from ..compare_config_settings import config_run_settings

    run_settings = config_run_settings(resolved_cfg)
    force_public, _ = resolve_force_public_scope(resolved_cfg.public_symbols, None)
    _warn_force_public_ignored(force_public, bool(resolved_cfg.scope_public))
    collect_mode, _ = _resolve_compare_collect_mode(
        kwargs.get("depth"),
        run_settings.source_method,
        None,
        kwargs.get("new_sources"),
        None,
        kwargs.get("new_build_info"),
    )
    scope = _ScopeChoices(
        scope_to_public_surface=bool(resolved_cfg.scope_public),
        collapse_versioned_symbols=bool(
            kwargs.get("collapse_versioned_symbols")
            or resolved_cfg.collapse_versioned_symbols
        ),
        force_public_symbols=frozenset(force_public),
        on_incomplete=resolved_cfg.on_incomplete_scope or "warn",
        dso_only=bool(resolved_cfg.release_dso_only),
        include_private_dso=bool(resolved_cfg.release_include_private_dso),
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

    return ResolvedNoBaselineInvocation(
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
            exclude_headers=exclude_headers,
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
            collect_mode=collect_mode,
        ),
        compile=_CompileChoices(
            lang=run_settings.lang,
            lang_explicit=run_settings.lang_explicit,
            include_dependencies=bool(kwargs.get("include_dependencies", False)),
            version=kwargs.get("new_version") or "",
            debug_roots=list(kwargs.get("debug_roots") or ())
            + list(kwargs.get("debug_roots_new") or ()),
            include_labels=kwargs.get("include_labels") or None,
            context=compile_context,
            pdb=Path(run_settings.pdb_path) if run_settings.pdb_path else None,
            debuginfod=run_settings.debuginfod,
            debuginfod_url=run_settings.debuginfod_url,
            dwarf_only=run_settings.dwarf_only,
            debug_format=run_settings.effective_debug_format,
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
        # ADR-020b: config-only, no CLI kwarg -- same already-resolved
        # `EnvironmentMatrix` the two-sided `compare` path reads.
        env_matrix=resolved_cfg.deployment,
    )


def scalar_unsupported_format_message(fmt: str) -> str:
    """The usage error for an export format this audit does not render.

    One message rather than a *flag*-parameterized pair: since plan slice 7m
    every export -- the stdout one and every file one -- comes from the same
    ``-o FORMAT=DESTINATION`` operand, so there is only one option left to
    name.

    Names the ruling rather than promising a later phase: ``html`` and
    ``review`` are narrative renderings of a *comparison* (a verdict badge,
    an OLD -> NEW headline, a release recommendation), none of which a
    single-build audit has -- see :data:`abicheck.report.no_baseline.
    NO_BASELINE_UNSUPPORTED_FORMATS` for the full reasoning.
    """
    supported = "/".join(sorted(NO_BASELINE_SUPPORTED_FORMATS))
    if fmt in NO_BASELINE_UNSUPPORTED_FORMATS:
        return (
            f"-o {fmt}=... is not available with --no-baseline: it renders a "
            "compatibility comparison (verdict, OLD -> NEW counts, release "
            "recommendation), and a single-build audit has none of those"
            f". Use one of {supported} instead; oneline is the "
            "closest equivalent to a review digest."
        )
    return (
        f"--no-baseline does not support -o {fmt}=... -- only {supported} "
        "are available for an audit report."
    )


def load_policy_documents(
    inv: ResolvedNoBaselineInvocation, kwargs: dict[str, Any]
) -> tuple[Any, Any]:
    """Load ``--suppress``/``--policy-file`` under the resolved config's own
    two acceptance checks -- the one load both the dry run (validation only)
    and the real run perform, so a preview cannot approve a document the run
    rejects."""
    return _load_suppression_and_policy(
        kwargs.get("suppress"),
        kwargs.get("policy") or "strict_abi",
        kwargs.get("policy_file_path"),
        strict_suppressions=inv.suppression.strict_suppressions,
        require_justification=inv.suppression.require_justification,
    )


def audit_inputs(
    inv: ResolvedNoBaselineInvocation,
    *,
    suppression: Any,
    policy: str,
    policy_file: Any,
) -> NoBaselineAuditInputs:
    """The resolved invocation as the framework-free inputs every audited
    candidate -- a lone file, or each member of a set -- is audited with."""
    return NoBaselineAuditInputs(
        headers=tuple(inv.headers.headers),
        exclude_headers=inv.headers.exclude_headers,
        includes=tuple(inv.headers.includes),
        lang=inv.compile.lang,
        lang_explicit=inv.compile.lang_explicit,
        public_headers=tuple(inv.headers.public_headers),
        public_header_dirs=tuple(inv.headers.public_header_dirs),
        sources=inv.evidence.sources,
        build_info=inv.evidence.build_info,
        build_config=inv.evidence.build_config,
        depth=inv.evidence.depth,
        version=inv.compile.version,
        debug_roots=tuple(inv.compile.debug_roots),
        collect_mode=inv.evidence.collect_mode,
        pdb=inv.compile.pdb,
        enable_debuginfod=inv.compile.debuginfod,
        debuginfod_url=inv.compile.debuginfod_url,
        dwarf_only=inv.compile.dwarf_only,
        debug_format=inv.compile.debug_format,
        include_labels=inv.compile.include_labels,
        include_dependencies=inv.compile.include_dependencies,
        compile=inv.compile.context,
        notify=_click_notify,
        suppression=suppression,
        policy=policy,
        policy_file=policy_file,
        scope_to_public_surface=inv.scope.scope_to_public_surface,
        force_public_symbols=inv.scope.force_public_symbols,
        collapse_versioned_symbols=inv.scope.collapse_versioned_symbols,
        contract_evaluation=inv.contract.evaluation,
        contract_mode=inv.contract.mode,
        env_matrix=inv.env_matrix,
    )
