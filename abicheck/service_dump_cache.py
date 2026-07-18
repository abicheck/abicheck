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

"""Whole-snapshot cache wiring for ``service.run_dump()``.

Split out of ``service.py`` to keep that file under the AI-readiness
line-count cap. A leaf module: it never imports ``service.py`` (that would
create an import cycle, since ``service.py`` needs to call
:func:`cached_run_dump`) — instead :func:`cached_run_dump` takes ``run_dump``
itself as an explicit callable parameter, dependency-injection style, same
end result as a lazy import without the indirection.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from .model import AbiSnapshot


def _dump_is_cacheable(
    *,
    pdb_path: Path | None,
    dwarf_only: bool,
    debug_roots: list[Path] | None,
    enable_debuginfod: bool,
    debug_format: str | None,
    symbols_only: bool,
    debug_presence_only: bool,
    # Untyped (not `CompileContext | None`): the type lives in service_scan.py,
    # and importing it here — even under TYPE_CHECKING — closes an import
    # cycle back through service.py/scan_engine.py. Only ever compared to
    # `None` below, so no real type-safety is lost.
    compile: object | None,
    header_graph: bool,
    header_graph_includes: bool,
) -> bool:
    """Whether a ``run_dump`` call is safe to serve from the whole-snapshot
    cache (:mod:`abicheck.snapshot_cache`).

    Deliberately conservative: only the plain "binary + public headers" shape
    — the dominant release-baseline/CI comparison case a repeated pipeline
    re-extracts identically on every run — is cached. A PDB path, a DWARF
    debug-info root, debuginfod resolution (network-dependent), a forced
    debug format, a symbols-only/debug-presence-only dump, a custom
    ``CompileContext``, or the header-only semantic graph all change what
    ``run_dump`` produces in ways not folded into the cache key below, so
    those combinations always fall through to a live dump rather than risk
    serving a stale/mismatched snapshot.
    """
    return (
        pdb_path is None
        and not dwarf_only
        and not debug_roots
        and not enable_debuginfod
        and debug_format is None
        and not symbols_only
        and not debug_presence_only
        and compile is None
        and not header_graph
        and not header_graph_includes
    )


def _dump_cache_extra_key(
    binary_fmt: str,
    header_backend: str,
    public_headers: list[Path] | None,
    public_header_dirs: list[Path] | None,
) -> str:
    """Build the ``extra`` cache-key material for a cacheable dump — every
    input to ``run_dump`` that affects its output besides the binary content
    / headers / includes / version / lang that
    :func:`abicheck.snapshot_cache._cache_key` already hashes directly.

    Joined with NUL (``\\x00``) rather than a printable delimiter like
    ``,``/``|``: a path may legally contain a comma or pipe on POSIX, so a
    printable-delimiter join risks two different inputs — e.g. one path
    ``"a,b"`` vs. two paths ``"a"``/``"b"`` — collapsing to the same key. NUL
    can't appear in a filesystem path on any supported platform, so it can't
    collide regardless of what the caller's paths contain.

    Hashes the RESOLVED backend (``dumper._resolve_header_backend``), not the
    raw ``header_backend`` string as passed: an ``"auto"`` request consults
    ``ABICHECK_AST_FRONTEND`` at dump time, so the raw string is the same
    ``"auto"`` regardless of whether that env var is unset (-> castxml),
    pinned to ``hybrid``, or pinned to ``clang``. Hashing the raw string let
    a hybrid-pinned run's snapshot get cached under the identical key an
    unpinned castxml run would also use (and vice versa) — this on-disk
    cache persists across process invocations, so a later run with the env
    var in a different state could silently reuse the wrong producer's
    snapshot instead of ever calling the real dump (Codex review).

    When the resolved backend is ``"clang"`` OR ``"hybrid"``, also hashes the
    resolved G28 Phase 4 layout-tool identity
    (``clang_layout_tool.find_layout_tool_bin()`` — the
    ``ABICHECK_CLANG_LAYOUT_TOOL`` path, or whatever a bare
    ``abicheck-clang-layout-tool`` resolves to on ``PATH``, or ``""`` if
    unavailable). ``service.run_dump`` calls ``attach_clang_layout`` for
    every ``"clang"``-backend dump, so the snapshot's layout fields depend on
    that tool's availability/identity too — omitting it let a snapshot
    cached before enabling/changing the tool get silently reused afterward
    (or vice versa), never re-running the real dump to pick up the change
    (Codex review). A ``"hybrid"`` dump ALSO depends on it: ``run_dump``'s
    hybrid branch recurses into its own ``header_backend="clang"`` sub-dump
    (which gets the same ``attach_clang_layout`` enrichment) before
    ``merge_snapshots`` folds any clang-only declarations — carrying their
    layout facts — into the merged result (Codex review). Skipped only for
    ``"castxml"``, whose snapshots never involve this tool at all.
    """
    from .dumper import _resolve_header_backend

    resolved_backend = _resolve_header_backend(header_backend)
    layout_tool = ""
    if resolved_backend in ("clang", "hybrid"):
        from .clang_layout_tool import find_layout_tool_bin

        layout_tool = find_layout_tool_bin() or ""

    sep = "\x00"
    return sep.join(
        [
            binary_fmt,
            resolved_backend,
            layout_tool,
            sep.join(sorted(str(p) for p in (public_headers or []))),
            sep.join(sorted(str(p) for p in (public_header_dirs or []))),
        ]
    )


def cached_run_dump(
    run_dump: Callable[..., AbiSnapshot],
    path: Path,
    binary_fmt: str,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    version: str = "",
    lang: str = "c++",
    *,
    pdb_path: Path | None = None,
    dwarf_only: bool = False,
    debug_roots: list[Path] | None = None,
    enable_debuginfod: bool = False,
    debuginfod_url: str | None = None,
    debug_format: str | None = None,
    symbols_only: bool = False,
    debug_presence_only: bool = False,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    header_backend: str = "auto",
    compile: object | None = None,
    header_graph: bool = False,
    header_graph_includes: bool = False,
    notify: Callable[[str], None] | None = None,
) -> AbiSnapshot:
    """``run_dump(...)``, transparently served from the whole-snapshot cache
    when the call shape is cacheable (:func:`_dump_is_cacheable`) — avoiding a
    repeat binary/header extraction entirely (no castxml/DWARF/ELF work, no
    model construction) on a hit. Falls through to a live ``run_dump`` call
    on a miss or an uncacheable shape, then populates the cache.

    ``run_dump`` is passed in by the caller (``service.py``) rather than
    imported here, so this module never needs to import ``service`` itself
    (which would be a cycle: ``service.py`` calls this function).
    """
    _headers = headers or []
    _includes = includes or []
    cacheable = _dump_is_cacheable(
        pdb_path=pdb_path,
        dwarf_only=dwarf_only,
        debug_roots=debug_roots,
        enable_debuginfod=enable_debuginfod,
        debug_format=debug_format,
        symbols_only=symbols_only,
        debug_presence_only=debug_presence_only,
        compile=compile,
        header_graph=header_graph,
        header_graph_includes=header_graph_includes,
    )
    if not cacheable:
        return run_dump(
            path,
            binary_fmt,
            headers,
            includes,
            version,
            lang,
            pdb_path=pdb_path,
            dwarf_only=dwarf_only,
            debug_roots=debug_roots,
            enable_debuginfod=enable_debuginfod,
            debuginfod_url=debuginfod_url,
            debug_format=debug_format,
            symbols_only=symbols_only,
            debug_presence_only=debug_presence_only,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            header_backend=header_backend,
            compile=compile,
            header_graph=header_graph,
            header_graph_includes=header_graph_includes,
            notify=notify,
        )

    from . import snapshot_cache

    extra = _dump_cache_extra_key(
        binary_fmt, header_backend, public_headers, public_header_dirs
    )
    cached = snapshot_cache.lookup(path, _headers, _includes, version, lang, extra=extra)
    if cached is not None:
        return cached
    snap = run_dump(
        path,
        binary_fmt,
        headers,
        includes,
        version,
        lang,
        pdb_path=pdb_path,
        dwarf_only=dwarf_only,
        debug_roots=debug_roots,
        enable_debuginfod=enable_debuginfod,
        debuginfod_url=debuginfod_url,
        debug_format=debug_format,
        symbols_only=symbols_only,
        debug_presence_only=debug_presence_only,
        public_headers=public_headers,
        public_header_dirs=public_header_dirs,
        header_backend=header_backend,
        compile=compile,
        header_graph=header_graph,
        header_graph_includes=header_graph_includes,
        notify=notify,
    )
    snapshot_cache.store(snap, path, _headers, _includes, version, lang, extra=extra)
    return snap
