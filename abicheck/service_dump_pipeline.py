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

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .errors import AstContextMissingError, ValidationError
from .workflows.artifact import ResolvedArtifactPlan
from .workflows.artifact.execute import (
    _resolve_side_snapshot_impl,
    enforce_requested_depth,
)
from .workflows.artifact.resolve import (
    is_raw_source_tree,
    reject_hybrid_source_frontend,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .api_types import DumpRequest
    from .compile_context import CompileContext
    from .model import AbiSnapshot
    from .service_compare_evidence import SideEvidence

__all__ = [
    "DumpResult",
    "ResolvedDumpRequest",
    "execute_dump_request",
    "resolve_dump_request",
    "run_dump_request",
]


@dataclass(frozen=True)
class ResolvedDumpRequest:
    """A :class:`DumpRequest` after resolution, before execution.

    CLI cleanup phase two, PR C / PR 3A (see
    ``docs/contribute/plans/cli-cleanup-phase-two.md``): the object
    ``dump --dry-run`` is meant to render, once its rendering path
    (``cli_dump_helpers.render_dump_dry_run``, currently a hand-written
    second implementation) is migrated to build from this instead of
    re-deriving the same facts independently.

    Carries only what :func:`resolve_dump_request` can determine without
    invoking castxml/clang or writing anything: the normalized language, the
    requested header-AST backend, the detected binary format, the requested
    ``depth`` (an input, known up front) and the effective *collect mode*
    (the build/source evidence level ``depth`` resolves to). Deliberately
    does **not** carry an *achieved* depth — that can only be read off the
    completed snapshot (``cli_dump_helpers.fold_dump_provenance_into_dict``
    derives it via ``_gated_source_label(snap.build_source, snap)``), so a
    resolve-only object reporting it would have to guess, and a guess that
    disagrees with the real run defeats the point of rendering ``--dry-run``
    from a real resolved object (Codex review, fresh evidence). See
    :class:`DumpResult` for the achieved depth.

    Also deliberately excludes the P0.3 L3→L2 compile-context fold's result:
    that fold (``buildsource.l2_seed.seed_includes_and_fold_compile_context``)
    can raise ``HeaderCompileContextAmbiguousError`` on genuinely ambiguous
    build evidence, and ``--dry-run``'s existing contract
    (``render_dump_dry_run``'s own docstring) is to never raise on anything
    but a usage error. Folding it in here would be a real behavior change to
    that contract, not merely an additive one — it stays inside
    :func:`execute_dump_request`, unchanged from where :func:`run_dump_request`
    already runs it today (via :func:`~abicheck.service_input_resolution.resolve_side_snapshot`).

    ``artifact_plan`` (dedup-and-convergence plan, Phase 1 item 1
    "Milestone B"): the same facts this object already carries, also
    attached to a :class:`~abicheck.workflows.artifact.ResolvedArtifactPlan`
    -- the general, cross-consumer shape the plan's target architecture
    names. Built with an empty ``pending_cleanups`` (this function allocates
    no resource -- see :mod:`abicheck.workflows.artifact.contracts`'s own
    module docstring for why the two fields that *would* require one,
    effective include search and effective compile context, stay excluded
    here too), so it is
    additive, inert data today: nothing yet reads it. It exists so a future
    consumer of the general shape (e.g. a migrated ``render_dump_dry_run``)
    has one object to build from instead of this dump-specific one, without
    this dataclass's own field surface changing again when that lands.

    Excluded from this dataclass's generated ``__eq__``/``__hash__``
    (``compare=False``, Codex review): ``ResolvedArtifactPlan`` is a plain
    class, not a dataclass, so it compares by identity. Two structurally
    identical ``DumpRequest``s resolved independently would otherwise
    produce two ``ResolvedDumpRequest``s that compare unequal purely
    because each carries its own, distinct ``ResolvedArtifactPlan``
    instance -- silently breaking equality-based comparison or caching for
    every existing and future caller of this frozen dataclass, over a field
    that is itself inert today.
    """

    request: DumpRequest
    lang: str
    lang_explicit: bool
    header_backend: str
    # A *reporting-only* projection of the concrete header-AST backend
    # resolution currently favors -- what a future `--dry-run` render would
    # show. `header_backend` alone under-reports this: service.py's own
    # eff_backend computation gives an explicit `evidence.compile.frontend`
    # precedence over the bare `header_backend` arg, and resolves "auto" to
    # a concrete backend either way, so a naive render of `header_backend`
    # can name a different frontend than what would currently be chosen.
    #
    # Deliberately NOT what execute_dump_request passes to execution
    # (Codex review, two rounds -- the first attempt did pass this value
    # through, which is a real regression, not a pin: `dumper.
    # _header_ast_parser`'s own `_auto_ast_fallback_eligible(backend)`
    # checks whether `backend` is *literally* the string "auto" to decide
    # whether a CastXML failure may gracefully fall back to Clang, and a
    # non-"host" `frontend_context` has its own "auto"-specific routing --
    # pre-resolving "auto" to a concrete choice before it reaches that
    # function silently strips those behaviors). Execution therefore keeps
    # passing the bare `header_backend` through unchanged, and this field
    # is accepted as a best-effort preview that can, in principle, disagree
    # with what execution ends up doing if the environment changes between
    # resolve and execute, or if the genuinely-unpinned-"auto" fallback
    # path fires -- the same class of accepted imprecision every other
    # resolve-time preview in this object already carries.
    effective_header_backend: str
    fmt: str | None
    debug_format: str | None
    requested_depth: str | None
    evidence: SideEvidence
    public_headers: tuple[Path, ...]
    public_header_dirs: tuple[Path, ...]
    artifact_plan: ResolvedArtifactPlan | None = field(default=None, compare=False)

    @property
    def collect_mode(self) -> str:
        """The effective build/source evidence level ``depth`` resolved to."""
        return self.evidence.collect_mode

    @property
    def headers(self) -> tuple[Path, ...]:
        """The resolved public-header set (files only; see ``public_header_dirs``)."""
        return tuple(self.evidence.headers)


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
    """

    resolved: ResolvedDumpRequest
    snapshot: AbiSnapshot
    effective_depth: str
    effective_includes: tuple[Path, ...] = ()
    effective_compile_context: CompileContext | None = None


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
            combination can satisfy (ADR-063 Phase 4) — e.g. ``--build-target``
            combined with a pre-captured Bazel ``aquery``/``cquery`` jsonproto.
    """
    from . import service, service_compare_evidence as _sce
    from .api_types import HEADER_AST_FRONTENDS
    from .header_utils import split_public_header_inputs
    from .workflows.plan import AnalysisPlanner

    request.validate()
    # ADR-063 Phase 4: reject a request no resolved collector/backend
    # combination can satisfy before any extraction runs (PlanningError),
    # rather than discovering the gap mid-run or not at all. See
    # `abicheck.workflows.plan`'s own module docstring for exactly what this
    # does and does not check.
    AnalysisPlanner.resolve(request)
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
    # clang unconditionally (`if resolved == "clang" or frontend_context !=
    # "host": return _run_clang()`), regardless of what the backend itself
    # resolved to -- mirror that here so this reporting field doesn't claim
    # castxml for a request that will always run clang. But an *explicit*
    # `--ast-frontend castxml` (or an env-pinned one) combined with a
    # non-host context doesn't route to clang at all -- it raises
    # AstContextMissingError at execution, so claiming "clang" there would
    # be equally wrong in the other direction (Codex review, two rounds:
    # the first fix applied the clang override unconditionally, missing
    # this pinned-castxml case entirely). Reuse dumper's own resolver to
    # tell the two apart without duplicating its pin/env logic; on the
    # raising path, leave whatever `_resolve_header_backend` already
    # produced above -- this best-effort preview never raises.
    if (
        evidence.compile is not None
        and evidence.compile.frontend_context.lower() != "host"
    ):
        from .dumper import _resolve_single_ast_backend

        requested = (
            evidence.compile.frontend
            if evidence.compile.frontend.lower() != "auto"
            else header_backend
        )
        try:
            _resolve_single_ast_backend(
                requested, evidence.compile.frontend_context.lower()
            )
        except (AstContextMissingError, ValidationError):
            pass
        else:
            effective_header_backend = "clang"

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
    )


def execute_dump_request(
    resolved: ResolvedDumpRequest,
    *,
    notify: Callable[[str], None] | None = None,
    build_config: Path | None = None,
    build_query: str | None = None,
    build_compile_db: str | None = None,
    changed_paths: tuple[str, ...] = (),
    allow_build_query: bool | None = None,
    legacy_compile_db_tokens: tuple[str, ...] = (),
    legacy_compile_db_matched: bool = False,
    seed_collect_mode: str | None = None,
    source_frontend_from_folded_context: bool = False,
) -> DumpResult:
    """Execute a :class:`ResolvedDumpRequest` — steps 3-5 of
    :func:`run_dump_request`'s own docstring (``resolve_input``, the
    dependency walk, the depth floor).

    *notify* is forwarded to :func:`abicheck.service.resolve_input` for
    user-facing progress notes ("following a linker script"); ``None`` logs
    them instead.

    *build_config*/*build_query*/*build_compile_db*/*changed_paths*/
    *allow_build_query* (PR 3A, dump/scan resolver convergence): optional
    pass-throughs to
    :func:`~abicheck.service_input_resolution._resolve_side_snapshot_impl`,
    all defaulted to their existing no-op values so :func:`run_dump_request`
    and every other pre-existing caller is unaffected. These exist only for
    the ELF ``dump`` CLI path's ``--config`` flag -- and, for a programmatic
    caller, its own ``build_query``/``build_compile_db`` arguments (PR 3C
    removed the CLI flags of those names; these parameters stay because a
    Python API caller is the operator, exactly as an explicit ``--config``
    is) -- to
    route through this one shared primitive instead of a second, independent
    call to the same underlying fold.

    *legacy_compile_db_tokens* (ADR-063 Phase 1): the castxml flags the CLI's
    own legacy ``-p``/``--compile-db`` auto-match
    (``cli_helpers_compare._resolve_build_context_flags``) already derived,
    forwarded verbatim to :func:`~abicheck.workflows.artifact.execute._resolve_side_snapshot_impl`
    -- see that function's own docstring for the precedence rule (the P0.3
    fold's own result wins whenever it applies) and
    ``docs/contribute/known-gaps.md``'s "ADR-063 Phase 1" entry for exactly
    what this closes and what still doesn't. *legacy_compile_db_matched*
    (Codex review, fresh evidence) is a separate signal from whether any
    tokens were actually derived -- see the resolve-layer function's own
    docstring for why a real match with zero derived flags still must set
    it. Both default falsy, so every pre-existing caller (including
    :func:`run_dump_request`) is unaffected. Both are passed by the migrated
    ``dump`` CLI's real run for either binary format
    (``frontends.cli.dump_execute.execute_dump_cli_run``) -- ADR-063 Phase 1
    migrated PE/Mach-O onto this same function after ELF, so
    ``cli_dump_non_elf.handle_non_elf_dump`` is no longer called from
    ``dump_cmd`` for either format (it stays defined for its own direct
    unit tests).

    *seed_collect_mode*/*source_frontend_from_folded_context* (Codex review
    on the initial ELF migration -- two real regressions it introduced):
    forwarded verbatim to
    :func:`~abicheck.workflows.artifact.execute._resolve_side_snapshot_impl`,
    whose own docstring documents each. Both default to this function's
    pre-existing behavior (``seed_collect_mode=None`` pins the L2 seed's
    collect mode to ``"off"``; ``source_frontend_from_folded_context=False``
    keeps L4 replay pointed at the pre-fold compiler), so every pre-existing
    caller is unaffected. The retired ``perform_elf_dump`` always forwarded
    its own resolved ``collect_mode`` to the identical L2 seed call
    (unconditionally running a zero-config inferred build query for a
    ``--sources`` tree with no compile database) and always reassigned
    ``gcc_path``/``gcc_prefix``/``effective_gcc_options`` from the L3 fold's
    context once it applied (so an L4 source replay used the compiler the L3
    fold actually matched, not the caller's pre-fold default) -- the
    migrated ELF run passes ``seed_collect_mode=resolved.collect_mode`` and
    ``source_frontend_from_folded_context=True`` to preserve both, exactly
    as ``scan``'s own candidate resolution already does for the identical
    reasons (see that call site's comments).

    Raises:
        ValidationError: If *resolved* requests a ``depth`` the resolved
            snapshot did not reach, or if its input carries no ``path`` (a
            source-only request — see
            :func:`~abicheck.cli_buildsource.dump_source_only`).
        SnapshotError: If the input cannot be loaded.
    """
    from .dependency_info import populate_side_dependency_info
    from .evidence_depth import gated_source_label

    request = resolved.request
    side = request.input
    if side.path is None:
        # PR 3A blocker 5: `InputSpec.path` was widened to `Path | None` so a
        # source-only dump is *expressible* as a typed request (which is what
        # lets `dump_cmd` build one `DumpRequest` covering both of its
        # branches, and lets `--dry-run` resolve one). Executing that shape is
        # a genuinely different pipeline -- `cli_buildsource.dump_source_only`
        # collects L3-L5 into an otherwise empty snapshot with no
        # `resolve_input` call at all -- and routing it through here is its own
        # slice, not part of making the model able to say it. Fail loudly and
        # specifically rather than with an `AttributeError` from deep inside
        # extraction.
        raise ValidationError(
            "executing a binary-less (source-only) DumpRequest is not wired "
            "into execute_dump_request yet -- resolve_dump_request() supports "
            "it (that is what `dump --dry-run` needs), but producing the "
            "snapshot is still cli_buildsource.dump_source_only's own "
            "pipeline. Supply InputSpec.path, or use the `dump` CLI."
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
        build_config=build_config,
        build_query=build_query,
        build_compile_db=build_compile_db,
        changed_paths=changed_paths,
        allow_build_query=allow_build_query,
        legacy_compile_db_tokens=legacy_compile_db_tokens,
        legacy_compile_db_matched=legacy_compile_db_matched,
        seed_collect_mode=seed_collect_mode,
        source_frontend_from_folded_context=source_frontend_from_folded_context,
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
    return DumpResult(
        resolved=resolved,
        snapshot=snap,
        effective_depth=effective_depth,
        effective_includes=resolution.effective_includes,
        effective_compile_context=resolution.effective_compile_context,
    )
