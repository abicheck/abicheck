# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Execute a binary-less (``InputSpec.path is None``) ``ResolvedDumpRequest``
whose evidence is public headers alone (workstream F S1, "Header-only
comparison; bounded static-archive investigation",
``docs/contribute/plans/vision-api-abi-evolution.md``).

Before this module, a ``dump -H api.h`` with no ``SO_PATH`` and no
``--sources``/``--build-info`` had exactly one real execution path:
:func:`~abicheck.cli_buildsource.dump_source_only`, which never reads
``headers`` at all and writes an empty (0 function/type) snapshot -- the
CLI's own real-run branch (``frontends/cli/commands/dump.py``) warns "``-H``
has no effect on this source-only dump" and proceeds anyway. This module is
the real header-AST execution that shape has always been missing: it routes
a bare header list (or a ``--dump-manifest``) through
:func:`abicheck.header_only_dump.build_header_only_snapshot` -- the exact
same L2 parse-and-normalize machinery a binary dump's header pass already
uses, just with no ELF/PE/Mach-O metadata at all and an empty
observed-export set on both sides of that call, since there is no binary to
have exported anything from (see that function's own docstring for why it
lives in a flat, unclassified-adjacent module rather than here). The
resulting :class:`~abicheck.model.AbiSnapshot` carries real functions,
variables, types, enums, typedefs, and constants parsed straight from the
headers, with ``header_only=True`` (schema v44) marking the tier explicitly
so a report/policy consumer never mistakes it for the pre-existing L3-L5
source-only shape (:mod:`abicheck.workflows.artifact.execute_source_only`),
which carries no header-AST declarations at all.

**What this snapshot cannot carry, structurally** (see
``abicheck.policy.header_only_capabilities`` for the report-facing ledger):
a header-AST parse alone never observes a real ELF ``.dynsym``/PE export
directory/Mach-O export trie, so every mangled name here is the header
frontend's own *guessed* Itanium/MSVC spelling, not a linker-confirmed
export; there is no binary layout to derive struct/class field offsets,
padding, or vtable/RTTI shape from; and there is no symbol-versioning
(``.gnu.version``) evidence at all. Two header-only snapshots compare their
*declared* surface only -- exactly ADR-063/ADR-049's "weaker evidence
narrows conclusions, never fabricates" principle, made concrete for this one
new operand shape.

Deliberately excludes layering L3-L5 build/source evidence on top (a
``sources``/``build_info``-bearing side takes the pre-existing
:func:`~abicheck.workflows.artifact.execute_source_only.
execute_source_only_dump_request` path unchanged, exactly as it did before
this module existed) -- :func:`is_header_only_evidence` is the one place
that dispatch decision is made, so the two paths cannot silently overlap or
disagree about which shape a given request is.

Split into its own module rather than added to ``execute_source_only.py``,
mirroring that module's own stated reason for *its* existence (see its
docstring): each stays a small, single-shape leaf, and
``service_dump_pipeline.execute_dump_request`` already imports one sibling
this way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...errors import ValidationError
from .dump_execution_options import DumpExecutionOptions, _DumpAssuranceView
from .dump_request import ResolvedDumpRequest
from .execute import enforce_requested_depth

if TYPE_CHECKING:
    from ...model import AbiSnapshot
    from ...workflows.resolved_execution_context import ResolvedExecutionContext

__all__ = [
    "HeaderOnlyDumpOutcome",
    "execute_header_only_dump_request",
    "is_header_only_evidence",
]


def is_header_only_evidence(resolved: ResolvedDumpRequest) -> bool:
    """True when *resolved* is the binary-less, headers-only shape this
    module executes.

    Only meaningful for a request whose ``InputSpec.path is None`` (the
    caller -- :func:`~abicheck.service_dump_pipeline.execute_dump_request`
    -- already branches on that); this function answers the *second*
    question, which of the two binary-less shapes it is. ``True`` when the
    resolved header set or a ``--dump-manifest`` names real evidence to
    parse *and* neither ``sources`` nor ``build_info`` was also given --
    a side declaring either of those takes the pre-existing L3-L5
    :func:`~abicheck.workflows.artifact.execute_source_only.
    execute_source_only_dump_request` path unchanged, so the two shapes
    never overlap.
    """
    side = resolved.request.input
    if side.sources is not None or side.build_info is not None:
        return False
    return bool(resolved.headers) or resolved.evidence.dump_manifest is not None


@dataclass(frozen=True)
class HeaderOnlyDumpOutcome:
    """The three facts :func:`~abicheck.service_dump_pipeline.
    execute_dump_request` needs to build its own
    :class:`~abicheck.service_dump_pipeline.DumpResult` from -- mirrors
    :class:`~abicheck.workflows.artifact.execute_source_only.
    SourceOnlyDumpOutcome`'s identical shape and identical reason (avoiding
    a real import cycle back into ``service_dump_pipeline``)."""

    snapshot: AbiSnapshot
    effective_depth: str
    resolved_execution_context: ResolvedExecutionContext | None


def execute_header_only_dump_request(
    resolved: ResolvedDumpRequest, options: DumpExecutionOptions
) -> HeaderOnlyDumpOutcome:
    """Execute a binary-less, headers-only :class:`ResolvedDumpRequest`.

    Runs the header-AST parse via :func:`abicheck.header_only_dump.
    build_header_only_snapshot`, which returns an
    :class:`~abicheck.model.AbiSnapshot` with ``platform=None`` (no ELF/PE/
    Mach-O metadata), ``from_headers=True``, and ``header_only=True`` --
    see that function's own docstring for the parse itself.

    *options* is accepted for the identical reason
    :func:`~abicheck.workflows.artifact.execute_source_only.
    execute_source_only_dump_request` takes it -- parity of signature with
    that sibling, even though a headers-only parse needs none of its
    build/source-evidence fields today (``build_config``/``build_query``/
    ``build_compile_db``/``changed_paths`` are all L3 concerns this path
    does not reach).

    Raises:
        ValidationError: If neither a resolved header nor a
            ``dump_manifest`` names anything to parse (should not happen
            for a request :func:`is_header_only_evidence` already accepted,
            but checked directly rather than trusted, since this function
            has its own public contract).
    """
    from ...evidence_depth import depth_rank, gated_source_label
    from ...header_only_dump import build_header_only_snapshot

    side = resolved.request.input
    headers = list(resolved.headers)
    dump_manifest = resolved.evidence.dump_manifest
    if not headers and dump_manifest is None:
        raise ValidationError(
            "a binary-less, header-only DumpRequest needs at least one "
            "-H/--header or a --dump-manifest to have anything to parse "
            "-- supply InputSpec.path for an ordinary artifact dump, or "
            "sources/build_info for a source-only snapshot."
        )

    snap = build_header_only_snapshot(
        library_hint=headers[0] if headers else None,
        version=side.version,
        headers=headers,
        extra_includes=list(side.includes),
        dump_manifest=dump_manifest,
        backend=resolved.header_backend,
        compile=side.compile,
        lang=resolved.lang,
        lang_explicit=resolved.lang_explicit,
        public_headers=list(resolved.public_headers),
        public_header_dirs=list(resolved.public_header_dirs),
        frontend_context=(
            side.compile.frontend_context if side.compile is not None else "host"
        ),
    )

    enforce_requested_depth(resolved.requested_depth, (("input", snap),))
    try:
        # Defensive fallback, mirrors execute_dump_request's/
        # execute_source_only_dump_request's own identical try/except.
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
        # No side_effective_compile_context call (mirrors
        # execute_source_only_dump_request's own identical comment): there
        # is no side.path/binary-observed compile context to record here.

    return HeaderOnlyDumpOutcome(
        snapshot=snap,
        effective_depth=effective_depth,
        resolved_execution_context=resolved_execution_context,
    )
