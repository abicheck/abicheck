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

"""``service.run_dump``'s dependency-scoping wrapper.

Moved from ``dumper_scoping`` (``extract``) in design-hardening Phase 2 so
the scoping pass runs through
:func:`~abicheck.workflows.snapshot_factory.finish_snapshot`, the one place
the finishing passes are applied. The scoping *implementation*
(``dumper_scoping.resolve_dependency_scope``) stays in ``extract``.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..extract.dependency_exclusion import (
    dependency_exclusion_scope,
    suppress_dependency_exclusion,
)
from ..extract.dump_manifest_roots import dump_manifest_header_roots
from ..extract.headers.clang.streaming import suppress_streaming_prune
from ..model import AbiSnapshot
from .snapshot_factory import DependencyScopeInputs, SnapshotFinish, finish_snapshot

__all__ = [
    "apply_dependency_scope_to_run_dump_result",
    "extraction_scope",
    "run_dump_header_roots",
    "wrap_run_dump_with_dependency_scope",
]


@contextmanager
def extraction_scope(
    include_dependencies: bool, header_roots: Sequence[Path | str]
) -> Iterator[None]:
    """Decide, once, what a dump's extraction may skip given its scope.

    The ``dependency_scope`` a dump records and the parse that produced its
    declarations must agree. Two mechanisms can drop dependency declarations
    during the parse (the opt-in streaming pruner and the parse-time
    dependency skip); both are driven from here so neither can disagree with
    the scope the result is stamped with.

    ``include_dependencies=True`` (a full surface): nothing may be skipped,
    even when an enclosing dump declared a scope -- a ``hybrid`` leg, or any
    nested ``run_dump``. Without this, the enclosing scope's skip predicate
    reached the inner parse, and a filtered surface was stamped ``"full"``.
    ``False``: the parse may skip exactly what scoping with *header_roots*
    drops afterwards.
    """
    if include_dependencies:
        with suppress_streaming_prune(), suppress_dependency_exclusion():
            yield
    else:
        with dependency_exclusion_scope(header_roots):
            yield


def apply_dependency_scope_to_run_dump_result(
    snap: AbiSnapshot,
    include_dependencies: bool,
    bound_args: inspect.BoundArguments,
) -> AbiSnapshot:
    """``service.run_dump``'s own choke point: *include_dependencies*
    defaults to ``True`` there (preserving every existing caller — scan,
    ``dump``'s own inline calls — that doesn't pass it explicitly);
    only ``compare`` opts into ``False`` to filter its live-binary dumping
    the same way ``dump`` filters by default. *bound_args* is
    ``inspect.Signature.bind_partial(*args, **kwargs)`` against the real
    dumping function's signature — used to recover the caller's ``headers``
    regardless of whether it was passed positionally or by keyword, the
    same ``-H``/``--header`` root set :func:`resolve_dependency_scope` needs
    to avoid misclassifying an installed library's own system-prefixed path
    as a dependency. ``--dump-manifest`` is mutually exclusive with ``-H``,
    so ``headers`` alone is empty for a manifest-driven dump — its own
    project-owned roots (:func:`dump_manifest_header_roots`) are folded in
    too, the same way ``cli_dump_helpers.py``'s ``dump`` path already does,
    else a manifest project header installed under a system-like prefix
    would be misclassified as a dependency (Codex review). ``public_headers``/
    ``public_header_dirs`` (ADR-024 Phase 1 / ADR-055 D1's ``InputSpec.
    public_header_dirs``) are folded in too -- an explicitly-declared public
    file or directory rooted under a system-like prefix (e.g. an installed
    library's own ``/usr/include/mylib/api.h``, reached transitively rather
    than listed in ``headers``) must not be misclassified as a dependency
    either (Codex review, twice: the first pass only folded in
    ``public_header_dirs``, missing the file-level ``public_headers`` set)."""
    return finish_snapshot(
        snap,
        SnapshotFinish(
            dependency_scope=DependencyScopeInputs(
                include_dependencies, _run_dump_header_roots(bound_args)
            )
        ),
    )


def _run_dump_header_roots(bound_args: inspect.BoundArguments) -> tuple[Any, ...]:
    """Scoping roots for one ``run_dump`` call (see the function above)."""
    a = bound_args.arguments
    return run_dump_header_roots(
        a.get("headers"),
        a.get("dump_manifest"),
        a.get("public_headers"),
        a.get("public_header_dirs"),
    )


def run_dump_header_roots(
    headers: Sequence[Any] | None,
    dump_manifest: Any,
    public_headers: Sequence[Any] | None,
    public_header_dirs: Sequence[Any] | None,
) -> tuple[Any, ...]:
    """A dump's scoping roots: its headers, its manifest's project-owned
    roots, and its declared public headers and header directories."""
    return (
        tuple(headers or ())
        + dump_manifest_header_roots(dump_manifest)
        + tuple(public_headers or ())
        + tuple(public_header_dirs or ())
    )


def wrap_run_dump_with_dependency_scope(
    uncached_fn: Callable[..., AbiSnapshot],
) -> Callable[..., AbiSnapshot]:
    """Build ``service.run_dump`` from ``service._run_dump_uncached``: a
    thin wrapper adding an *include_dependencies* keyword (default ``False``,
    matching the CLI flag and ``InputSpec.include_dependencies``) and applying
    :func:`apply_dependency_scope_to_run_dump_result` to the result — see that
    function's own docstring.

    ``functools.wraps`` copies ``__wrapped__`` from *uncached_fn*, which
    ``inspect.signature`` follows by default — silently hiding the new
    ``include_dependencies`` keyword from anything that introspects the
    wrapper's signature (the generated Python API reference, or a
    signature-driven caller/validation framework), even though it's a real,
    accepted parameter (Codex review). ``__signature__`` is set explicitly
    below to the real, extended signature so introspection sees it.
    """
    sig = inspect.signature(uncached_fn)
    new_param = inspect.Parameter(
        "include_dependencies",
        kind=inspect.Parameter.KEYWORD_ONLY,
        default=False,  # must track the wrapper's own default below
        # A bare string, matching every other parameter's annotation here:
        # this module (like the rest of the codebase) has `from __future__
        # import annotations`, so `inspect.signature` on a real function
        # already returns string annotations, not type objects.
        annotation="bool",
    )
    old_params = list(sig.parameters.values())
    # A KEYWORD_ONLY parameter must sort before any VAR_KEYWORD (**kwargs) one
    # -- insert just ahead of it rather than always appending, so this stays
    # valid even against a caller signature that ends in **kwargs (as a test
    # double's does; the real `_run_dump_uncached` has none).
    insert_at = next(
        (
            i
            for i, p in enumerate(old_params)
            if p.kind is inspect.Parameter.VAR_KEYWORD
        ),
        len(old_params),
    )
    extended_sig = sig.replace(
        parameters=[*old_params[:insert_at], new_param, *old_params[insert_at:]]
    )

    @functools.wraps(uncached_fn)
    def run_dump(
        *args: object, include_dependencies: bool = False, **kwargs: object
    ) -> AbiSnapshot:
        # Default matches the CLI flag and `InputSpec`; see that field's note.
        # The parse-time skip rules come from `extraction_scope`, the one
        # place that maps this request's scope onto what the parse may drop.
        bound = sig.bind_partial(*args, **kwargs)
        with extraction_scope(include_dependencies, _run_dump_header_roots(bound)):
            snap = uncached_fn(*args, **kwargs)
        return apply_dependency_scope_to_run_dump_result(
            snap, include_dependencies, bound
        )

    run_dump.__signature__ = extended_sig  # type: ignore[attr-defined]
    return run_dump
