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

"""One side's inline L3-L5 build/source embed (:func:`embed_side_build_source`).

Split out of :mod:`abicheck.workflows.artifact.execute` (which keeps the side
resolution that calls it) so each stays under the production file-size cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...errors import SnapshotError, ValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from ...compile_context import CompileContext
    from ...model import AbiSnapshot
    from ...service_compare_evidence import SideEvidence
    from ..request_inputs import InputSpec


def embed_side_build_source(
    snap: AbiSnapshot,
    side: InputSpec,
    evidence: SideEvidence,
    header_backend: str,
    public_headers: list[Path],
    public_header_dirs: list[Path],
    *,
    changed_paths: tuple[str, ...] = (),
    build_config: Path | None = None,
    build_config_explicit: bool = True,
    build_query: str | None = None,
    build_compile_db: str | None = None,
    source_frontend_compile: CompileContext | None = None,
) -> None:
    """Embed one side's inline L3-L5 build/source evidence into *snap*.

    Same public roots as ``resolve_input``, plus a ``dump_manifest``'s
    *declared-public* roots only (a manifest's project-owned TU includes are
    private, hence ``dump_manifest_public_roots`` rather than
    ``dump_manifest_header_roots``).

    A malformed pack raises ``SnapshotError`` from
    ``buildsource.embed.embed_build_source``, which needs no translation; a
    malformed config raises ``ValidationError`` and is flattened onto
    ``SnapshotError`` here
    (Codex review).

    *changed_paths*: an optional pass-through to ``embed_build_source``
    scoping L4 replay to the paths a change touched (ADR-043 D7).

    *build_config*/*build_query*/*build_compile_db* (Codex review, fresh
    evidence): the identical build inputs the caller already resolved for
    the L2 seed (:func:`_resolve_side_snapshot_impl` forwards its own
    already-gated values here — see :func:`_gated_build_query_inputs`, the
    single place *build_config*/*build_query* are actually authorized).
    Without this, the L2 header-AST parse and the L3-L5 embed could resolve
    *different* build configurations for the same input — the L2 seed using
    the caller's explicit config while this embed step fell back to
    auto-discovery — so the snapshot's own evidence layers could silently
    describe two different builds, and an explicitly requested depth could
    fail to be satisfied despite the caller having supplied exactly what it
    needed.

    *source_frontend_compile*: the context whose ``gcc_path``/``gcc_prefix``
    selects the L4 replay compiler, when it is not ``evidence.compile`` --
    ``dump`` passes the *folded* (post-P0.3) context its L2 header AST was
    actually pointed at. The two differ only when the caller set neither
    selector and the matched compile unit named an MSVC/clang-cl driver.
    """
    import abicheck.service_compare_evidence as _sce

    from ...buildsource.embed import embed_build_source
    from ...dumper_clang import resolve_source_frontend_clang_bin
    from ...extract.dump_manifest_roots import dump_manifest_public_roots

    ctx = evidence.compile
    frontend_ctx = (
        source_frontend_compile if source_frontend_compile is not None else ctx
    )
    manifest_roots = dump_manifest_public_roots(evidence.dump_manifest)
    try:
        embed_build_source(
            snap,
            build_info=side.build_info,
            sources=side.sources,
            build_config=build_config,
            build_config_explicit=build_config_explicit,
            build_query=build_query,
            build_compile_db=build_compile_db,
            build_targets=side.build_targets,
            collect_mode=evidence.collect_mode,
            changed_paths=changed_paths,
            extractor=_sce.effective_frontend(evidence.compile, header_backend),
            # L4 source-ABI replay must invoke the compiler this input's own L2
            # header AST was pointed at (`gcc_path`/`gcc_prefix`), not
            # `embed_build_source`'s bare "clang" default -- the same fix
            # the retired `scan_engine` and the `dump` CLI carry. Without it a
            # typed request naming a non-default toolchain (e.g. icpx) replayed
            # L4 through a plain "clang" that may not understand the real
            # build's flags, so an omitted `depth` silently returned a weaker
            # snapshot and an explicit `depth="source"` failed (Codex review).
            # `exclude_cl_style=False` because L4 re-drives a CL compile unit
            # with `--driver-mode=cl` itself; only the S2 pre-scan needs the
            # exclusion.
            clang_bin=resolve_source_frontend_clang_bin(
                frontend_ctx.gcc_path if frontend_ctx else None,
                frontend_ctx.gcc_prefix if frontend_ctx else None,
                exclude_cl_style=False,
            ),
            public_headers=tuple(str(p) for p in public_headers),
            public_header_dirs=tuple(str(p) for p in public_header_dirs)
            + tuple(str(p) for p in manifest_roots),
        )
    except ValidationError as exc:
        # The engine keeps usage and operational errors distinct (the CLI needs
        # 64 vs 1); this surface has always flattened both onto SnapshotError,
        # so preserve that. SnapshotError itself propagates unchanged.
        raise SnapshotError(str(exc)) from exc
