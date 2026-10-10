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

"""The ``hybrid`` header backend for a native dump, as a composition.

Two single-backend extractions (castxml, then clang) of the same request,
merged by :func:`run_hybrid_dump`. Each leg is one
call of the caller's single-backend *extract-and-finish* function -- the
format adapter plus its post-extraction tail -- never a re-entry into
``run_dump``.

A leg keeps the exact contract the recursive ``run_dump(...,
include_dependencies=True)`` call it replaces gave it (lane B, stage B1c):
the parse may skip nothing (:func:`~abicheck.workflows.run_dump_scope.
extraction_scope` with ``True``), the result records a full dependency
scope, and the live-source licence is granted per leg. The outer
``run_dump`` scopes the merged snapshot once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...buildsource.source_inputs import granting_live_source_licence
from ...compile_context import CompileContext
from ...extract.dependency_exclusion import suppress_dependency_exclusion
from ...model import AbiSnapshot
from ...storage import closure_identity
from ...storage.header_ast_cache import ast_memoize_scope
from ..run_dump_scope import extraction_scope
from ..snapshot_factory import DependencyScopeInputs, SnapshotFinish, finish_snapshot
from .formats import NativeExtractRequest
from .hybrid_merge import merge_snapshots

ExtractAndFinish = Callable[[NativeExtractRequest], AbiSnapshot]


def run_hybrid_dump(
    dump_fn: Callable[..., AbiSnapshot],
    so_path: Path,
    headers: list[Path],
    **kwargs: Any,
) -> AbiSnapshot:
    """Run *dump_fn* (``dumper.dump``) once per real backend and merge.

    Takes *dump_fn* as a parameter so this module never depends on
    ``dumper.py``, which depends on it. Every keyword argument is forwarded
    to both sub-dumps unchanged except ``header_backend``, which this
    function sets explicitly on each call; only :func:`merge_snapshots` is
    new. The parse-time dependency skip is off: only clang could apply it.
    """
    with (
        closure_identity.defer_closure_identity_renumbering(),
        suppress_dependency_exclusion(),
    ):
        castxml_snap = dump_fn(so_path, headers, header_backend="castxml", **kwargs)
        clang_snap = dump_fn(so_path, headers, header_backend="clang", **kwargs)
    return closure_identity.renumber_anonymous_closure_identities(
        merge_snapshots(castxml_snap, clang_snap)
    )


def _forced_backend(
    request: NativeExtractRequest, backend: str
) -> NativeExtractRequest:
    """*request* pinned to *backend*: the effective backend and an explicit
    ``compile.frontend`` (which outranks the bare argument), so the leg can
    never resolve back to ``hybrid``."""
    compile_ctx = (
        replace(request.compile, frontend=backend)
        if request.compile is not None
        else CompileContext(frontend=backend)
    )
    return replace(request, header_backend=backend, compile=compile_ctx)


def _full_scope_leg(
    extract_and_finish: ExtractAndFinish, header_roots: tuple[Any, ...]
) -> ExtractAndFinish:
    """One leg with ``run_dump(..., include_dependencies=True)``'s contract."""

    def leg(request: NativeExtractRequest) -> AbiSnapshot:
        with extraction_scope(True, header_roots):
            snap = extract_and_finish(request)
        return finish_snapshot(
            snap,
            SnapshotFinish(dependency_scope=DependencyScopeInputs(True, header_roots)),
        )

    return granting_live_source_licence(leg)


def compose_hybrid(
    extract_and_finish: ExtractAndFinish,
    request: NativeExtractRequest,
    *,
    header_roots: tuple[Any, ...],
) -> AbiSnapshot:
    """Merge a castxml leg and a clang leg of *request*.

    *extract_and_finish* runs one single-backend dump of a request (format
    adapter plus tail) and should skip the header-only graph: the caller
    attaches it once, to the merged declarations. *header_roots* are the
    dump's own scoping roots (headers, manifest roots, public headers and
    directories).
    """
    leg = _full_scope_leg(extract_and_finish, header_roots)

    def _leg_for_backend(
        _so_path: Path, _headers: list[Path], *, header_backend: str
    ) -> AbiSnapshot:
        return leg(_forced_backend(request, header_backend))

    # AST memoization (G31 Phase C): the caller's header-graph attach after
    # the merge is a real downstream consumer of both legs' parses.
    with ast_memoize_scope():
        return run_hybrid_dump(_leg_for_backend, request.path, request.headers)
