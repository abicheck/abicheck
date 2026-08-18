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

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .errors import ValidationError
from .service_input_resolution import (
    enforce_requested_depth,
    is_raw_source_tree,
    reject_hybrid_source_frontend,
    resolve_side_snapshot,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .api_types import DumpRequest
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
    """

    request: DumpRequest
    lang: str
    lang_explicit: bool
    header_backend: str
    fmt: str | None
    debug_format: str | None
    requested_depth: str | None
    evidence: SideEvidence
    public_headers: tuple[Path, ...]
    public_header_dirs: tuple[Path, ...]

    @property
    def collect_mode(self) -> str:
        """The effective build/source evidence level ``depth`` resolved to."""
        return self.evidence.collect_mode

    @property
    def headers(self) -> tuple[Path, ...]:
        """The resolved public-header set (files only; see ``public_header_dirs``)."""
        return tuple(self.evidence.headers)

    @property
    def effective_header_backend(self) -> str:
        """The *concrete* header-AST backend execution will actually use.

        ``header_backend`` alone is not this (Codex review, fresh evidence):
        ``service.py``'s own ``eff_backend`` computation gives an explicit
        ``evidence.compile.frontend`` (e.g. from ``ABICHECK_AST_FRONTEND`` or
        a per-input ``--compile-frontend`` override) precedence over the bare
        ``header_backend`` arg, and resolves ``"auto"`` to a concrete backend
        either way — so a dry-run render of ``header_backend`` alone can name
        a different frontend than execution resolves to. Computed the
        identical way execution does, via
        :func:`~abicheck.service_compare_evidence.effective_frontend`.
        """
        from .service_compare_evidence import effective_frontend

        return effective_frontend(self.evidence.compile, self.header_backend)


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
    """

    resolved: ResolvedDumpRequest
    snapshot: AbiSnapshot
    effective_depth: str


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
    """
    from . import service, service_compare_evidence as _sce
    from .api_types import HEADER_AST_FRONTENDS
    from .header_utils import split_public_header_inputs

    request.validate()
    # validate() accepts lang case-insensitively; the ELF dump path does
    # case-sensitive `lang == "c"` checks, so normalise here. `android` (no
    # header-AST path) falls back to "auto" for the binary dump.
    lang = request.lang.lower()
    frontend_lower = request.frontend.lower()
    header_backend = (
        frontend_lower if frontend_lower in HEADER_AST_FRONTENDS else "auto"
    )
    side = request.input
    fmt = service.detect_binary_format(side.path)
    debug_format = _sce.normalized_debug_format(request)
    _sce.reject_debug_format_for_binaries(debug_format, (("input", fmt),))

    evidence = _sce.resolve_dump_request_evidence(request)
    _reject_unsupported_frontends(request, header_backend, evidence)

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

    return ResolvedDumpRequest(
        request=request,
        lang=lang,
        lang_explicit=request.lang_explicit,
        header_backend=header_backend,
        fmt=fmt,
        debug_format=debug_format,
        requested_depth=request.depth,
        evidence=evidence,
        public_headers=tuple(public_headers),
        public_header_dirs=tuple(public_header_dirs),
    )


def execute_dump_request(
    resolved: ResolvedDumpRequest,
    *,
    notify: Callable[[str], None] | None = None,
) -> DumpResult:
    """Execute a :class:`ResolvedDumpRequest` — steps 3-5 of
    :func:`run_dump_request`'s own docstring (``resolve_input``, the
    dependency walk, the depth floor).

    *notify* is forwarded to :func:`abicheck.service.resolve_input` for
    user-facing progress notes ("following a linker script"); ``None`` logs
    them instead.

    Raises:
        ValidationError: If *resolved* requests a ``depth`` the resolved
            snapshot did not reach.
        SnapshotError: If the input cannot be loaded.
    """
    from .cli_dump_helpers import _gated_source_label
    from .dependency_info import populate_side_dependency_info

    request = resolved.request
    side = request.input

    snap = resolve_side_snapshot(
        side,
        resolved.evidence,
        lang=resolved.lang,
        lang_explicit=resolved.lang_explicit,
        # The *concrete* backend, not the bare requested one (Codex review,
        # fresh evidence): resolved.header_backend can be "auto", which
        # service.py's own eff_backend computation would otherwise re-resolve
        # at execution time via a fresh ABICHECK_AST_FRONTEND env read --
        # letting execution silently disagree with what resolution (and any
        # dry-run render of it) already reported as effective_header_backend
        # if the environment changed in between. Passing the already-resolved
        # concrete value pins the decision resolution made.
        header_backend=resolved.effective_header_backend,
        fmt=resolved.fmt,
        public_headers=list(resolved.public_headers),
        public_header_dirs=list(resolved.public_header_dirs),
        enable_debuginfod=request.enable_debuginfod,
        debuginfod_url=request.debuginfod_url,
        dwarf_only=request.dwarf_only,
        debug_format=resolved.debug_format,
        include_labels=dict(request.include_labels) or None,
        notify=notify,
    )

    if request.follow_dependencies:
        populate_side_dependency_info(
            snap,
            side,
            resolved.fmt,
            list(request.dependency_search_paths),
            request.ld_library_path,
        )

    enforce_requested_depth(resolved.requested_depth, (("input", snap),))
    effective_depth = _gated_source_label(snap.build_source, snap)
    return DumpResult(resolved=resolved, snapshot=snap, effective_depth=effective_depth)
