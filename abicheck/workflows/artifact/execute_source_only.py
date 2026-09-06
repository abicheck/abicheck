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

"""Execute a binary-less (``InputSpec.path is None``) ``ResolvedDumpRequest``.

ADR-063 Track T4 ("Dump request contract"), item 1's second still-open
clause: :func:`abicheck.service_dump_pipeline.execute_dump_request` used to
raise unconditionally for this shape even though
:func:`abicheck.service_dump_pipeline.resolve_dump_request` has always fully
resolved it (that resolution is what ``dump --dry-run`` needs). The only
real ``dump --sources``/``--build-info`` (no ``SO_PATH``) pipeline was,
until now, :func:`~abicheck.cli_buildsource.dump_source_only`'s own -- a
CLI-only function collecting L3-L5 evidence inline into an otherwise-empty
snapshot with no typed request or ``resolve_input`` call at all. This
module is that same pipeline, reduced to its engine primitives so a non-CLI
(typed API) caller can reach it too.

Split into its own module (rather than living in
:mod:`abicheck.service_dump_pipeline` alongside
:func:`~abicheck.service_dump_pipeline.execute_dump_request`) purely to keep
that module under the architecture gate's 800-line new-file cap -- it has
no debt-ledger baseline of its own and was already at the cap. It lives
under ``workflows/artifact/`` (a real ADR-061 responsibility-package
directory, not a new flat ``abicheck/service_*.py``/``dumper_*.py``
sibling: ``architecture/modules.yaml``'s ``frozen_root_families`` closes
both of those flat namespaces to new members, per ADR-061's own migration
direction and the precedent recorded in
``docs/contribute/adr/061-responsibility-package-architecture.md``'s
"`abicheck/service_render_compat.py` ... was itself rejected" note) --
``service_dump_pipeline.py`` is itself classified into the ``workflows``
layer (``architecture/modules.yaml``'s ``layers.workflows.legacy_paths``),
so this is a same-layer sibling, not a new cross-layer dependency.

:func:`execute_source_only_dump_request` returns a plain
:class:`SourceOnlyDumpOutcome` rather than a real
:class:`~abicheck.service_dump_pipeline.DumpResult` -- deliberately, not
merely to avoid one import. ``service_dump_pipeline.py`` imports *this*
module (to call the function), and the AI-readiness ``import-cycle-growth``
gate's cycle scan walks every import anywhere in a file, including inside a
function body, so importing ``DumpResult`` back here even lazily would
still register as a new two-module cycle. Returning the three facts
``execute_dump_request`` needs (unpacked in that function's own source-only
branch, which already has ``DumpResult`` in scope to construct) avoids the
cycle entirely rather than papering over it with a deferred import that the
gate would catch anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...errors import ValidationError
from ..extraction import embed_build_source, resolve_source_frontend_clang_bin
from .dump_execution_options import DumpExecutionOptions, _DumpAssuranceView
from .dump_request import ResolvedDumpRequest
from .execute import enforce_requested_depth

if TYPE_CHECKING:
    from ...model import AbiSnapshot
    from ...workflows.resolved_execution_context import ResolvedExecutionContext

__all__ = ["SourceOnlyDumpOutcome", "execute_source_only_dump_request"]


@dataclass(frozen=True)
class SourceOnlyDumpOutcome:
    """The three facts :func:`~abicheck.service_dump_pipeline.execute_dump_request`
    needs to build its own :class:`~abicheck.service_dump_pipeline.DumpResult`
    from -- see this module's own docstring for why this function returns
    this instead of a real ``DumpResult``."""

    snapshot: AbiSnapshot
    effective_depth: str
    resolved_execution_context: ResolvedExecutionContext | None


def execute_source_only_dump_request(
    resolved: ResolvedDumpRequest, options: DumpExecutionOptions
) -> SourceOnlyDumpOutcome:
    """Execute a binary-less :class:`~abicheck.service_dump_pipeline.ResolvedDumpRequest`.

    Build an empty-library :class:`~abicheck.model.AbiSnapshot` (no L0-L2
    pass at all, named after whichever of ``sources``/``build_info`` is
    given, matching ``dump_source_only``'s own naming rule); embed the
    requested L3-L5 evidence via
    :func:`~abicheck.workflows.extraction.embed_build_source` (the engine
    primitive, not :func:`~abicheck.cli_buildsource.embed_build_source`'s
    Click-error-translating wrapper -- both raise the identical
    :class:`~abicheck.errors.ValidationError`/
    :class:`~abicheck.errors.SnapshotError` pair, so nothing here weakens
    ``execute_dump_request``'s own documented contract); then enforce the
    requested-depth floor and derive ``effective_depth`` exactly like the
    binary path does. ``gcc_path``/``gcc_prefix`` come from ``side.compile``
    (the typed equivalent of ``dump_source_only``'s own ``--compiler``/
    ``--compiler-prefix``) -- there is no L2 header AST here to have
    resolved a ``CompileContext`` from, but the field still carries a
    caller's explicit L4 cross-toolchain override.

    **No idempotence guard needed here** (unlike ``cli_buildsource.
    _write_snapshot_output``'s own ``build_source_already_satisfies``
    check): that guard stops a *second* embed replaying L4 source-ABI
    replay over a snapshot a prior embed already populated (the CLI's own
    write step, or ``execute_dump_request``'s binary path embedding at
    *resolve* time). This function's ``snap`` is freshly constructed and
    gets exactly one ``embed_build_source`` call -- nothing earlier to be
    idempotent against. A caller that passes the resulting snapshot through
    ``_write_snapshot_output`` a second time is exactly the case that guard
    protects, unaffected by anything here.

    Deliberately narrower than the general path-less shape
    ``DumpRequest.validate()`` accepts: a *manifest-only* (``dump_manifest``
    set, ``sources``/``build_info`` both absent) path-less request
    validates (a manifest counts as declared evidence too), but executing
    one would need a full header-AST manifest dump with no binary to derive
    an exported-symbol set from -- a materially different, unimplemented
    pipeline. That shape reaches the same "no sources, no build_info" guard
    below and gets the identical ``ValidationError``, preserving
    ``dump_source_only``'s own long-standing contract rather than silently
    embedding nothing.
    """
    from ...evidence_depth import depth_rank, gated_source_label
    from ...model import AbiSnapshot

    side = resolved.request.input
    if side.sources is None and side.build_info is None:
        raise ValidationError(
            "a binary-less DumpRequest (InputSpec.path is None) needs "
            "sources and/or build_info to have anything to extract -- "
            "supply InputSpec.path for an ordinary artifact dump, or "
            "sources/build_info for a source-only snapshot."
        )

    hint = side.sources if side.sources is not None else side.build_info
    library = hint.name if hint is not None else "source"
    snap = AbiSnapshot(library=library, version=side.version)

    compile_ctx = side.compile
    clang_bin = resolve_source_frontend_clang_bin(
        compile_ctx.gcc_path if compile_ctx is not None else None,
        compile_ctx.gcc_prefix if compile_ctx is not None else None,
        exclude_cl_style=False,
    )
    embed_build_source(
        snap,
        side.build_info,
        side.sources,
        build_config=options.build_config,
        clang_bin=clang_bin,
        collect_mode=resolved.collect_mode,
        build_query=options.build_query,
        build_compile_db=options.build_compile_db,
        build_targets=side.build_targets,
        changed_paths=options.changed_paths,
        extractor=resolved.header_backend,
    )

    enforce_requested_depth(resolved.requested_depth, (("input", snap),))
    try:
        # Defensive fallback, mirrors execute_dump_request's own.
        effective_depth = gated_source_label(snap.build_source, snap)
    except (TypeError, ValueError, OverflowError):
        effective_depth = "headers" if snap.from_headers else "binary"

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
        # No `side_effective_compile_context` call (unlike the binary path):
        # there is no `side.path`/header-AST parse to record a compile
        # context against, so `compile_contexts` stays empty.
    return SourceOnlyDumpOutcome(
        snapshot=snap,
        effective_depth=effective_depth,
        resolved_execution_context=resolved_execution_context,
    )
