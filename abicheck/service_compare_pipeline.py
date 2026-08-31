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

* Everything this module needs from ``service`` is looked up **through the
  module object at call time** (``from . import service`` inside the function),
  never bound at import time. That is what keeps
  ``monkeypatch.setattr(service, "resolve_input", ...)`` — and the same for
  ``compare_snapshots`` — affecting this code exactly as it did when the body
  lived in ``service.py``. Helpers ``service`` itself only *re-exports*
  (``pair_wide_cxx20_std_override``, ``split_public_header_inputs``,
  ``populate_pair_dependency_info``) are imported straight from the module
  that defines them instead: nothing patches them through ``service``, and
  going through a re-export would make them look like public service API
  (mypy's ``no_implicit_reexport``) when they are not. The import is also
  function-local so this module does not join ``service``'s import cycle
  (AGENTS.md "What NOT to do").
* This module holds no state and makes no policy decisions of its own; every
  behavioural rule in it moved here verbatim from ``service.run_compare_request``.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .api_types import CompareRequest, CompareResult, InputSpec, required_path
from .compile_context import CompileContext
from .confidence import note_if_same_binary_compared
from .dependency_info import populate_pair_dependency_info
from .errors import ValidationError
from .workflows.artifact.execute import (
    enforce_requested_depth,
    resolve_side_snapshot,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .model import AbiSnapshot
    from .service_compare_evidence import SideEvidence

__all__ = [
    "ResolvedComparePair",
    "classify_compare_pair",
    "resolve_compare_request",
    "resolve_sides_sequentially",
    "run_compare",
    "run_compare_request",
]


@dataclasses.dataclass(frozen=True)
class ResolvedComparePair:
    """Both sides of a comparison, resolved and ready to classify.

    The seam between :func:`resolve_compare_request` and
    :func:`classify_compare_pair`. It carries what classification genuinely
    needs beyond the two snapshots — the detected binary formats (for a front
    end reporting on them) and each side's resolved
    :class:`~abicheck.service_compare_evidence.SideEvidence` (whose
    ``collect_mode`` drives the embedded build-source diff).
    """

    old: AbiSnapshot
    new: AbiSnapshot
    old_fmt: str | None
    new_fmt: str | None
    old_evidence: SideEvidence
    new_evidence: SideEvidence


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
    from .service_scan import pair_wide_cxx20_std_override

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
        ValidationError: If the request fails :meth:`CompareRequest.validate`,
            names a frontend with no extractor for its evidence, or requests a
            ``depth`` the resolved snapshots did not reach.
        SnapshotError: If either input cannot be loaded.
    """
    from . import service, service_compare_evidence as _sce
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
    from .api_types import HEADER_AST_FRONTENDS

    frontend_lower = request.frontend.lower()
    header_backend = (
        frontend_lower if frontend_lower in HEADER_AST_FRONTENDS else "auto"
    )
    # `validate()` above already rejected a `None` path on either side of a
    # comparison (`_path_required_errors(..., source_only_allowed=False)`);
    # `required_path` is the one place that narrowing is spelled.
    old_fmt = service.detect_binary_format(required_path(request.old, "old"))
    new_fmt = service.detect_binary_format(required_path(request.new, "new"))
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
    ) -> AbiSnapshot:
        return resolve_side_snapshot(
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
        )

    def _resolve_old_side() -> AbiSnapshot:
        return _resolve_side(
            request.old,
            old_evidence,
            old_fmt,
            old_public_headers,
            old_public_header_dirs,
        )

    def _resolve_new_side() -> AbiSnapshot:
        return _resolve_side(
            request.new,
            new_evidence,
            new_fmt,
            new_public_headers,
            new_public_header_dirs,
        )

    if not allow_parallel or resolve_sides_sequentially(request):
        old = _resolve_old_side()
        new = _resolve_new_side()
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            old_future = pool.submit(_resolve_old_side)
            new_future = pool.submit(_resolve_new_side)
            old = old_future.result()
            new = new_future.result()

    # ADR-055 D1: `--follow-deps`'s transitive DependencyInfo. After both sides
    # resolve, not inside `_resolve_side`: it reads the on-disk ELF, so it
    # gains nothing from the extraction threads.
    populate_pair_dependency_info(request, old, new, old_fmt=old_fmt, new_fmt=new_fmt)
    enforce_requested_depth(request.depth, (("old", old), ("new", new)))
    return ResolvedComparePair(
        old=old,
        new=new,
        old_fmt=old_fmt,
        new_fmt=new_fmt,
        old_evidence=old_evidence,
        new_evidence=new_evidence,
    )


def classify_compare_pair(
    request: CompareRequest, pair: ResolvedComparePair
) -> CompareResult:
    """Classify an already-resolved pair — the second half of ``run_compare_request``.

    Loads the request's suppression list and policy file, diffs whatever
    build-source evidence the snapshots carry into ``extra_changes`` (without
    it a source-only change reads as artifact-only compatible), classifies
    through the Tier-2 ``compare_snapshots`` chokepoint, and attaches the
    coverage/metrics the diff itself does not produce.

    A front end that must configure the run between the two phases (the native
    ``compare`` CLI's ADR-049 ``resolve_and_apply``, which can move the policy
    file and severity levels a ``--pack`` selects) calls
    :func:`resolve_compare_request` and then classifies on its own terms
    instead of calling this; everything else composes the two through
    :func:`abicheck.service.run_compare_request`.
    """
    from . import service
    from .buildsource.evidence_report import (
        attach_evidence_metrics,
        prepare_embedded_build_source,
    )

    old, new = pair.old, pair.new
    suppression, pf = service.load_suppression_and_policy(
        request.suppress, request.policy, request.policy_file_path
    )
    # CLI cleanup phase two, PR B slice 1: fold an already-resolved pack's
    # policy/contract-surface contributions into the loaded PolicyFile, the
    # same way `pack_application.policy_file_with_packs` already does for the
    # single-pair `compare` CLI. Every field is `None` for every pre-existing
    # caller (the CLI's own single-pair path folds its packs before calling
    # `compare_snapshots` directly and never sets these), so this is a no-op
    # unless a caller -- today, the directory/package release fan-out --
    # populated `CompareRequest.pack_policy_overrides`/
    # `pack_internal_namespaces`. See `CompareRequest.pack_policy_overrides`'s
    # own docstring for why this is the one chokepoint to apply it at.
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
    result = service.compare_snapshots(
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
        pattern_verdicts=request.pattern_verdicts,
        reconcile_build_context=request.reconcile_build_context,
        env_matrix=service.load_env_matrix(request.env_matrix_path),
        diagnostic_comparison=request.diagnostic_comparison,
        contract_evaluation=request.contract_evaluation,
        contract_mode=request.contract_mode,
    )
    if layer_coverage_rows:
        result.layer_coverage = layer_coverage_rows
    attach_evidence_metrics(result, evidence_metrics, extra_changes or [])
    # Hash through the full GNU ld linker-script chain to its final resolved
    # target -- resolve_side_snapshot() already followed the identical chain
    # to produce `old`/`new` above -- so a (possibly multi-hop) script vs.
    # its target DSO given as the other `CompareRequest` side still reads as
    # byte-identical (Codex review, fresh evidence; mirrors the same fix on
    # `cli_scan_baseline._run_baseline_compare`).
    from .binary_utils import resolve_linker_script_chain

    def _hashable_path(p: Path) -> Path:  # a text snapshot/manifest can coincidentally match the INPUT()/GROUP() probe -- skip linker-script resolution for it (Codex review)
        return p if service.sniff_text_format(p) in ("json", "perl", "symvers") else resolve_linker_script_chain(p)

    result.old_metadata = service.collect_metadata(_hashable_path(required_path(request.old, "old")))
    result.new_metadata = service.collect_metadata(_hashable_path(required_path(request.new, "new")))
    note_if_same_binary_compared(result)

    # P0.4 follow-up (P2 review, discussion_r3787839902): `DiffResult.
    # requested_depth`/`analysis_assurance.requested_depth`/`depth_satisfied`
    # were only ever stamped by the CLI's own `cli_compare_helpers.
    # _report_compare_result` -- this shared classification half of
    # `run_compare_request` (the typed Python API / MCP `abi_compare` entry
    # point) never populated either, so a direct caller passing an explicit,
    # successfully-resolved `CompareRequest.depth` still read
    # `requested_depth=None`/`depth_satisfied=None` and could report
    # `status="complete"` even though a depth was genuinely requested.
    # `request.depth` is the same "explicit override, never inferred"
    # sentinel `CompareRequest.depth`'s own field comment documents (`None`
    # when the caller never set it -- there is no separate Click
    # parameter-source concept for a plain dataclass, so `None`-vs-set is
    # already the request's own "was this explicit" signal); only stamp it
    # when it is not `None`, mirroring the CLI's identical `if depth is not
    # None` guard. Recomputes `analysis_assurance` the same way
    # `checker.compare()` itself does (`old_pack`/`new_pack` from each
    # snapshot's own *embedded* `build_source` -- this path has no
    # out-of-band `--old/new-build-info`/`--old/new-sources` pack directory
    # of the CLI's own to fold in, since `InputSpec.sources`/`build_info`
    # are embedded into `old`/`new` before `resolve_compare_request` ever
    # returns), so `layer_coverage`/`requested_depth` -- both only known
    # after `compare_snapshots` returns -- are reflected too.
    #
    # P0.4 follow-up (P2 review): `CompareRequest.validation_errors()`
    # (`_depth_errors` in `api_types.py`) accepts `depth` case-insensitively
    # (`depth.lower() in USER_DEPTHS`), and every consumer of the *value*
    # (`enforce_requested_depth`'s own `depth.lower()` lookup into
    # `_DEPTH_RANK` above, line 194's `request.depth.lower() == "binary"`
    # check) normalizes case before using it -- so a valid, successfully
    # validated request like `depth="HEADERS"` reached this assignment with
    # its original casing preserved. That un-normalized string then broke
    # every downstream reader that expects the lowercase
    # `EVIDENCE_DEPTH_VALUES` spelling: `compute_analysis_assurance()`'s
    # depth-rank lookup silently missed (falling back to a wrong default),
    # and `validate_evidence_depth()` (every JSON reporter) raised
    # `ValueError` outright. Stamp the same lowercased form the rest of this
    # module already normalizes to, not the raw request string.
    if request.depth is not None:
        result.requested_depth = request.depth.lower()
    from .analysis_assurance import compute_analysis_assurance

    result.analysis_assurance = compute_analysis_assurance(
        result,
        old,
        new,
        old_pack=getattr(old, "build_source", None),
        new_pack=getattr(new, "build_source", None),
    )
    # ADR-055 D2/D4: `suppression` is carried out so a front end applying a
    # post-classification concern (appcompat's `scope_diff_to_app`) reuses the
    # list this call already resolved instead of loading it a second time.
    return CompareResult(
        diff=result, old_snapshot=old, new_snapshot=new, suppression=suppression
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
        ValidationError: If the request fails :meth:`CompareRequest.validate`.
        SnapshotError: If either input cannot be loaded.

    Moved here from ``service.py`` (CLI cleanup phase two, "PR B" slice 1,
    alongside :func:`run_compare`) once the latter's two new pack-forwarding
    parameters pushed that file over the AI-readiness file-size cap --
    re-exported from ``service.py`` unchanged, the same pattern
    ``resolve_compare_request``/``classify_compare_pair`` already use.
    """
    return classify_compare_pair(request, resolve_compare_request(request))


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
) -> CompareResult:
    """Compare two ABI inputs and return the classified diff result.

    Keyword-argument shim over :func:`run_compare_request`: it assembles a
    :class:`CompareRequest` from loose arguments and delegates, so existing
    callers keep working while the typed request is the real chokepoint
    (ADR-037 D2). New callers should build a ``CompareRequest`` directly.
    Trailing keyword-only params (``debuginfod_url`` onward) are appended
    after every pre-existing one, never alongside a thematically-closer
    neighbor, so a positional caller keeps binding each argument to the same
    parameter it always did (Codex review, PR #551). ``include_dependencies``
    applies to *both* sides — build a ``CompareRequest`` for a per-side override.

    ``pack_policy_overrides``/``pack_internal_namespaces`` (CLI cleanup phase
    two, "PR B" slice 1): an already-resolved ``--pack``'s
    ``policy.overrides``/``surface.internal_namespaces`` contribution --
    see ``CompareRequest.pack_policy_overrides``'s own docstring for what
    folds them in and why. ``None``/empty on both is a no-op, matching every
    pre-existing caller.

    ``compile_context`` is a both-sides :class:`~abicheck.compile_context.
    CompileContext` (the L2 cross-toolchain/frontend family --
    ``--ast-frontend``/``--compiler``/``--compiler-prefix``/
    ``--compiler-option``/``--sysroot``/``--nostdinc``/``--frontend-context``),
    applied identically to :class:`InputSpec.compile` on both sides -- the
    same both-sides-only granularity ``include_dependencies`` already has in
    this shim; a caller needing a genuine per-side override should build a
    :class:`CompareRequest` directly instead. ``None`` (the default) is a
    no-op, matching every pre-existing caller.

    Returns:
        A :class:`~abicheck.api_types.CompareResult`. This returned the bare
        ``(DiffResult, old, new)`` tuple before 0.6; ``.as_tuple()`` gives that
        shape back for a caller that unpacks positionally.
    Raises:
        SnapshotError: If either input cannot be loaded.
        ValidationError: If inputs have unrecognised formats.
    """
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
    )
    return run_compare_request(request)
