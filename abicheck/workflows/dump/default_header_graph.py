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
    from ...model import AbiSnapshot
    from ...service_header_graph_attach import HeaderGraphAst

__all__ = ["attach_default_header_graph"]


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
