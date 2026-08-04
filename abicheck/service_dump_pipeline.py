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

    from .api_types import DumpRequest
    from .model import AbiSnapshot
    from .service_compare_evidence import SideEvidence

__all__ = ["run_dump_request"]


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

    Raises:
        ValidationError: If the request fails :meth:`DumpRequest.validate`,
            names a frontend with no extractor for its evidence, or requests a
            ``depth`` the resolved snapshot did not reach.
        SnapshotError: If the input cannot be loaded.
    """
    from . import service, service_compare_evidence as _sce
    from .api_types import HEADER_AST_FRONTENDS
    from .dependency_info import populate_side_dependency_info
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

    snap = resolve_side_snapshot(
        side,
        evidence,
        lang=lang,
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

    if request.follow_dependencies:
        populate_side_dependency_info(
            snap,
            side,
            fmt,
            list(request.dependency_search_paths),
            request.ld_library_path,
        )

    enforce_requested_depth(request.depth, (("input", snap),))
    return snap
