# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The shared owner of the default header-only (L2) graph attach.

The CLI's native dump (``native._finish_native_snapshot``) and the typed
``dumper.dump()`` (through ``snapshot_factory.finish_binary_dump``) both attach
the header graph here, so the same inputs give the same graph-derived findings
(``var_visibility_changed`` and the vanished-specialization kinds) through either
surface. A no-op when *enabled* is false, the graph switches are off, or no
headers were parsed.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

from ...service_header_graph_attach import (
    _HEADER_GRAPH_ENABLED,
    _HEADER_GRAPH_INCLUDES_ENABLED,
    _attach_header_graph,
)

if TYPE_CHECKING:
    from concurrent.futures import Future

    from ...compile_context import CompileContext
    from ...dump_manifest import DumpManifest
    from ...model import AbiSnapshot
    from ...service_header_graph_attach import HeaderGraphAst

__all__ = [
    "attach_default_header_graph",
    "attach_typed_header_graph",
    "graph_public_scope",
    "header_graph_suppressed",
]

_ATTACH_SUPPRESSED: ContextVar[bool] = ContextVar(
    "header_graph_attach_suppressed", default=False
)


@contextmanager
def header_graph_suppressed() -> Iterator[None]:
    """Skip the default header-graph attach for dumps started in this scope.

    Used by a caller that attaches the graph itself, once, to a merged snapshot
    (a hybrid's legs) or by the native tail that owns the attach.
    """
    token = _ATTACH_SUPPRESSED.set(True)
    try:
        yield
    finally:
        _ATTACH_SUPPRESSED.reset(token)


def effective_public_scope(
    dump_manifest: DumpManifest | None,
    public_headers: list[Path] | None,
    public_header_dirs: list[Path] | None,
) -> tuple[list[Path] | None, list[Path] | None]:
    """The public header files and directories a dump records for its scope.

    A dump manifest's own public paths and directories replace the CLI-flag
    derived ones (their mutual exclusivity is validated by the caller). With no
    manifest the caller's own values come back unchanged.
    """
    if dump_manifest is not None:
        return (
            list(dump_manifest.public_header_paths),
            list(dump_manifest.public_header_dirs),
        )
    return public_headers, public_header_dirs


def graph_public_scope(
    headers: Sequence[Path],
    public_headers: Sequence[Path] | None,
    public_header_dirs: Sequence[Path] | None,
) -> tuple[list[Path], list[Path] | None]:
    """The graph's public files and directories for a typed dump.

    Explicit public files or directories define the boundary and are used as
    given. Otherwise the -H operands are the boundary: directory operands are
    public directories (contained headers are public) and file operands are
    public files, as the CLI routes them.
    """
    if public_headers or public_header_dirs:
        files = list(public_headers or [])
        dirs = list(public_header_dirs) if public_header_dirs else None
        return files, dirs
    return (
        [h for h in headers if not h.is_dir()],
        [h for h in headers if h.is_dir()],
    )


def compile_context_from_options(
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: Sequence[str] = (),
    sysroot: Path | None = None,
    nostdinc: bool = False,
) -> CompileContext | None:
    """The compile context a typed dump's header parse runs with, or ``None``.

    The graph parse must see the same ``-D``/``-I``/sysroot/nostdinc options as
    the primary header parse, or it can describe a different preprocessor
    configuration than the snapshot it is attached to. With no options given,
    ``None`` keeps the graph parse on its default context, as before.
    """
    from ...compile_context import CompileContext

    if not (
        gcc_path
        or gcc_prefix
        or gcc_options
        or gcc_option_tokens
        or sysroot
        or nostdinc
    ):
        return None
    return CompileContext(
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=tuple(gcc_option_tokens),
        sysroot=sysroot,
        nostdinc=nostdinc,
    )


def attach_typed_header_graph(
    snap: AbiSnapshot,
    *,
    headers: Sequence[Path],
    includes: Sequence[Path],
    lang: str | None,
    public_headers: Sequence[Path] | None,
    public_header_dirs: Sequence[Path] | None,
    include_search_dirs: Sequence[Path] | None,
    enabled: bool,
    compile: CompileContext | None = None,
) -> AbiSnapshot:
    """The typed route's graph attach: the -H boundary, then the shared owner."""
    files, dirs = graph_public_scope(headers, public_headers, public_header_dirs)
    return attach_default_header_graph(
        snap,
        headers=list(headers),
        includes=list(includes),
        lang=lang,
        compile=compile,
        public_headers=files,
        public_header_dirs=dirs,
        include_search_dirs=(
            list(include_search_dirs) if include_search_dirs is not None else None
        ),
        enabled=enabled,
    )


def attach_default_header_graph(
    snap: AbiSnapshot,
    *,
    headers: list[Path],
    includes: list[Path],
    lang: str | None,
    compile: CompileContext | None,
    public_headers: list[Path] | None,
    public_header_dirs: list[Path] | None,
    include_search_dirs: list[Path] | None = None,
    enabled: bool = True,
    prefetched: Future[HeaderGraphAst] | None = None,
) -> AbiSnapshot:
    """Attach the default header-only graph to *snap* (see the module docstring)."""
    if _ATTACH_SUPPRESSED.get():
        return snap
    return _attach_header_graph(
        snap,
        _HEADER_GRAPH_ENABLED and enabled,
        _HEADER_GRAPH_INCLUDES_ENABLED and enabled,
        headers,
        includes,
        lang,
        compile,
        public_headers,
        public_header_dirs,
        include_search_dirs=include_search_dirs,
        prefetched=prefetched,
    )
