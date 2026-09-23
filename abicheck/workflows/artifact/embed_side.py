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
    from collections.abc import Callable
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
    defer_cleanup: list[Callable[[], None]] | None = None,
    source_extractor: str | None = None,
    source_frontend_compile: CompileContext | None = None,
    expand_public_header_roots: bool = False,
    l4_public_headers: list[Path] | None = None,
    l4_public_header_dirs: list[Path] | None = None,
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

    *changed_paths* (PR 3A, dump/scan resolver convergence): an optional
    pass-through to ``embed_build_source``, defaulted to its existing no-op
    value so every pre-existing caller (``compare``, ``dump``'s typed
    pipeline) is unaffected — only ``scan``'s candidate-side resolution
    passes a non-default value, for its POI-focused L4 replay scoping.

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

    The last four parameters exist so ``scan``'s candidate resolution can route
    through this one primitive without any of its own long-standing behaviour
    changing underneath it (CLI cleanup phase two, PR 3A). Each defaults to
    exactly what this function did before, so every pre-existing caller
    (``compare``, ``dump``'s typed pipeline) is bit-for-bit unaffected:

    * *defer_cleanup* — ``scan`` owns the command-lifetime cleanup list its
      collection's temp build dirs are drained from; ``compare``/``dump`` let
      ``embed_build_source`` drain its own.
    * *source_extractor* — the L4 replay frontend. ``None`` keeps this
      function's own :func:`service_compare_evidence.effective_frontend`
      resolution. ``scan`` passes ``"auto"``, which
      ``buildsource.inline._make_source_extractor`` reads as clang, because
      that is what ``scan`` has always done — making it match the other
      resolvers would newly *require* castxml for a ``scan --depth source``
      that works with clang today, a real behaviour change for real users
      that cannot be verified without a castxml-capable lane (recorded as an
      open divergence in the plan's PR 3A section, deliberately not closed by
      the migration that added this parameter).
    * *source_frontend_compile* — the context whose ``gcc_path``/
      ``gcc_prefix`` selects the L4 replay compiler, when it is not
      ``evidence.compile``. ``scan`` passes the *folded* (post-P0.3) context,
      i.e. the one its L2 header AST was actually pointed at, which is what
      this function's own ``clang_bin`` comment below says the intent is; the
      two differ only when the caller set neither selector and the matched
      compile unit named an MSVC/clang-cl driver.
    * *expand_public_header_roots* — expand a public-header *directory* into
      its individual files before handing the list to ``embed_build_source``.
      ``scan`` does; the raw pass-through this function otherwise uses loses
      ``clang_public_roots._equivalent_public_roots_for_unit``'s
      single-sample mirror promotion for a directory root (a change switching
      ``scan`` to the raw shape was landed and reverted for exactly that
      regression — see the plan's 2026-08-20 note).
    * *l4_public_headers*/*l4_public_header_dirs* — an override root set for
      *this call's* ``embed_build_source`` invocation only, when the caller's
      L2-facing *public_headers*/*public_header_dirs* need to stay narrower
      than what L4 replay should classify against. ``scan`` needs exactly
      this split: its L2/crosscheck-origin provenance
      (``cli_scan_baseline._public_provenance_set``) deliberately does not
      activate for a lone ``-H`` *file* with no accompanying directory (a
      single header cannot establish a public directory boundary — see that
      function's own docstring, and its pinned
      ``test_lone_file_does_not_activate``), so changing that default to fix
      L4 would also silently flip the origin/crosscheck-skip behavior every
      other scan already relies on. But ``dump``'s write-time embed and
      ``compare``'s implicit-dump operand both derive their public-header
      roots via the more permissive ``split_public_header_inputs`` (every
      ``-H`` file/dir is a root, no directory required) — so a `dump`
      baseline for a lone-``-H``-file project correctly links its L4
      declarations to binary symbols while a `scan --against` candidate for
      the identical project silently degrades to zero matches, producing a
      spurious ``source_decl_binary_symbol_mismatch``/
      ``source_to_binary_mapping_changed`` RISK finding on an unchanged
      library purely from this L2-vs-L4 root-set asymmetry (reproduced
      end-to-end; PR 3A review). Defaults to ``None`` (use
      *public_headers*/*public_header_dirs* unchanged, exactly as before this
      parameter existed) for every pre-existing caller.
    """
    import abicheck.service_compare_evidence as _sce

    from ...buildsource.embed import embed_build_source
    from ...dry_run_estimate import expand_public_header_inputs
    from ...dumper_clang import resolve_source_frontend_clang_bin
    from ...extract.dump_manifest_roots import dump_manifest_public_roots

    ctx = evidence.compile
    frontend_ctx = (
        source_frontend_compile if source_frontend_compile is not None else ctx
    )
    manifest_roots = dump_manifest_public_roots(evidence.dump_manifest)
    embed_headers = (
        l4_public_headers if l4_public_headers is not None else public_headers
    )
    embed_header_dirs = (
        l4_public_header_dirs
        if l4_public_header_dirs is not None
        else public_header_dirs
    )
    if expand_public_header_roots:
        embedded_public_headers: tuple[str, ...] = tuple(
            expand_public_header_inputs(
                [*embed_headers, *embed_header_dirs, *manifest_roots]
            )
        )
    else:
        embedded_public_headers = tuple(str(p) for p in embed_headers)
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
            extractor=(
                source_extractor
                if source_extractor is not None
                else _sce.effective_frontend(evidence.compile, header_backend)
            ),
            # L4 source-ABI replay must invoke the compiler this input's own L2
            # header AST was pointed at (`gcc_path`/`gcc_prefix`), not
            # `embed_build_source`'s bare "clang" default -- the same fix
            # `scan_engine` and the `dump` CLI already carry. Without it a
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
            public_headers=embedded_public_headers,
            public_header_dirs=tuple(str(p) for p in embed_header_dirs)
            + tuple(str(p) for p in manifest_roots),
            defer_cleanup=defer_cleanup,
        )
    except ValidationError as exc:
        # The engine keeps usage and operational errors distinct (the CLI needs
        # 64 vs 1); this surface has always flattened both onto SnapshotError,
        # so preserve that. SnapshotError itself propagates unchanged.
        raise SnapshotError(str(exc)) from exc
