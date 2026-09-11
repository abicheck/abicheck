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

"""ADR-055 D1: ``run_compare_request`` split into its two real phases.

``run_compare_request`` was one function doing two separable things — turning a
:class:`CompareRequest` into a resolved pair of snapshots, then classifying that
pair. The native ``compare`` CLI needs to run its own ADR-049 step *between*
those two (``cli_compare_receipt.resolve_and_apply`` needs the Click context to
answer "did the user type this?", and a ``--pack`` it selects can move the
policy file and severity levels the classification is then scored under), so as
one function it could not reuse either half and kept a parallel resolution
implementation of its own (``cli_resolve._resolve_compare_snapshots``).

Splitting it here gives all three front ends one resolution implementation:

* :func:`resolve_compare_request` — validate, resolve both sides' evidence,
  produce the two :class:`AbiSnapshot`\\ s, enrich dependencies, enforce the
  requested depth.
* :func:`classify_compare_pair` — load suppression/policy, diff the embedded
  build-source evidence, classify, attach metrics.

``service.run_compare_request`` is now exactly their composition, so a caller
that does not need the seam keeps one call, and one that does (the CLI) gets
the same resolution the typed path uses instead of a second copy.

Two mechanical notes:

* Everything this module needs is imported function-local, straight from its
  real owner (``workflows.input_resolution``/``workflows.compare_policy``,
  not the flat ``service`` facade re-export -- ADR-061 gap A), so
  ``monkeypatch.setattr`` on the owner module keeps working and this module
  does not join ``service``'s import cycle.
* This module holds no state and makes no policy decisions of its own; every
  behavioural rule in it moved here verbatim from ``service.run_compare_request``.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .compile_context import CompileContext
from .confidence import note_if_same_binary_compared
from .dependency_info import populate_pair_dependency_info
from .environment_matrix import EnvironmentMatrix
from .errors import ValidationError
from .policy.depth_projection import (
    project_build_source_pack_to_depth,
    project_pair_to_depth,
    project_snapshot_to_depth,
)
from .workflows import abi3_audit, gate as gate_workflow
from .workflows.artifact.compile_context_gate import (
    SideCompileInput,
    resolved_pair_compile_contexts,
)
from .workflows.artifact.execute import _resolve_side_snapshot_impl
from .workflows.contracts import CompareRequest, CompareResult
from .workflows.request_inputs import InputSpec, required_path

if TYPE_CHECKING:
    from collections.abc import Callable

    from .model import AbiSnapshot
    from .service_compare_evidence import SideEvidence
    from .workflows.artifact.execute import SideResolution
    from .workflows.resolved_execution_context import ResolvedExecutionContext

#: "Not passed" marker for `classify_compare_pair`'s `resolved_env_matrix` --
#: `None` is itself a legitimate resolved value, so it can't double as unset.
_ENV_MATRIX_UNRESOLVED = object()

__all__ = [
    "ResolvedComparePair",
    "classify_compare_pair",
    "project_build_source_pack_to_depth",
    "project_pair_to_depth",
    "project_snapshot_to_depth",
    "resolve_compare_request",
    "resolve_sides_sequentially",
    "run_compare",
    "run_compare_request",
]


@dataclasses.dataclass(frozen=True)
class ResolvedComparePair:
    """Both sides of a comparison, resolved and ready to classify.

    The seam between :func:`resolve_compare_request` and
    :func:`classify_compare_pair`: the detected binary formats, and each
    side's resolved ``SideEvidence`` (``collect_mode`` drives the embed diff).

    ``resolved_execution_context`` (One Semantic Pipeline plan, sub-phase 4B)
    is the :class:`~abicheck.workflows.resolved_execution_context.
    ResolvedExecutionContext` built from this request's own AnalysisPlan,
    plus each side's resolved ``CompileContext`` when ``compile_context_gate``
    judges it safe — still no ``evaluation_config`` (resolves one layer up).
    """

    old: AbiSnapshot
    new: AbiSnapshot
    old_fmt: str | None
    new_fmt: str | None
    old_evidence: SideEvidence
    new_evidence: SideEvidence
    resolved_execution_context: ResolvedExecutionContext | None = None


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
    if os.environ.get("ABICHECK_PARALLEL_EXTRACTION", "1").strip().lower() in (
        "0",
        "false",
        "no",
    ):
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
    from . import deadline

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
    from .compile_context import CompileContext
    from .dry_run_estimate import pair_wide_cxx20_std_override

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
    from .header_utils import split_public_header_inputs

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

    The ``hybrid`` half is :func:`~abicheck.service_input_resolution.reject_hybrid_source_frontend`,
    shared with ``dump``'s own typed path; the ``android`` half names
    ``run_compare_request`` in its message and stays here.
    """
    from .workflows.artifact.resolve import (
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
    ``ABICHECK_PARALLEL_EXTRACTION=0``). The native ``compare`` CLI passes
    ``False`` — it has always resolved sequentially, and its two dumps write
    interleaving progress notes to the same stderr, so adopting this shared
    resolution deliberately does not change its memory or output profile.
    Flipping the CLI to concurrent extraction is a measurable change worth
    making on its own evidence, not a side effect of removing a duplicate
    implementation.

    Raises:
        ValidationError: If the request fails :meth:`CompareRequest.validate`
            or names a frontend with no extractor for its evidence. **Not**
            for an unreached ``depth`` since PR #1195 (ADR-064's exit-7 axis,
            on ``DiffResult.evidence_contract_error``).
        PlanningError: If :class:`~abicheck.workflows.plan.AnalysisPlanner`
            finds a requested evidence input no resolved collector/backend
            combination can satisfy (ADR-063 Phase 4) — e.g. ``--build-target``
            combined with a pre-captured Bazel ``aquery``/``cquery`` jsonproto.
        SnapshotError: If either input cannot be loaded.
    """
    from . import deadline, service_compare_evidence as _sce
    from .workflows.input_resolution import detect_binary_format
    from .workflows.plan import AnalysisPlanner

    request.validate()
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
    from .model.header_ast_frontends import HEADER_AST_FRONTENDS

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
            changed_paths=request.changed_paths,  # ADR-043 D7 POI scoping
            # ADR-068 Phase 4, absorbed from `ScanRequest.allow_build_query` (with `ScanRequest.build_config`'s own already-present `InputSpec.build_config` counterpart, which nothing on this path read before). `None` (not `False`) when unset, so the primitive keeps its own documented default rather than this request restating it -- and under that default `_gated_build_query_inputs` nulls `build_config` outright, passive keys included, so every pre-existing request resolves exactly as it did (it passed no `build_config` here at all). `compare` deliberately does not pass `build_config_locally_trusted`: that is `scan`'s own split, and giving it to `compare` would newly make a discovered config's passive settings affect a comparison -- a behaviour change this field's absorption does not carry. See `CompareRequest.allow_build_query`'s own docstring.
            build_config=side.build_config,
            allow_build_query=request.allow_build_query or None,
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

    if not allow_parallel or resolve_sides_sequentially(request):
        old_res = _resolve_old_side()
        new_res = _resolve_new_side()
    else:
        # ADR-068 §3 #19 (Codex review): re-enter the captured deadline in
        # each worker -- see `_deadline_bound_side_worker`'s own docstring.
        _deadline_ts = deadline.current_deadline_ts()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            old_future = pool.submit(
                _deadline_bound_side_worker, _deadline_ts, _resolve_old_side
            )
            new_future = pool.submit(
                _deadline_bound_side_worker, _deadline_ts, _resolve_new_side
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
    from .workflows.resolved_execution_context import ResolvedExecutionContext

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


def classify_compare_pair(
    request: CompareRequest,
    pair: ResolvedComparePair,
    *,
    resolved_env_matrix: EnvironmentMatrix | None | object = _ENV_MATRIX_UNRESOLVED,
) -> CompareResult:
    """Classify an already-resolved pair — the second half of ``run_compare_request``.

    Loads the request's suppression list and policy file, diffs whatever
    build-source evidence the snapshots carry into ``extra_changes`` (without
    it a source-only change reads as artifact-only compatible), classifies
    through the Tier-2 ``compare_snapshots`` chokepoint, and attaches the
    coverage/metrics the diff itself does not produce -- plus ADR-068 D3's candidate-only ``--abi3`` enrichment (Phase 2d), folded into ``extra_changes`` *before* classification so policy scores it; a no-op unless ``CompareRequest.abi3_floor`` is set.

    A front end that must configure the run between the two phases (the native
    ``compare`` CLI's ADR-049 ``resolve_and_apply``, which can move the policy
    file and severity levels a ``--pack`` selects) calls
    :func:`resolve_compare_request` and then classifies on its own terms
    instead of calling this; everything else composes the two through
    :func:`abicheck.service.run_compare_request`.

    *resolved_env_matrix* lets :func:`run_compare_request` thread through a
    matrix it already resolved early, before :func:`resolve_compare_request`'s
    extraction work, so a bad ``env_matrix_path`` fails fast without a second
    file read here. Left unset, this resolves it itself (unchanged behaviour
    for a caller classifying directly, without ``run_compare_request``).
    """
    from . import deadline
    from .buildsource.evidence_report import (
        attach_evidence_metrics,
        prepare_embedded_build_source,
    )
    from .workflows.compare_policy import compare_snapshots, load_suppression_and_policy
    from .workflows.input_resolution import (
        collect_metadata,
        sniff_text_format,
    )

    # Same classify-stage boundary check as `resolve_compare_request`'s own
    # (Codex review, fresh evidence, PR #1178): `compare_snapshots` below can
    # complete with no subprocess/extraction work at all.
    deadline.check()

    # ADR-020b / ADR-068 D5: `effective_env_matrix()` resolves a lazy
    # `env_matrix_path`, here unless `run_compare_request` already resolved
    # it earlier and threaded the answer through (see docstring above).
    env_matrix: EnvironmentMatrix | None
    if resolved_env_matrix is _ENV_MATRIX_UNRESOLVED:
        env_matrix = request.effective_env_matrix()
    else:
        assert resolved_env_matrix is None or isinstance(
            resolved_env_matrix, EnvironmentMatrix
        )
        env_matrix = resolved_env_matrix

    # ADR-063 Phase 8's "--depth floor vs ceiling" gap: the *ceiling* half,
    # narrowing what this classification may see to the requested rung. The
    # floor is the exit-7 axis below; the ceiling applies either way.
    # Deliberately a *view*, not a mutation of
    # `pair.old`/`pair.new` (see `project_pair_to_depth`'s own docstring) --
    # `pair` may still be read elsewhere for its unprojected snapshots.
    old, new = project_pair_to_depth(pair.old, pair.new, request.depth)
    suppression, pf = load_suppression_and_policy(
        request.suppress, request.policy, request.policy_file_path
    )
    # CLI cleanup phase two, PR B slice 1: fold an already-resolved pack's
    # policy/contract-surface contributions into the loaded PolicyFile, the
    # same way `pack_application.policy_file_with_packs` does for single-pair
    # `compare` -- a no-op unless a caller populated `CompareRequest.
    # pack_policy_overrides`/`pack_internal_namespaces`. `pf` is reused for
    # the receipt below too (`compare_gate_receipt._with_pack_forwarded_
    # provenance` records the forwarded pack's own contribution honestly).
    if request.pack_policy_overrides or request.pack_internal_namespaces is not None:
        from .pack_application import PackApplication, policy_file_with_packs

        pf = policy_file_with_packs(
            pf,
            PackApplication(
                policy_overrides=dict(request.pack_policy_overrides or {}),
                internal_namespaces=request.pack_internal_namespaces,
            ),
            base_policy=request.policy,
        )
    # ADR-068 §3 #23 / ADR-049 D7: fold project-config overrides at the weakest
    # tier, after the pack fold above. Round 5 finding 3: kept OUT of the receipt call below, which re-derives the project contribution at the correct tier.
    pf_before_project_fold = pf
    if request.project_policy_overrides:
        from .policy.policy_file_project_overrides import (
            apply_lower_precedence_overrides,
        )

        pf = apply_lower_precedence_overrides(
            pf, dict(request.project_policy_overrides), base_policy=request.policy
        )
    # The four Nones are the out-of-band pack-override params -- reusing the
    # raw sources/build_info paths would make `_resolve_side_pack` try (and
    # fail) to reload them as packs; None uses the embedded facts.
    (
        extra_changes,
        layer_coverage_rows,
        evidence_metrics,
        _ev_changes,
    ) = prepare_embedded_build_source(
        old,
        new,
        pair.old_evidence.collect_mode,
        None,
        None,
        None,
        None,
        None,
        policy_file=pf,
    )
    extra_changes, _fail = abi3_audit.fold(extra_changes, new, request.abi3_floor)
    result = compare_snapshots(
        old,
        new,
        suppression=suppression,
        policy=request.policy,
        policy_file=pf,
        scope_to_public_surface=request.scope_public,
        force_public_symbols=(
            set(request.force_public_symbols) if request.force_public_symbols else None
        ),
        extra_changes=extra_changes,
        public_surface_allowlist=(
            set(request.public_surface_allowlist)
            if request.public_surface_allowlist is not None
            else None
        ),
        # Codex review, second look (PR #1154 follow-up: "Obtain ADR approval
        # before forcing verdict modulation"): a prior fix here forced
        # `pattern_verdicts=True` unconditionally, citing ADR-068 D4's
        # "no legitimate off position" principle -- but ADR-068 is
        # "Proposed -- not implemented", not an accepted decision, and the
        # ADR that *is* accepted (ADR-027) explicitly defers flipping
        # `--pattern-verdicts` to default-on until a release cycle's worth
        # of FP-rate and parity validation. The native single-pair CLI
        # (cli_compare_helpers.py) and the release fan-out's own
        # `_compare_one_library` (cli_compare_release_pairwise.py) each
        # made their own, separately-committed decision to hardcode
        # `pattern_verdicts=True` at their own call sites -- that predates
        # this fix and is out of scope for it -- but this shared Tier-2
        # chokepoint (the typed API's `service.run_compare`, and the
        # stored-BundleFacts drivers that call it) forwards the request's
        # own field again, so a bare `CompareRequest()`/`run_compare()` call
        # keeps the accepted opt-in default instead of a silent, un-reviewed
        # flip. `surface_metrics` and `reconcile_build_context` are no longer
        # parameters of that Tier-2 verb at all (one-comparison-product.md
        # Phase 5 / §4.1's AUTO rows): `compare_snapshots` forces both on for
        # every caller, so `CompareRequest` carries neither field any more --
        # front-end parity with the CLI, where the two flags are gone too.
        pattern_verdicts=request.pattern_verdicts,
        collapse_versioned_symbols=request.collapse_versioned_symbols,
        env_matrix=env_matrix,
        diagnostic_comparison=request.diagnostic_comparison,
        contract_evaluation=request.contract_evaluation,
        contract_mode=request.contract_mode,
    )
    if layer_coverage_rows:
        result.layer_coverage = layer_coverage_rows
    attach_evidence_metrics(result, evidence_metrics, extra_changes or [])
    abi3_audit.record_abi3_evidence_contract_error(result, _fail)
    # ADR-068 §3 #28 -- `compare`'s only depth-floor mechanism since PR #1195.
    # The liveness rule it needs lives with the axis, not here.
    from .workflows import depth_evidence_contract

    depth_evidence_contract.record_for_compare_request(result, request, old, new)
    # Hash through the full GNU ld linker-script chain to its final resolved
    # target -- resolve_side_snapshot() already followed the identical chain
    # to produce `old`/`new` above -- so a (possibly multi-hop) script vs.
    # its target DSO given as the other `CompareRequest` side still reads as
    # byte-identical (mirrors the same fix on
    # `cli_scan_baseline._run_baseline_compare`).
    from .binary_utils import resolve_linker_script_chain

    # A text snapshot/manifest can coincidentally match the INPUT()/GROUP()
    # probe -- skip linker-script resolution for it.
    def _hashable_path(p: Path) -> Path:
        return (
            p
            if sniff_text_format(p) in ("json", "perl", "symvers")
            else resolve_linker_script_chain(p)
        )

    result.old_metadata = collect_metadata(
        _hashable_path(required_path(request.old, "old"))
    )
    result.new_metadata = collect_metadata(
        _hashable_path(required_path(request.new, "new"))
    )
    # Item 4 fix: collect_metadata() is a no-op for a JSON/text snapshot
    # path, so a snapshot-input compare left note_if_same_binary_compared
    # unable to fire on content-identical snapshots. Digest fallback below
    # requires *both* sides missing metadata (`and`, not `or`): a mixed
    # live-binary-vs-snapshot compare has one side absent by design.
    old_digest = new_digest = None
    if result.old_metadata is None and result.new_metadata is None:
        from .serialization import snapshot_content_digest

        old_digest = snapshot_content_digest(old)
        new_digest = snapshot_content_digest(new)
    note_if_same_binary_compared(
        result, old_snapshot_digest=old_digest, new_snapshot_digest=new_digest
    )

    # P0.4 follow-up (P2 review, discussion_r3787839902): stamps
    # `DiffResult.requested_depth` for `analysis_assurance` below, preferring
    # `pair.resolved_execution_context`'s value over `request.depth` only
    # when the two agree (a caller may pass a *different* `request` than
    # built `pair`; a disagreement defers to `request.depth`, what `old`/
    # `new` above were actually projected to -- Codex review).
    normalized_request_depth = (
        request.depth.lower() if request.depth is not None else None
    )
    context = pair.resolved_execution_context
    if context is not None and context.requested_depth == normalized_request_depth:
        result.requested_depth = context.requested_depth
    elif normalized_request_depth is not None:
        result.requested_depth = normalized_request_depth
    from .analysis_assurance import compute_analysis_assurance

    result.analysis_assurance = compute_analysis_assurance(
        result,
        old,
        new,
        old_pack=getattr(old, "build_source", None),
        new_pack=getattr(new, "build_source", None),
    )
    # ADR-064/PR G2: resolve severity into the same `GateOptions` the
    # release fan-out uses, then the canonical decision. No manual
    # exit-code-scheme selector to pass any more -- the algorithm is purely
    # derived from whether `severity_preset` (or a resolved gate pack) put a
    # severity setting in effect.
    gate = gate_workflow.resolve_release_gate_options(
        None,
        severity_preset=request.severity_preset,
        severity_abi_breaking=None,
        severity_potential_breaking=None,
        severity_quality_issues=None,
        severity_addition=None,
    )
    # Abort-axes-aware (plan P3): a typed caller's own `exit_decision` reports an `--abi3` evidence-contract abort too.
    exit_decision = gate_workflow.resolve_compare_exit_decision_with_abort_axes(
        result, gate.severity, gate.exit_code_scheme
    )

    # Installs the same gate onto result.contract_context; see
    # workflows.compare_gate_receipt's own docstring for the full account.
    from .workflows.compare_gate_receipt import install_resolved_gate_receipt

    install_resolved_gate_receipt(
        result, request, gate, pf_before_project_fold, suppression
    )

    # ADR-055 D2/D4: `suppression` is carried out so a front end applying a
    # post-classification concern (appcompat's `scope_diff_to_app`) reuses the
    # list this call already resolved instead of loading it a second time.
    return CompareResult(
        diff=result,
        old_snapshot=old,
        new_snapshot=new,
        suppression=suppression,
        exit_decision=exit_decision,
        severity_config=gate.severity,
    )


def run_compare_request(request: CompareRequest) -> CompareResult:
    """Compare two ABI inputs described by a :class:`CompareRequest`.

    The single classification chokepoint (ADR-037 D1/D2): every front-end
    builds a ``CompareRequest`` and calls this, so defaults cannot diverge
    between invocation paths. The keyword-argument :func:`run_compare` is a
    thin shim that builds the request and delegates here.

    Exactly the composition of this module's own two phases --
    :func:`resolve_compare_request` then :func:`classify_compare_pair`. A
    front end that must configure the run between them (the native
    ``compare`` CLI's ADR-049 ``resolve_and_apply``, whose ``--pack`` can
    move the policy file and severity levels the classification is scored
    under) calls the two phases directly rather than keeping a second
    resolution implementation.

    Returns a :class:`~abicheck.api_types.CompareResult` (ADR-055 D2), not the
    bare ``(DiffResult, old, new)`` tuple it returned before 0.6: a struct can
    gain a field without breaking positional callers, which a tuple cannot.
    ``CompareResult.as_tuple()`` reproduces the old shape for a caller that
    wants it back in one line.

    Raises:
        ValidationError: If the request fails :meth:`CompareRequest.validate`,
            or if ``env_matrix_path`` names a missing/malformed environment
            matrix -- resolved here, before :func:`resolve_compare_request`'s
            extraction work starts (Codex review, PR #1221 follow-up:
            previously resolved only inside :func:`classify_compare_pair`,
            after resolution had already run).
        PlanningError: See :func:`resolve_compare_request` — raised from
            inside its own call here.
        SnapshotError: If either input cannot be loaded.

    Moved here from ``service.py`` (CLI cleanup phase two, "PR B" slice 1,
    alongside :func:`run_compare`) once the latter's two new pack-forwarding
    parameters pushed that file over the AI-readiness file-size cap --
    re-exported from ``service.py`` unchanged, the same pattern
    ``resolve_compare_request``/``classify_compare_pair`` already use.

    ADR-068 §3 #19: ``request.budget_s`` (``None`` = unbounded) bounds both
    phases under one ``deadline.deadline_scope``. A typed caller gets a
    clear :class:`~abicheck.deadline.DeadlineExceeded` rather than a partial
    result -- ``cli_compare_helpers.run_compare`` is what maps this axis
    onto exit 5 for the CLI, which needs an exit code instead.
    """
    from . import deadline

    with deadline.deadline_scope(request.budget_s):
        # Resolve env_matrix_path before resolve_compare_request's own
        # extraction work starts, so a bad path fails fast; threaded into
        # classify_compare_pair so the file isn't read a second time.
        # __post_init__ still does zero file I/O -- this only reads it once
        # a full run_compare_request call actually reaches this point.
        resolved_env_matrix = request.effective_env_matrix()
        pair = resolve_compare_request(request)
        return classify_compare_pair(
            request, pair, resolved_env_matrix=resolved_env_matrix
        )


def run_compare(
    old_input: Path,
    new_input: Path,
    old_headers: list[Path] | None = None,
    new_headers: list[Path] | None = None,
    old_includes: list[Path] | None = None,
    new_includes: list[Path] | None = None,
    old_version: str = "",
    new_version: str = "",
    lang: str = "c++",
    frontend: str = "auto",
    suppress: Path | None = None,
    policy: str = "strict_abi",
    policy_file_path: Path | None = None,
    old_pdb_path: Path | None = None,
    new_pdb_path: Path | None = None,
    old_debug_roots: list[Path] | None = None,
    new_debug_roots: list[Path] | None = None,
    enable_debuginfod: bool = False,
    scope_to_public_surface: bool = True,
    force_public_symbols: set[str] | None = None,
    pattern_verdicts: bool = False,
    public_surface_allowlist: set[str] | None = None,
    debuginfod_url: str | None = None,
    diagnostic_comparison: bool = False,
    contract_evaluation: bool = False,
    include_dependencies: bool = True,
    contract_mode: str | None = None,
    pack_policy_overrides: dict[Any, Any] | None = None,
    pack_internal_namespaces: tuple[str, ...] | None = None,
    compile_context: CompileContext | None = None,
    depth: str | None = None,
    *,
    severity_preset: str | None = None,
    public_header_dirs: list[Path] | None = None,
    collapse_versioned_symbols: bool = False,
    project_policy_overrides: dict[Any, Any] | None = None,
    env_matrix: EnvironmentMatrix | None = None,
) -> CompareResult:
    """Compare two ABI inputs and return the classified diff result.

    Keyword-argument shim over :func:`run_compare_request`: it assembles a
    :class:`CompareRequest` from loose arguments and delegates, so existing
    callers keep working while the typed request is the real chokepoint
    (ADR-037 D2). New callers should build a ``CompareRequest`` directly.
    Trailing params (``debuginfod_url`` onward) are appended after every
    pre-existing one, so a positional caller keeps binding each argument to
    the same parameter it always did (Codex review, PR #551). Only
    ``severity_preset`` (the newest, never previously released) is actually
    ``*``-enforced keyword-only -- enforcing it retroactively for the older
    ones too would reject a real caller still passing one positionally, the
    public-API break that "keeps binding positionally" promise forbids
    (Codex review, fresh evidence, PR #1032). (Its sibling
    ``exit_code_scheme`` parameter was deleted in CLI cleanup phase two PR
    G2 -- the manual algorithm selector no longer exists anywhere.)
    ``include_dependencies`` applies to *both* sides — build a
    ``CompareRequest`` for a per-side override.

    ``pack_policy_overrides``/``pack_internal_namespaces``: an already-
    resolved ``--pack``'s ``policy.overrides``/``surface.internal_namespaces``
    contribution -- see ``CompareRequest.pack_policy_overrides``'s own
    docstring for what folds them in and why. ``None``/empty is a no-op.

    ``project_policy_overrides`` (ADR-068 §3 #23): weaker than both an
    explicit ``--policy <file>`` and ``pack_policy_overrides`` above --
    see ``CompareRequest.project_policy_overrides``. ``None``/empty is a
    no-op.

    ``compile_context`` is a both-sides :class:`~abicheck.compile_context.
    CompileContext` (the L2 cross-toolchain/frontend family --
    ``--ast-frontend``/``--compiler``/``--compiler-prefix``/
    ``--compiler-option``/``--sysroot``/``--nostdinc``/``--frontend-context``),
    applied identically to :class:`InputSpec.compile` on both sides -- the
    same both-sides-only granularity ``include_dependencies`` already has in
    this shim; a caller needing a genuine per-side override should build a
    :class:`CompareRequest` directly instead. ``None`` (the default) is a
    no-op, matching every pre-existing caller.

    ``depth`` forwards straight onto :class:`CompareRequest.depth` -- the
    release fan-out's ``_run_compare_pair`` needed a way to forward its own
    resolved ``--depth binary`` per pair; every other caller passes ``None``.

    ``severity_preset`` (Codex review): forwards onto ``CompareRequest``'s
    identically-named field; ``None`` is a no-op.

    ``public_header_dirs`` (CodeRabbit review, PR #1138): a project's
    ``scope.public_header_dirs``, folded unchanged into both sides'
    ``InputSpec.public_header_dirs`` -- closes the release fan-out's own
    gap, since ``cli_compare_release_pairwise._run_compare_pair`` calls
    this shim rather than ``cli_resolve._resolve_compare_snapshots``
    (which already threads this config key for a single-pair compare).
    ``None`` is a no-op, matching every pre-existing caller.

    ``env_matrix`` (ADR-020b / ADR-068 D5): the already-resolved declared
    deployment matrix (`.abicheck.yml`'s `deployment:`, former
    `--env-matrix FILE`), forwarded onto `CompareRequest`'s identically-
    named field so a release fan-out member gets the same declared-floor
    reclassification a single-pair compare would. `None` is a no-op.

    ``collapse_versioned_symbols`` (Codex review, fresh evidence): forwards
    onto ``CompareRequest``'s identically-named field -- closes the release
    fan-out's own gap the same way ``public_header_dirs`` above did: a
    ``scope.collapse_versioned_symbols: true`` project config resolved to
    this shim's caller previously had no channel to reach a directory/
    package member's own per-library compare, so each member silently kept
    the field at its ``False`` default and could report a version-renamed
    symbol as a spurious removal/addition where the identical scalar
    comparison collapsed it. ``False`` (the default) is a no-op, matching
    every pre-existing caller.

    Returns:
        A :class:`~abicheck.api_types.CompareResult`. This returned the bare
        ``(DiffResult, old, new)`` tuple before 0.6; ``.as_tuple()`` gives that
        shape back for a caller that unpacks positionally.
    Raises:
        SnapshotError: If either input cannot be loaded.
        ValidationError: If inputs have unrecognised formats.
    """
    _public_header_dirs = tuple(public_header_dirs or ())
    request = CompareRequest(
        old=InputSpec(
            path=old_input,
            headers=tuple(old_headers or ()),
            includes=tuple(old_includes or ()),
            version=old_version,
            pdb=old_pdb_path,
            debug_roots=tuple(old_debug_roots or ()),
            include_dependencies=include_dependencies,
            compile=compile_context,
            public_header_dirs=_public_header_dirs,
        ),
        new=InputSpec(
            path=new_input,
            headers=tuple(new_headers or ()),
            includes=tuple(new_includes or ()),
            version=new_version,
            pdb=new_pdb_path,
            debug_roots=tuple(new_debug_roots or ()),
            include_dependencies=include_dependencies,
            compile=compile_context,
            public_header_dirs=_public_header_dirs,
        ),
        lang=lang,
        frontend=frontend,
        policy=policy,
        policy_file_path=policy_file_path,
        suppress=suppress,
        scope_public=scope_to_public_surface,
        force_public_symbols=(
            frozenset(force_public_symbols) if force_public_symbols else None
        ),
        public_surface_allowlist=(
            frozenset(public_surface_allowlist)
            if public_surface_allowlist is not None
            else None
        ),
        pattern_verdicts=pattern_verdicts,
        enable_debuginfod=enable_debuginfod,
        debuginfod_url=debuginfod_url,
        diagnostic_comparison=diagnostic_comparison,
        contract_evaluation=contract_evaluation,
        contract_mode=contract_mode,
        pack_policy_overrides=(
            tuple(pack_policy_overrides.items()) if pack_policy_overrides else None
        ),
        pack_internal_namespaces=pack_internal_namespaces,
        project_policy_overrides=(
            tuple(project_policy_overrides.items())
            if project_policy_overrides
            else None
        ),
        depth=depth,
        severity_preset=severity_preset,
        collapse_versioned_symbols=collapse_versioned_symbols,
        env_matrix=env_matrix,
    )
    return run_compare_request(request)
