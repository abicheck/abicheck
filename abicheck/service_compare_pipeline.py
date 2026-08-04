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
from typing import TYPE_CHECKING

from .api_types import CompareResult
from .dependency_info import populate_pair_dependency_info
from .errors import SnapshotError, ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .api_types import CompareRequest, InputSpec
    from .compile_context import CompileContext
    from .model import AbiSnapshot
    from .service_compare_evidence import SideEvidence

__all__ = [
    "ResolvedComparePair",
    "classify_compare_pair",
    "resolve_compare_request",
    "resolve_sides_sequentially",
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
    return request.old.dump_manifest is not None or request.new.dump_manifest is not None


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


def _pair_compile_context(
    request: CompareRequest, lang: str
) -> CompileContext | None:
    """The pair-wide C++20 dialect override, if the header sets imply one."""
    from .compile_context import CompileContext
    from .service_scan import pair_wide_cxx20_std_override

    override = pair_wide_cxx20_std_override(
        lang,
        list(request.old.headers) + _manifest_forced_includes(request.old.dump_manifest),
        list(request.new.headers) + _manifest_forced_includes(request.new.dump_manifest),
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


def _is_raw_source_tree(path: Path | None) -> bool:
    """True for a source tree needing real extraction — not a prebuilt pack."""
    from .buildsource.inline import is_pack_dir
    from .cli_buildsource_helpers import _is_inputs_pack_dir

    return path is not None and not (is_pack_dir(path) or _is_inputs_pack_dir(path))


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
    """
    from . import service_compare_evidence as _sce

    if frontend_lower == "android" and any(
        _is_raw_source_tree(side.sources) for side in (request.old, request.new)
    ):
        raise ValidationError(
            "the 'android' AST frontend's source-ABI replay is not yet wired "
            "into run_compare_request's inline evidence collection for a raw "
            "source tree -- pass a prebuilt evidence pack directory instead, "
            "or use has_sources=True with no inline sources/build_info."
        )
    if request.depth is not None and request.depth.lower() == "source":
        for side, evidence in ((request.old, old_evidence), (request.new, new_evidence)):
            if (
                _is_raw_source_tree(side.sources)
                and _sce.effective_frontend(evidence.compile, header_backend) == "hybrid"
            ):
                raise ValidationError(
                    "depth='source' is incompatible with the 'hybrid' AST "
                    "frontend: L4 source-ABI replay has no dual-backend hybrid "
                    "extractor. Use 'castxml' or 'clang' for a depth='source' "
                    "request."
                )


def _enforce_requested_depth(
    request: CompareRequest, old: AbiSnapshot, new: AbiSnapshot
) -> None:
    """Fail when an explicit ``depth`` was requested but not actually reached.

    Mirrors ``dump``'s own ``check_requested_depth_satisfied`` hard-fail, but
    raises ``ValidationError`` (a Tier-2 API has no ``ClickException``
    concept). Without it, a raw input that could not reach the requested rung
    — no usable compile database, extractor, or linkable declarations —
    silently diffed whatever weaker evidence ``embed_build_source`` produced.

    Known, accepted limitation (Codex review, not fixed here): this is a
    floor, not a ceiling. A side whose input is an already-serialized JSON
    snapshot with richer embedded evidence than ``depth`` requested still
    diffs with all of it — ``resolve_input``'s ``fmt == "json"`` branch
    returns ``load_snapshot(path)`` verbatim, matching the CLI's own
    long-documented default, which ``--depth`` has never projected down for a
    pre-built snapshot either.
    """
    if request.depth is None:
        return
    from .cli_dump_helpers import _DEPTH_RANK, _gated_source_label

    # validate() already restricts depth to USER_DEPTHS.
    requested_rank = _DEPTH_RANK.get(request.depth.lower(), 0)
    for side_label, snap in (("old", old), ("new", new)):
        effective = _gated_source_label(snap.build_source, snap)
        if _DEPTH_RANK.get(effective, 0) < requested_rank:
            raise ValidationError(
                f"depth={request.depth!r} was requested for the {side_label} "
                f"side but the resolved snapshot only reached {effective!r} "
                "evidence depth. Supply the evidence this rung needs (headers, "
                "a build/compile database, or --sources with linkable "
                "declarations) or lower depth to match what is actually "
                "available."
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

    request.validate()
    # validate() accepts lang case-insensitively; the ELF dump path does
    # case-sensitive `lang == "c"` checks, so normalise here. `android` (no
    # header-AST path) falls back to "auto" for the binary dump.
    lang = request.lang.lower()
    from .api_types import HEADER_AST_FRONTENDS

    frontend_lower = request.frontend.lower()
    header_backend = (
        frontend_lower if frontend_lower in HEADER_AST_FRONTENDS else "auto"
    )
    old_fmt = service.detect_binary_format(request.old.path)
    new_fmt = service.detect_binary_format(request.new.path)
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
        snap = service.resolve_input(
            side.path,
            evidence.headers,
            list(side.includes),
            side.version,
            lang,
            is_elf=True if fmt == "elf" else None,
            pdb_path=side.pdb,
            debug_roots=list(side.debug_roots) or None,
            enable_debuginfod=request.enable_debuginfod,
            debuginfod_url=request.debuginfod_url,
            header_backend=header_backend,
            compile=evidence.compile,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            include_dependencies=side.include_dependencies,
            dump_manifest=evidence.dump_manifest,
            follow_linker_scripts=side.follow_linker_scripts,
            dwarf_only=request.dwarf_only,
            debug_format=debug_format,
            include_labels=dict(request.include_labels) or None,
            notify=notify,
        )
        if side.sources or side.build_info:
            _embed_side_build_source(
                snap,
                side,
                evidence,
                header_backend,
                public_headers,
                public_header_dirs,
            )
        return snap

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
    populate_pair_dependency_info(
        request, old, new, old_fmt=old_fmt, new_fmt=new_fmt
    )
    _enforce_requested_depth(request, old, new)
    return ResolvedComparePair(
        old=old,
        new=new,
        old_fmt=old_fmt,
        new_fmt=new_fmt,
        old_evidence=old_evidence,
        new_evidence=new_evidence,
    )


def _embed_side_build_source(
    snap: AbiSnapshot,
    side: InputSpec,
    evidence: SideEvidence,
    header_backend: str,
    public_headers: list[Path],
    public_header_dirs: list[Path],
) -> None:
    """Embed one side's inline L3-L5 build/source evidence into *snap*.

    Same public roots as ``resolve_input``, plus a ``dump_manifest``'s
    *declared-public* roots only (a manifest's project-owned TU includes are
    private, hence ``dump_manifest_public_roots`` rather than
    ``dump_manifest_header_roots``).

    A malformed pack raises ``click.ClickException`` deep inside
    ``embed_build_source`` — no place in this Tier-2 API's
    ``ValidationError``/``SnapshotError`` contract, so it is translated here
    (Codex review).
    """
    import click

    from . import service_compare_evidence as _sce
    from .cli_buildsource import embed_build_source
    from .dumper_scoping import dump_manifest_public_roots

    try:
        embed_build_source(
            snap,
            build_info=side.build_info,
            sources=side.sources,
            collect_mode=evidence.collect_mode,
            extractor=_sce.effective_frontend(evidence.compile, header_backend),
            public_headers=tuple(str(p) for p in public_headers),
            public_header_dirs=tuple(str(p) for p in public_header_dirs)
            + tuple(str(p) for p in dump_manifest_public_roots(evidence.dump_manifest)),
            quiet=True,
        )
    except click.ClickException as exc:
        raise SnapshotError(str(exc)) from exc


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
    from .cli_buildsource import attach_evidence_metrics, prepare_embedded_build_source

    old, new = pair.old, pair.new
    suppression, pf = service.load_suppression_and_policy(
        request.suppress, request.policy, request.policy_file_path
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
        quiet=True,
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
    attach_evidence_metrics(result, evidence_metrics, extra_changes or [], quiet=True)
    result.old_metadata = service.collect_metadata(request.old.path)
    result.new_metadata = service.collect_metadata(request.new.path)
    # ADR-055 D2/D4: `suppression` is carried out so a front end applying a
    # post-classification concern (appcompat's `scope_diff_to_app`) reuses the
    # list this call already resolved instead of loading it a second time.
    return CompareResult(
        diff=result, old_snapshot=old, new_snapshot=new, suppression=suppression
    )
