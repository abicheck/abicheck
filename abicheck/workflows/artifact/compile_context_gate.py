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

"""One Semantic Pipeline plan, sub-phase 4B: whether a side's resolved
``CompileContext`` is safe to record onto ``ResolvedExecutionContext.
compile_contexts``.

Split out of :mod:`abicheck.workflows.artifact.execute` (Codex review, PR
#1047, fresh evidence) rather than grown further there: both
``execute.py`` and ``service_compare_pipeline.py`` were already at or past
the 800-line production cap, and this gate is a genuinely separable,
dependency-light concern -- the one place that decides whether a resolved
``CompileContext`` is safe to record, and the two-sided helper built on top
of it for a comparison's pair. Moving responsibility here (rather than
adding a debt-ledger exception to either already-tight file) is the fix
the architecture gate's own error message asks for.

Deliberately takes a bare ``CompileContext | None`` rather than a whole
``SideResolution`` (Codex review, second round): this gate only ever reads
``SideResolution.effective_compile_context``, and importing the type for
that alone would pull this new leaf module into the large, already-
allowlisted ``workflows.artifact.execute -> service -> ... ->
service_dump_pipeline`` cycle as a genuinely *new* member -- exactly what
the architecture gate's ``import-cycle-growth`` check exists to catch, and
extending that allowlist needs an ADR, not a routine edit (root
``AGENTS.md``). Depending on the primitive value instead of the whole
object is the real fix: it is also the looser, more honest coupling for
what this function actually needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ...compile_context import CompileContext
    from ...model import AbiSnapshot

__all__ = [
    "SideCompileInput",
    "resolved_pair_compile_contexts",
    "side_effective_compile_context",
]


def side_effective_compile_context(
    effective_compile_context: CompileContext | None,
    snapshot: AbiSnapshot,
    path: Path,
    *,
    dump_manifest: object | None,
) -> CompileContext | None:
    """*effective_compile_context* (a side's own
    ``SideResolution.effective_compile_context``) if it is safe to record
    onto ``ResolvedExecutionContext.compile_contexts``, else `None`.

    Shared by ``service_dump_pipeline.execute_dump_request`` and
    ``service_compare_pipeline.resolve_compare_request`` (Codex review,
    PR #1037; lifted out of the dump path's own inline conditional once the
    compare path needed the identical gate). Only when a header-AST parse
    actually ran *this invocation* (``snapshot.from_headers``), the binary
    format was successfully detected, and the side isn't a manifest-driven
    dump -- whose own real header-AST parse runs under its own
    manifest-authoritative ``frontend_context`` (e.g. ``"device"``), not the
    request-derived context this fold resolved, so recording it here would
    risk stating a wrong (``"host"``) toolchain.

    *path* is the side's own input, detected here -- following a GNU ld
    linker script to its real target first, mirroring ``resolve_input``'s
    own dispatch order -- rather than accepting an already-detected format
    from the caller (Codex review, PR #1047, fresh evidence): a linker
    script's own bytes are text, so a raw, un-followed format detection on
    it reads `None` even when the resolved target -- what actually drove
    the header-AST parse -- is a real binary, which silently dropped a real
    compile context for exactly this input shape when the compare path
    first passed its own un-followed `old_fmt`/`new_fmt` here.
    """
    if (
        effective_compile_context is None
        or not snapshot.from_headers
        or dump_manifest is not None
    ):
        return None
    # `workflows/AGENTS.md` forbids importing through the `abicheck.service`
    # compatibility facade -- these are its canonical `workflows.
    # input_resolution` implementations, not a re-derivation.
    from ..input_resolution import detect_binary_format, sniff_text_format

    fmt = detect_binary_format(path)
    if fmt is None and sniff_text_format(path) not in ("json", "perl"):
        from ...binary_utils import resolve_linker_script_chain

        fmt = detect_binary_format(resolve_linker_script_chain(path))
    return effective_compile_context if fmt is not None else None


@dataclass(frozen=True)
class SideCompileInput:
    """One side's inputs to :func:`resolved_pair_compile_contexts` --
    bundled so the call site (already juggling both sides' snapshots,
    resolutions, and requests) doesn't have to spell six-plus positional/
    keyword arguments per side."""

    effective_compile_context: CompileContext | None
    snapshot: AbiSnapshot
    path: Path
    dump_manifest: object | None = None


def resolved_pair_compile_contexts(
    old: SideCompileInput, new: SideCompileInput
) -> dict[str, CompileContext]:
    """Both sides' :func:`side_effective_compile_context` results, keyed
    ``"old"``/``"new"`` -- the shape
    :attr:`~abicheck.workflows.resolved_execution_context.
    ResolvedExecutionContext.compile_contexts` wants, with a side simply
    absent (not placeholder-valued) when the gate excludes it."""
    contexts: dict[str, CompileContext] = {}
    old_ctx = side_effective_compile_context(
        old.effective_compile_context,
        old.snapshot,
        old.path,
        dump_manifest=old.dump_manifest,
    )
    if old_ctx is not None:
        contexts["old"] = old_ctx
    new_ctx = side_effective_compile_context(
        new.effective_compile_context,
        new.snapshot,
        new.path,
        dump_manifest=new.dump_manifest,
    )
    if new_ctx is not None:
        contexts["new"] = new_ctx
    return contexts
