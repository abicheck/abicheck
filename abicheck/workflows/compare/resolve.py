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

"""Resolve both sides of a typed compare into a classifiable pair.

The first half of the typed compare pipeline (ADR-055 D1): validate a
:class:`CompareRequest`, resolve each side's evidence into an
:class:`AbiSnapshot`, enrich dependencies and enforce the requested depth.
:mod:`abicheck.workflows.compare.execute` runs the second half. Every front
end resolves through :func:`resolve_compare_request` -- the native ``compare``
CLI, the typed Python API and the MCP ``abi_compare`` tool -- so there is one
resolution implementation.

Moved here from ``abicheck/service_compare_pipeline.py`` (ADR-061 migration;
``workflows/AGENTS.md`` named it the last module of the compare pipeline still
outside its owner). Behaviour is unchanged.
"""

from __future__ import annotations

import contextvars
import functools
from pathlib import Path
from typing import TYPE_CHECKING

from ...buildsource.header_graph_ast_projection import header_graph_bisection_scope
from ...compile_context import CompileContext
from ...dependency_info import populate_pair_dependency_info
from ...errors import ValidationError
from ...extract.env_flags import env_flag
from ...process_resources import BudgetedExecutor
from ...storage.header_ast_cache import ast_acquisition_scope
from ..artifact.compile_context_gate import (
    SideCompileInput,
    resolved_pair_compile_contexts,
)
from ..artifact.compile_db_match import match_side_compile_db
from ..artifact.dump_execution_options import DumpExecutionOptions
from ..artifact.execute import _resolve_side_snapshot_impl
from ..compare_classify import ResolvedComparePair
from ..request_inputs import InputSpec, required_path
from ..service_requests import CompareRequest

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...service_compare_evidence import SideEvidence
    from ..artifact.execute import SideResolution

__all__ = [
    "resolve_compare_request",
    "resolve_sides_sequentially",
]


def resolve_sides_sequentially(request: CompareRequest) -> bool:
    """Whether both sides must resolve one after the other, not concurrently.

    Concurrency is the default: old/new resolution has no data dependency
    until both feed ``compare_snapshots``, and each is dominated by a
    castxml/DWARF subprocess and XML/JSON parsing rather than CPU held under
    the GIL. Two cases force sequential resolution instead:

    * ``ABICHECK_PARALLEL_EXTRACTION=0`` — the documented escape hatch for a
      memory-constrained runner, where two concurrent header-AST frontends
      roughly double peak RSS versus one at a time.
    * **Either side carries a ``dump_manifest``** (ADR-050 D6 / G32 Phase E).
      A manifest-driven dump sizes its own per-TU worker pool from a live
      ``MemAvailable`` reading; two of them starting together each size a
      full pool off the *same* reading and jointly overcommit. The native
      ``compare`` CLI has always resolved sequentially and so never had this
      problem, but ``InputSpec.dump_manifest`` made the manifest path
      reachable from ``run_compare_request`` too — which resolves
      concurrently — so the guard has to live here, where both front ends
      now share one resolution, rather than in the CLI's own copy of it.
    """
    if not env_flag("ABICHECK_PARALLEL_EXTRACTION"):
        return True
    return (
        request.old.dump_manifest is not None or request.new.dump_manifest is not None
    )


def _deadline_bound_side_worker(
    deadline_ts: float | None, worker: Callable[[], SideResolution]
) -> SideResolution:
    """Re-establish a captured ``--budget`` deadline inside a side-resolution worker.

    ``contextvars`` don't cross a ``ThreadPoolExecutor`` boundary, so without
    this a worker submitted from :func:`resolve_compare_request`'s parallel
    branch would silently ignore ``CompareRequest.budget_s``. Mirrors
    ``buildsource.source_replay._deadline_bound_worker`` -- its own copy,
    since that module is unrelated to `compare`."""
    import abicheck.deadline as deadline

    with deadline.with_deadline_ts(deadline_ts):
        return worker()


def _manifest_forced_includes(dump_manifest: object) -> list[Path]:
    """Forced includes declared by a ``--dump-manifest``'s translation units.

    A manifest replaces the side's ``headers`` (which is then empty), so its
    own forced includes must feed the pair-wide C++20 scan too or a
    manifest-only side's C++20 signal goes undetected (Codex review).
    """
    return [
        inc
        for tu in getattr(dump_manifest, "translation_units", ())
        for inc in tu.forced_includes
    ]


def _pair_compile_context(request: CompareRequest, lang: str) -> CompileContext | None:
    """The pair-wide C++20 dialect override, if the header sets imply one."""
    from ...compile_context import CompileContext
    from ...dry_run_estimate import pair_wide_cxx20_std_override

    override = pair_wide_cxx20_std_override(
        lang,
        list(request.old.headers)
        + _manifest_forced_includes(request.old.dump_manifest),
        list(request.new.headers)
        + _manifest_forced_includes(request.new.dump_manifest),
        None,
        (),
    )
    return CompileContext(gcc_option_tokens=override) if override is not None else None


def _public_header_sets(
    request: CompareRequest,
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    """Split each side's ``headers`` into the file/dir public-header sets.

    ``request.{old,new}.headers`` double as the public-header set for
    provenance tagging. They must be split into files and directories before
    tagging: an unsplit directory entry corrupts ``scope_fingerprint``.
    ``InputSpec.public_header_dirs`` is unioned in afterward.

    ``depth="binary"`` clears all four (Codex review): that depth clears
    ``evidence.headers``, but a headerless dump still fingerprints these, so
    leaving them populated makes two otherwise-identical sides raise a
    spurious ``ScopeMismatchError``.
    """
    from ...header_utils import split_public_header_inputs

    old_public_headers, old_public_header_dirs = split_public_header_inputs(
        request.old.headers
    )
    new_public_headers, new_public_header_dirs = split_public_header_inputs(
        request.new.headers
    )
    old_public_header_dirs += list(request.old.public_header_dirs)
    new_public_header_dirs += list(request.new.public_header_dirs)
    if request.depth is not None and request.depth.lower() == "binary":
        return [], [], [], []
    return (
        old_public_headers,
        old_public_header_dirs,
        new_public_headers,
        new_public_header_dirs,
    )


def _reject_unsupported_frontends(
    request: CompareRequest,
    frontend_lower: str,
    header_backend: str,
    old_evidence: SideEvidence,
    new_evidence: SideEvidence,
) -> None:
    """Reject the two frontend/evidence combinations that have no extractor.

    ``android`` and ``hybrid`` have no real ``embed_build_source`` extractor,
    so a raw source tree needing real extraction under either is a usage
    error rather than a silently weaker result. A prebuilt pack or a bare
    ``build_info`` never feeds L4, so neither is rejected. Mirrors ``cli.py``'s
    own ``--depth source`` + ``--ast-frontend hybrid`` ``UsageError``.

    The ``hybrid`` half is :func:`~abicheck.workflows.artifact.resolve.reject_hybrid_source_frontend`,
    shared with ``dump``'s own typed path; the ``android`` half names
    ``run_compare_request`` in its message and stays here.
    """
    from ..artifact.resolve import (
        is_raw_source_tree,
        reject_hybrid_source_frontend,
    )

    if frontend_lower == "android" and any(
        is_raw_source_tree(side.sources) for side in (request.old, request.new)
    ):
        raise ValidationError(
            "the 'android' AST frontend's source-ABI replay is not yet wired "
            "into run_compare_request's inline evidence collection for a raw "
            "source tree -- pass a prebuilt evidence pack directory instead, "
            "or use has_sources=True with no inline sources/build_info."
        )
    reject_hybrid_source_frontend(
        request.depth,
        ((request.old, old_evidence), (request.new, new_evidence)),
        header_backend,
    )


def _side_execution(request: CompareRequest, side: InputSpec) -> DumpExecutionOptions:
    """*side*'s execution plan: its own ``InputSpec.execution``, else the
    request-level defaults.

    The defaults are ADR-068 Phase 4's absorption of
    ``ScanRequest.allow_build_query``: ``None`` (not ``False``) when unset, so
    the primitive keeps its own documented default -- under which
    ``_gated_build_query_inputs`` nulls ``build_config`` outright, passive
    keys included, so a request that states no consent resolves as if it
    passed no ``build_config`` at all (see
    ``CompareRequest.allow_build_query``'s own docstring).
    """
    if side.execution is not None:
        return side.execution
    return DumpExecutionOptions(
        build_config=side.build_config,
        changed_paths=request.changed_paths,
        allow_build_query=request.allow_build_query or None,
    )


def resolve_compare_request(
    request: CompareRequest,
    *,
    notify: Callable[[str], None] | None = None,
    allow_parallel: bool = True,
) -> ResolvedComparePair:
    """Resolve both sides of *request* into a classifiable pair.

    The first half of :func:`abicheck.service.run_compare_request`, and the
    single resolution implementation every front end uses — the native
    ``compare`` CLI (through ``cli_resolve._resolve_compare_snapshots``), the
    typed Python API, and the MCP ``abi_compare`` tool.

    *notify* is forwarded to :func:`abicheck.service.resolve_input` for
    user-facing progress notes ("following a linker script"); the CLI passes a
    ``click.echo(..., err=True)`` wrapper, everything else leaves it ``None``
    so the notes are logged instead.

    *allow_parallel* is the caller's *permission* to resolve both sides
    concurrently, not a demand: :func:`resolve_sides_sequentially` can still
    veto it (a ``dump_manifest`` on either side, or
    ``ABICHECK_PARALLEL_EXTRACTION=0``) -- and covers *threads* only: two
    live binaries resolve in two concurrent child processes either way
    (``workflows.side_isolation.concurrent_children_apply``). The native
    ``compare`` CLI passes ``False`` so any other pair keeps its sequential,
    non-interleaved order.

    Raises:
        ValidationError: If the request fails :meth:`CompareRequest.validate`
            or names a frontend with no extractor for its evidence. **Not**
            for an unreached ``depth`` since PR #1195 (ADR-064's exit-7 axis,
            on ``DiffResult.evidence_contract_error``).
        PlanningError: If :class:`~abicheck.workflows.plan.AnalysisPlanner`
            finds a requested evidence input no resolved collector/backend
            combination can satisfy (ADR-063 Phase 4) — e.g. a ``.abicheck.yml`` ``build.targets`` declaration combined with a
            pre-captured Bazel ``aquery``/``cquery`` jsonproto.
        SnapshotError: If either input cannot be loaded.
    """
    import abicheck.deadline as deadline
    import abicheck.service_compare_evidence as _sce

    from ..input_resolution import detect_binary_format
    from ..plan import AnalysisPlanner

    request.validate()
    # Raw sources/build_info this run cannot collect from (depth binary/
    # headers, or a non-binary operand) are ignored with a note, before the
    # planner or any extraction sees them.
    request = _sce.without_unusable_raw_evidence(request, notify)
    # Codex review (fresh evidence, PR #1178): a stored-snapshot-only pair
    # needs no subprocess/extraction work at all, so nothing inside this
    # resolution would otherwise ever call `deadline.check()` -- an
    # already-expired `budget_s` (0, or exhausted by an earlier phase) would
    # silently resolve instead of raising here. `run_compare_request`'s
    # `deadline_scope` is already active by the time this runs.
    deadline.check()
    # ADR-063 Phase 4: reject a request no resolved collector/backend
    # combination can satisfy before any extraction runs (PlanningError),
    # rather than discovering the gap mid-run or not at all. See
    # `abicheck.workflows.plan`'s own module docstring for exactly what this
    # does and does not check.
    plan = AnalysisPlanner.resolve(request)
    # validate() accepts lang case-insensitively; the ELF dump path does
    # case-sensitive `lang == "c"` checks, so normalise here. `android` (no
    # header-AST path) falls back to "auto" for the binary dump.
    lang = request.lang.lower()
    from ...model.header_ast_frontends import HEADER_AST_FRONTENDS

    frontend_lower = request.frontend.lower()
    header_backend = (
        frontend_lower if frontend_lower in HEADER_AST_FRONTENDS else "auto"
    )
    # `validate()` above already rejected a `None` path on either side of a
    # comparison (`_path_required_errors(..., source_only_allowed=False)`);
    # `required_path` is the one place that narrowing is spelled.
    old_fmt = detect_binary_format(required_path(request.old, "old"))
    new_fmt = detect_binary_format(required_path(request.new, "new"))
    debug_format = _sce.normalized_debug_format(request)
    _sce.reject_debug_format_for_non_elf(debug_format, old_fmt, new_fmt)

    pair_compile = _pair_compile_context(request, lang)
    (
        old_public_headers,
        old_public_header_dirs,
        new_public_headers,
        new_public_header_dirs,
    ) = _public_header_sets(request)
    old_evidence, new_evidence = _sce.resolve_compare_request_evidence(
        request, pair_compile
    )
    # Mirrors `resolve_dump_request`'s identical per-side check (Codex
    # review, PR 3A investigation): a `CompareRequest` side's own
    # `InputSpec.compile_db_filter` reaches the same P0.3 L3->L2 fold /
    # `embed_side_build_source` split `resolve_dump_request`'s guard was
    # written for, so the same L2-filtered/L3-unfiltered refusal must fire
    # here too, not only on the single-input `dump` path.
    _sce.reject_compile_db_filter_scope_mismatch(
        (("old", request.old, old_evidence), ("new", request.new, new_evidence))
    )
    _reject_unsupported_frontends(
        request, frontend_lower, header_backend, old_evidence, new_evidence
    )

    def _resolve_side(
        side: InputSpec,
        evidence: SideEvidence,
        fmt: str | None,
        public_headers: list[Path],
        public_header_dirs: list[Path],
    ) -> SideResolution:
        options = _side_execution(request, side)
        compile_db = match_side_compile_db(
            options.compile_db, evidence.headers, options.compile_db_filter, notify
        )
        return _resolve_side_snapshot_impl(
            side,
            evidence,
            lang=lang,
            lang_explicit=request.lang_explicit,
            header_backend=header_backend,
            fmt=fmt,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            enable_debuginfod=request.enable_debuginfod,
            debuginfod_url=request.debuginfod_url,
            dwarf_only=request.dwarf_only,
            debug_format=debug_format,
            include_labels=dict(request.include_labels) or None,
            notify=notify,
            build_config=options.build_config,
            build_config_explicit=options.build_config_explicit,
            build_query=options.build_query,
            build_compile_db=options.build_compile_db,
            changed_paths=options.changed_paths,  # ADR-043 D7 POI scoping
            allow_build_query=options.allow_build_query,
            compile_db_tokens=compile_db.tokens,
            compile_db_matched=compile_db.matched,
            seed_collect_mode=options.seed_collect_mode,
            source_frontend_from_folded_context=options.source_frontend_from_folded_context,
        )

    def _resolve_old_side() -> SideResolution:
        return _resolve_side(
            request.old,
            old_evidence,
            old_fmt,
            old_public_headers,
            old_public_header_dirs,
        )

    def _resolve_new_side() -> SideResolution:
        return _resolve_side(
            request.new,
            new_evidence,
            new_fmt,
            new_public_headers,
            new_public_header_dirs,
        )

    from ...model.performance import (
        current_performance_profile,
        parse_performance_profile,
        tuning_for,
    )
    from ..side_isolation import concurrent_children_apply, run_cold_isolated

    tuning = tuning_for(
        parse_performance_profile(request.performance_profile)
        if request.performance_profile is not None
        else current_performance_profile()
    )
    # `allow_parallel` permits two *threads*; two live binaries may still go
    # to two concurrent child processes without it (`concurrent_children_apply`).
    concurrent = not tuning.sequential_sides and not resolve_sides_sequentially(request)
    # One header-graph bisection memo per request, joined by an enclosing
    # release fan-out's; the side workers below inherit it (copy_context).
    with ast_acquisition_scope(), header_graph_bisection_scope():
        if tuning.isolate_sides or (
            concurrent and concurrent_children_apply(tuning, old_fmt, new_fmt)
        ):
            _deadline_ts = deadline.current_deadline_ts()
            old_res, new_res = run_cold_isolated(
                (_resolve_old_side, _resolve_new_side),
                lambda fn: functools.partial(
                    _deadline_bound_side_worker, _deadline_ts, fn
                ),
                concurrent=not tuning.isolate_sides,
            )
        elif not (concurrent and allow_parallel):
            old_res = _resolve_old_side()
            new_res = _resolve_new_side()
        else:
            # ADR-068 §3 #19 (Codex review): re-enter the captured deadline in
            # each worker -- see `_deadline_bound_side_worker`'s own docstring.
            _deadline_ts = deadline.current_deadline_ts()
            acquisition_context = contextvars.copy_context()
            with BudgetedExecutor(2) as pool:
                old_future = pool.submit(
                    acquisition_context.copy().run,
                    _deadline_bound_side_worker,
                    _deadline_ts,
                    _resolve_old_side,
                )
                new_future = pool.submit(
                    acquisition_context.copy().run,
                    _deadline_bound_side_worker,
                    _deadline_ts,
                    _resolve_new_side,
                )
                old_res = old_future.result()
                new_res = new_future.result()
    old, new = old_res.snapshot, new_res.snapshot

    # ADR-055 D1: `--follow-deps`'s transitive DependencyInfo. After both sides
    # resolve, not inside `_resolve_side`: it reads the on-disk ELF, so it
    # gains nothing from the extraction threads.
    populate_pair_dependency_info(request, old, new, old_fmt=old_fmt, new_fmt=new_fmt)
    # No `enforce_requested_depth` here (PR #1195): a depth shortfall is
    # ADR-064's exit-7 axis, recorded by `classify_compare_pair` below, which
    # this call pre-empted. Why: `policy/depth_evidence_contract.py`.
    from ..resolved_execution_context import ResolvedExecutionContext

    compile_contexts = resolved_pair_compile_contexts(
        SideCompileInput(
            old_res.effective_compile_context,
            old,
            required_path(request.old, "old"),
            request.old.dump_manifest,
        ),
        SideCompileInput(
            new_res.effective_compile_context,
            new,
            required_path(request.new, "new"),
            request.new.dump_manifest,
        ),
    )

    return ResolvedComparePair(
        old=old,
        new=new,
        old_fmt=old_fmt,
        new_fmt=new_fmt,
        old_evidence=old_evidence,
        new_evidence=new_evidence,
        resolved_execution_context=ResolvedExecutionContext.from_plan(
            plan, compile_contexts=compile_contexts
        ),
    )
