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

"""``dump``'s typed entry point: one :class:`DumpRequest` in, one snapshot out.

G33 Phase 5. :func:`abicheck.service.resolve_input` has always been the single
source of truth for *turning a path into a snapshot*, but everything a real
``dump`` does around that call — inferring a collect mode, embedding inline
L3-L5 build/source evidence, walking dependencies, and enforcing that an
explicit ``--depth`` was actually reached — lived only in ``cli.py``'s
``dump_cmd``. So a non-CLI caller either re-implemented those four steps or did
without them, which is exactly why the MCP ``abi_dump`` tool accepted five
arguments where ``abicheck dump`` accepts thirty.

:func:`run_dump_request` is those steps, over the same per-input primitives
``compare`` resolves through (:mod:`abicheck.service_input_resolution`). It is
the ``dump``-shaped sibling of
:func:`abicheck.service_compare_pipeline.resolve_compare_request`, not a second
implementation of it.

**Not** in scope, deliberately: the CLI's presentation and provenance layer —
``--dry-run`` rendering, git/build-id stamping, the
``fold_dump_provenance_into_json`` write step, and the deprecation warnings.
Those describe how one front end reports a dump, not how one is produced;
``cli.py`` keeps them, the same way it keeps ``compare``'s ``click.echo``
notifier after Phase 2 unified that command's resolution.

Same mechanical note as the compare pipeline: ``service`` is looked up through
the module object at call time so ``monkeypatch.setattr(service, ...)`` keeps
working, and the function-local import keeps this module out of ``service``'s
import cycle.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .errors import ValidationError
from .workflows.artifact import ResolvedArtifactPlan
from .workflows.artifact.compile_context_gate import side_effective_compile_context
from .workflows.artifact.dump_execution_options import (
    DumpExecutionOptions as DumpExecutionOptions,
    _DumpAssuranceView as _DumpAssuranceView,
)
from .workflows.artifact.dump_request import ResolvedDumpRequest as ResolvedDumpRequest
from .workflows.artifact.execute import (
    _resolve_side_snapshot_impl,
    enforce_requested_depth,
)
from .workflows.artifact.execute_header_only import (
    execute_header_only_dump_request,
    is_header_only_evidence,
)
from .workflows.artifact.execute_source_only import execute_source_only_dump_request
from .workflows.artifact.resolve import (
    is_raw_source_tree,
    reject_hybrid_source_frontend,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .compile_context import CompileContext
    from .model import AbiSnapshot
    from .service_compare_evidence import SideEvidence
    from .workflows.contracts import DumpRequest
    from .workflows.resolved_execution_context import ResolvedExecutionContext

__all__ = [
    "DumpExecutionOptions",
    "DumpResult",
    "ResolvedDumpRequest",
    "execute_dump_request",
    "resolve_dump_request",
    "run_dump_request",
]


@dataclass(frozen=True)
class DumpResult:
    """The executed result of a :class:`ResolvedDumpRequest` — a real snapshot,
    not a preview.

    Additive sibling to :func:`run_dump_request`, which keeps returning a
    bare :class:`~abicheck.model.AbiSnapshot` unchanged — changing a public
    function's return type is a breaking Python-API change (root
    ``AGENTS.md``), coordinated separately from this additive step; see
    ``docs/contribute/plans/cli-cleanup-phase-two.md``'s PR C section.
    :func:`run_dump_request` is now a thin adapter over
    :func:`execute_dump_request`.

    ``effective_depth`` is the *achieved* evidence depth (e.g. ``"source"``,
    ``"build"``, ``"headers"``, ``"binary"``) — the same value
    ``cli_dump_helpers.fold_dump_provenance_into_dict`` derives from the
    completed snapshot, computed here the identical way.

    Storage (writing the snapshot to disk) is deliberately not part of this
    object — see this module's own docstring, "Not in scope, deliberately":
    that is CLI presentation/provenance layer, not resolution or execution.

    ``effective_includes``/``effective_compile_context`` (PR 3A, dump/scan
    resolver convergence) are the P0.3 L3→L2 fold's own resolved values —
    computed inside :func:`execute_dump_request`'s call to
    :func:`~abicheck.service_input_resolution._resolve_side_snapshot_impl`
    but, before this addition, never surfaced. A CLI-side caller with a
    post-processing hook that must agree with the primary parse (the ELF
    ``dump`` path's ADR-039 build-context collector and header-graph second
    pass) needs these, rather than re-deriving them via a second, independent
    call to the same underlying fold.

    Defaulted to ``()``/``None`` (Codex review, fresh evidence) —
    :class:`DumpResult` is exported, documented Tier-2 API surface, so
    appending *required* fields would break an external caller already
    constructing the previous three-field shape, not just callers inside
    this repo (which are already updated). The defaults never surface in
    practice: :func:`execute_dump_request` — the only real constructor —
    always supplies both explicitly.

    **Lifetime caveat, not yet relevant to any caller in this repo (Codex
    review, fresh evidence)**: when the fold ran a trusted, zero-config
    *inferred* build-system query (no existing compile database), the
    temporary build directory it seeded ``effective_includes``/
    ``effective_compile_context`` from is deleted by the time this object is
    returned — cleanup runs, deliberately, right after the primary parse has
    consumed it (see
    :func:`~abicheck.service_input_resolution._resolve_side_snapshot_impl`'s
    own docstring). These fields are therefore safe to use for *identity or
    comparison* (exactly how ``scan_engine``'s own, pre-existing pair-aware
    baseline-context-reuse decision already uses its equivalent locals — see
    ``docs/contribute/plans/cli-cleanup-phase-two.md``'s PR 3A section), but
    a caller intending to re-read a file under one of these paths (the
    post-processing-hook use case named above) cannot yet do so safely — that
    is exactly the sort of pair-aware/lifetime redesign PR 3A's "Known gaps"
    entry already scopes as its own follow-up, not settled by exposing these
    fields alone.

    ``resolved_execution_context`` (One Semantic Pipeline plan, sub-phase
    4B): ``resolved.resolved_execution_context`` (the pre-execution view),
    completed via
    :meth:`~abicheck.workflows.resolved_execution_context.
    ResolvedExecutionContext.with_assurance` once this object's own
    ``effective_depth`` -- the one fact ``dump`` can compute that ``compare``
    computes via a real
    :class:`~abicheck.analysis_assurance.AnalysisAssurance` -- is known.
    ``dump`` has no comparison pair, so there is no real ``AnalysisAssurance``
    to attach; :func:`execute_dump_request` instead builds
    :class:`_DumpAssuranceView`, a minimal object carrying exactly the three
    attributes :meth:`~abicheck.workflows.resolved_execution_context.
    EvidenceView.from_assurance` reads (``requested_depth``,
    ``effective_depth``, ``depth_satisfied``) -- see that class for why this
    is a real post-execution fact, not a synthesized stand-in. ``None`` only
    when ``resolved.resolved_execution_context`` was itself ``None`` (a
    caller that hand-built a ``ResolvedDumpRequest`` bypassing
    :func:`resolve_dump_request`).
    """

    resolved: ResolvedDumpRequest
    snapshot: AbiSnapshot
    effective_depth: str
    effective_includes: tuple[Path, ...] = ()
    effective_compile_context: CompileContext | None = None
    resolved_execution_context: ResolvedExecutionContext | None = None


def _reject_unsupported_frontends(
    request: DumpRequest, header_backend: str, evidence: SideEvidence
) -> None:
    """Reject the frontend/evidence combinations that have no extractor.

    The single-input twin of ``service_compare_pipeline._reject_unsupported_frontends``:
    ``android`` and ``hybrid`` have no real ``embed_build_source`` extractor, so
    a raw source tree needing real extraction under either is a usage error
    rather than a silently weaker snapshot.
    """
    if request.frontend.lower() == "android" and is_raw_source_tree(
        request.input.sources
    ):
        raise ValidationError(
            "the 'android' AST frontend's source-ABI replay is not yet wired "
            "into run_dump_request's inline evidence collection for a raw "
            "source tree -- pass a prebuilt evidence pack directory instead, "
            "or use has_sources=True with no inline sources/build_info."
        )
    reject_hybrid_source_frontend(
        request.depth, ((request.input, evidence),), header_backend
    )


def run_dump_request(
    request: DumpRequest,
    *,
    notify: Callable[[str], None] | None = None,
) -> AbiSnapshot:
    """Resolve *request* into one :class:`~abicheck.model.AbiSnapshot`.

    The typed Tier-2 entry point behind ``abicheck dump`` and the MCP
    ``abi_dump`` tool. Runs, in order:

    1. :meth:`DumpRequest.validate` — the same value/cross-flag rules
       :class:`~abicheck.api_types.CompareRequest` applies;
    2. the input's evidence resolution (``depth`` → collect mode, headers,
       ``dump_manifest``, merged :class:`CompileContext`);
    3. :func:`abicheck.service.resolve_input`, plus inline build/source
       embedding when the input declares ``sources``/``build_info``;
    4. ``follow_dependencies``' transitive ``DependencyInfo``, ELF only;
    5. the depth floor — an explicit ``depth`` that was requested but not
       reached raises rather than returning a weaker snapshot.

    *notify* is forwarded to :func:`abicheck.service.resolve_input` for
    user-facing progress notes ("following a linker script"); ``None`` logs
    them instead.

    A thin adapter over :func:`resolve_dump_request` + :func:`execute_dump_request`
    (CLI cleanup phase two, PR C / PR 3A) — kept returning a bare
    :class:`~abicheck.model.AbiSnapshot`, unchanged, since this is a
    documented, tested public Tier-2 entry point and changing its return
    type is a breaking Python-API change coordinated separately (root
    ``AGENTS.md``). Call :func:`execute_dump_request` directly for the
    richer :class:`DumpResult`.

    Raises:
        ValidationError: If the request fails :meth:`DumpRequest.validate`,
            names a frontend with no extractor for its evidence, or requests a
            ``depth`` the resolved snapshot did not reach.
        PlanningError: See :func:`resolve_dump_request` — raised from inside
            its own call here.
        SnapshotError: If the input cannot be loaded.
    """
    return execute_dump_request(resolve_dump_request(request), notify=notify).snapshot


def resolve_dump_request(request: DumpRequest) -> ResolvedDumpRequest:
    """Resolve *request* into a :class:`ResolvedDumpRequest` — steps 1-2 of
    :func:`run_dump_request`'s own docstring (validation, evidence
    resolution), stopping before any castxml/clang invocation or write.

    This is the function ``dump --dry-run``'s rendering path is meant to
    build from (see :class:`ResolvedDumpRequest`'s own docstring) once
    ``cli_dump_helpers.render_dump_dry_run`` is migrated to it — not
    attempted here; see ``docs/contribute/plans/cli-cleanup-phase-two.md``'s
    PR C section for what that migration still needs.

    Raises:
        ValidationError: If the request fails :meth:`DumpRequest.validate`
            or names a frontend with no extractor for its evidence.
        PlanningError: If :class:`~abicheck.workflows.plan.AnalysisPlanner`
            finds a requested evidence input no resolved collector/backend
            combination can satisfy (ADR-063 Phase 4) — e.g. a
            ``.abicheck.yml`` ``build.targets`` declaration combined with a
            pre-captured Bazel ``aquery``/``cquery`` jsonproto.
    """
    from . import service, service_compare_evidence as _sce
    from .header_utils import split_public_header_inputs
    from .model.header_ast_frontends import HEADER_AST_FRONTENDS
    from .workflows.plan import AnalysisPlanner

    request.validate()
    # ADR-063 Phase 4: reject a request no resolved collector/backend
    # combination can satisfy before any extraction runs (PlanningError),
    # rather than discovering the gap mid-run or not at all. See
    # `abicheck.workflows.plan`'s own module docstring for exactly what this
    # does and does not check. The returned `AnalysisPlan` is also what
    # feeds `ResolvedExecutionContext.from_plan` below (One Semantic
    # Pipeline plan, sub-phase 4B) -- not a second resolution.
    plan = AnalysisPlanner.resolve(request)
    # validate() accepts lang case-insensitively; the ELF dump path does
    # case-sensitive `lang == "c"` checks, so normalise here. `android` (no
    # header-AST path) falls back to "auto" for the binary dump.
    lang = request.lang.lower()
    frontend_lower = request.frontend.lower()
    header_backend = (
        frontend_lower if frontend_lower in HEADER_AST_FRONTENDS else "auto"
    )
    side = request.input
    # `None` for a source-only dump (`InputSpec.path is None`, PR 3A blocker 5):
    # there is no native artifact to sniff a format from. `validate()` above
    # already required real `sources`/`build_info`/`dump_manifest` for that
    # shape, so this is the binary-less request, not a missing-input mistake.
    fmt = service.detect_binary_format(side.path) if side.path is not None else None
    debug_format = _sce.normalized_debug_format(request)
    _sce.reject_debug_format_for_binaries(debug_format, (("input", fmt),))

    evidence = _sce.resolve_dump_request_evidence(request)
    # Mirrors the ELF `dump` CLI's own `compile_db_filter_scope_error` check
    # (`cli.py`'s `dump_cmd`) -- this is the one place in the typed pipeline
    # that knows the *resolved* collect mode a `--compile-db-filter`-shaped
    # `InputSpec.compile_db_filter` would otherwise silently disagree with
    # (PR 3A investigation, 2026-08-21; see `InputSpec.compile_db_filter`'s
    # own docstring). Shared with `resolve_compare_request`'s identical
    # per-side check (Codex review: a `CompareRequest` side reaches the exact
    # same fold/embed split, so the guard belongs in one place both call).
    _sce.reject_compile_db_filter_scope_mismatch((("input", side, evidence),))
    _reject_unsupported_frontends(request, header_backend, evidence)
    # Pinned once, here -- not a lazily-recomputed property (see
    # ResolvedDumpRequest.effective_header_backend's own comment).
    effective_header_backend = _sce.effective_frontend(evidence.compile, header_backend)
    # dumper._header_ast_parser routes ANY non-"host" frontend_context to
    # clang unconditionally, regardless of what the backend itself resolved
    # to -- so the host-only `effective_frontend` above can under-report a
    # request that will always run clang. But an *explicit*
    # `--ast-frontend castxml` (or an env-pinned one) combined with a
    # non-host context doesn't route to clang at all -- it raises
    # AstContextMissingError at execution, so claiming "clang" there would
    # be equally wrong in the other direction (Codex review, two rounds:
    # the first fix applied the clang override unconditionally, missing
    # this pinned-castxml case entirely).
    #
    # `effective_frontend_for_context` is the one shared "selection"
    # function this and `dumper._header_ast_parser` both call for that
    # prediction (`dumper._resolve_effective_ast_backend`, ADR-063 T4) --
    # this used to inline the same override-precedence and
    # context-forces-clang rules a second time, which is exactly the
    # duplicated reimplementation that made those two Codex-review rounds
    # necessary in the first place. `None` means "leave `effective_frontend`
    # above unchanged" -- either the context is "host" (nothing to predict),
    # or the request is one no single parser could satisfy, and this is a
    # best-effort preview that must never raise.
    context_backend = _sce.effective_frontend_for_context(
        evidence.compile, header_backend
    )
    if context_backend is not None:
        effective_header_backend = context_backend

    # `headers` doubles as the public-header set for provenance tagging and
    # must be split into files and directories before tagging (an unsplit
    # directory entry corrupts `scope_fingerprint`); `public_header_dirs` is
    # unioned in afterward. `depth="binary"` clears both, matching
    # `_public_header_sets`: that depth clears `evidence.headers`, but a
    # headerless dump still fingerprints these.
    public_headers, public_header_dirs = split_public_header_inputs(side.headers)
    public_header_dirs += list(side.public_header_dirs)
    if request.depth is not None and request.depth.lower() == "binary":
        public_headers, public_header_dirs = [], []

    artifact_plan = ResolvedArtifactPlan(
        binary_format=fmt,
        lang=lang,
        header_backend=header_backend,
        effective_header_backend=effective_header_backend,
        requested_depth=request.depth,
        collect_mode=evidence.collect_mode,
        public_headers=tuple(public_headers),
        public_header_dirs=tuple(public_header_dirs),
    )
    from .workflows.resolved_execution_context import ResolvedExecutionContext

    return ResolvedDumpRequest(
        request=request,
        lang=lang,
        lang_explicit=request.lang_explicit,
        header_backend=header_backend,
        effective_header_backend=effective_header_backend,
        fmt=fmt,
        debug_format=debug_format,
        requested_depth=request.depth,
        evidence=evidence,
        public_headers=tuple(public_headers),
        public_header_dirs=tuple(public_header_dirs),
        artifact_plan=artifact_plan,
        resolved_execution_context=ResolvedExecutionContext.from_plan(plan),
    )


def execute_dump_request(
    resolved: ResolvedDumpRequest,
    *,
    notify: Callable[[str], None] | None = None,
    options: DumpExecutionOptions | None = None,
) -> DumpResult:
    """Execute a :class:`ResolvedDumpRequest` — steps 3-5 of
    :func:`run_dump_request`'s own docstring (``resolve_input``, the
    dependency walk, the depth floor).

    *notify* is forwarded to :func:`abicheck.service.resolve_input` for
    user-facing progress notes ("following a linker script"); ``None`` logs
    them instead. Kept as its own keyword rather than folded into *options*
    -- it is a plain logging callback, not resolution semantics, and every
    other Tier-2 entry point in this pipeline (``run_dump_request``,
    :func:`~abicheck.service_compare_pipeline.resolve_compare_request`)
    already takes ``notify`` the same way.

    *options* (ADR-063 Track T4, "Dump request contract"; see
    :class:`DumpExecutionOptions`) folds the nine out-of-band execution
    kwargs this function used to accept directly into one typed value.
    ``None`` (the default) falls back to *resolved*'s own
    :attr:`~ResolvedDumpRequest.execution_options` when the caller resolved
    one, or ``DumpExecutionOptions()`` when neither is set -- every
    pre-existing caller is unaffected.

    A binary-less (``InputSpec.path is None``) *resolved* is dispatched to
    :func:`~abicheck.workflows.artifact.execute_source_only.
    execute_source_only_dump_request` instead (ADR-063 Track T4) -- see
    that function's own docstring.

    Raises:
        ValidationError: If *resolved* requests a ``depth`` the resolved
            snapshot did not reach.
        SnapshotError: If the input cannot be loaded.
    """
    from .dependency_info import populate_side_dependency_info
    from .evidence_depth import depth_rank, gated_source_label

    if options is None:
        options = resolved.execution_options or DumpExecutionOptions()
    request = resolved.request
    side = request.input
    if side.path is None:
        # Two binary-less shapes share this branch (workstream F S1 added
        # the first of the two): a headers-only dump (`-H api.h`, no
        # sources/build_info -- `is_header_only_evidence`) routes through
        # the real header-AST parse; every other binary-less shape (the
        # pre-existing L3-L5 `--sources`/`--build-info` dump, ADR-063 Track
        # T4's own tracked gap) keeps its original path unchanged. Neither
        # branch constructs a `DumpResult` itself -- each returns its own
        # plain outcome type, so this is the one place that builds the
        # former from either (see either sibling module's own docstring for
        # why -- avoiding a real import cycle).
        if is_header_only_evidence(resolved):
            header_outcome = execute_header_only_dump_request(resolved, options)
            return DumpResult(
                resolved=resolved,
                snapshot=header_outcome.snapshot,
                effective_depth=header_outcome.effective_depth,
                resolved_execution_context=header_outcome.resolved_execution_context,
            )
        outcome = execute_source_only_dump_request(resolved, options)
        return DumpResult(
            resolved=resolved,
            snapshot=outcome.snapshot,
            effective_depth=outcome.effective_depth,
            resolved_execution_context=outcome.resolved_execution_context,
        )

    resolution = _resolve_side_snapshot_impl(
        side,
        resolved.evidence,
        lang=resolved.lang,
        lang_explicit=resolved.lang_explicit,
        # The *bare* requested backend, unchanged -- NOT effective_header_
        # backend (Codex review, fresh evidence, reverting an earlier
        # attempt at this same line). Passing the pre-resolved concrete
        # value here was tried and found to be a real regression, not a
        # pin: `dumper._header_ast_parser`'s own `_auto_ast_fallback_
        # eligible(backend)` checks whether `backend` is *literally* the
        # string "auto" to decide whether a CastXML failure may gracefully
        # fall back to Clang -- pre-resolving "auto" to "castxml" before it
        # gets here silently disables that fallback. `effective_header_
        # backend` exists purely for *reporting* (what a future `--dry-run`
        # projects), not as an execution-time override; see its own
        # docstring.
        header_backend=resolved.header_backend,
        fmt=resolved.fmt,
        public_headers=list(resolved.public_headers),
        public_header_dirs=list(resolved.public_header_dirs),
        enable_debuginfod=request.enable_debuginfod,
        debuginfod_url=request.debuginfod_url,
        dwarf_only=request.dwarf_only,
        debug_format=resolved.debug_format,
        include_labels=dict(request.include_labels) or None,
        notify=notify,
        build_config=options.build_config,
        build_query=options.build_query,
        build_compile_db=options.build_compile_db,
        changed_paths=options.changed_paths,
        allow_build_query=options.allow_build_query,
        legacy_compile_db_tokens=options.legacy_compile_db_tokens,
        legacy_compile_db_matched=options.legacy_compile_db_matched,
        seed_collect_mode=options.seed_collect_mode,
        source_frontend_from_folded_context=options.source_frontend_from_folded_context,
    )
    snap = resolution.snapshot

    if request.follow_dependencies:
        populate_side_dependency_info(
            snap,
            side,
            resolved.fmt,
            list(request.dependency_search_paths),
            request.ld_library_path,
        )

    enforce_requested_depth(resolved.requested_depth, (("input", snap),))
    try:
        # This call is new here -- unlike check_requested_depth_satisfied's
        # own call, it runs unconditionally, not just behind an explicit
        # --depth. _l4_source_abi_was_attempted() itself now degrades a
        # non-numeric compile_units_parsed to "not attempted" rather than
        # raising, so _gated_source_label still falls through to its own
        # L3/build-context checks (Codex review, two rounds); this except
        # is a defensive backstop only, matching that same fallback label.
        effective_depth = gated_source_label(snap.build_source, snap)
    except (TypeError, ValueError, OverflowError):
        effective_depth = "headers" if snap.from_headers else "binary"

    # One Semantic Pipeline plan, sub-phase 4B: the real post-execution
    # `with_assurance()` caller `resolve_dump_request`'s own docstring named
    # as the still-open half. `resolved.requested_depth` is lower-cased
    # first, matching `ResolvedExecutionContext.from_plan`'s own
    # case-normalization (see that method's docstring) -- `DumpRequest.depth`
    # is not itself normalized, and both `depth_rank` and the ladder
    # `EvidenceView.available_depths` states are case-sensitive.
    resolved_execution_context = None
    if resolved.resolved_execution_context is not None:
        requested_depth = (
            resolved.requested_depth.lower()
            if resolved.requested_depth is not None
            else None
        )
        resolved_execution_context = resolved.resolved_execution_context.with_assurance(
            _DumpAssuranceView(
                requested_depth=requested_depth,
                effective_depth=effective_depth,
                depth_satisfied=(
                    None
                    if requested_depth is None
                    else depth_rank(effective_depth) >= depth_rank(requested_depth)
                ),
            )
        )
        # `with_assurance()` alone leaves `compile_contexts` empty --
        # `side_effective_compile_context` (shared with the compare path)
        # answers whether `resolution.effective_compile_context` is safe to
        # record, including its own format detection (following a GNU ld
        # linker script to its real target). `side.path` is guaranteed
        # non-None here -- the `is None` branch above already returned.
        side_ctx = side_effective_compile_context(
            resolution.effective_compile_context,
            snap,
            side.path,
            dump_manifest=side.dump_manifest,
        )
        if side_ctx is not None:
            resolved_execution_context = dataclasses.replace(
                resolved_execution_context,
                compile_contexts={"input": side_ctx},
            )
    return DumpResult(
        resolved=resolved,
        snapshot=snap,
        effective_depth=effective_depth,
        effective_includes=resolution.effective_includes,
        effective_compile_context=resolution.effective_compile_context,
        resolved_execution_context=resolved_execution_context,
    )
