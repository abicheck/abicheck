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

"""The cache key: the per-request key material (:func:`_dump_cache_extra_key`)
and the content-hashed key over binary, headers, includes and salts
(:func:`_cache_key`). See the package docstring."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from ....extract.cache_header_scan import iter_cache_header_files
from ....storage.code_identity import abicheck_code_fingerprint
from . import salt


def compile_context_header_inputs(
    compile: object | None,
) -> tuple[list[Path], list[Path]] | None:
    """``(files, dirs)`` header inputs *compile* adds (``sysroot``, plus
    ``-I``/``-include``/... in ``gcc_options`` and ``gcc_option_tokens``),
    for :func:`cached_run_dump` to content-hash; ``None`` when one is
    untrackable (see :mod:`abicheck.extract.compile_header_inputs`)."""
    from ...._compiler_options import split_gcc_options
    from ....extract.compile_header_inputs import compile_option_header_inputs

    cc = _normalized_compile_context(compile)
    if cc is None:
        return [], []
    tokens = split_gcc_options(cc.gcc_options) if cc.gcc_options else []
    tokens += cc.gcc_option_tokens
    return compile_option_header_inputs(tokens, sysroot=cc.sysroot)


def _normalized_compile_context(compile: object | None) -> Any:
    """``None`` for "no customisation" -- a ``None`` context and an
    all-default ``CompileContext()`` produce identical dumps, so they must
    share one cache key."""
    from ....compile_context import CompileContext

    if not isinstance(compile, CompileContext):
        return None
    normalized = CompileContext(
        gcc_path=compile.gcc_path,
        gcc_prefix=compile.gcc_prefix,
        gcc_options=compile.gcc_options,
        gcc_option_tokens=tuple(compile.gcc_option_tokens),
        defines=tuple(compile.defines),
        sysroot=compile.sysroot,
        nostdinc=compile.nostdinc,
        frontend=(compile.frontend or "auto").lower(),
        frontend_context=compile.frontend_context or "host",
    )
    return None if normalized.is_default else normalized


def compile_context_cache_field(compile: object | None) -> str:
    """Canonical key material for a (normalized) compile context.

    ``frontend`` is deliberately *excluded*: it is folded in through the
    effective header backend (:func:`effective_header_backend`), which is
    what ``run_dump`` actually resolves. ``defines`` is excluded as a
    receipt-only field -- its rendered ``-D`` tokens already live in
    ``gcc_option_tokens``.
    """
    cc = _normalized_compile_context(compile)
    if cc is None:
        return "compile:default"
    return "compile:" + json.dumps(
        {
            "gcc_path": cc.gcc_path,
            "gcc_prefix": cc.gcc_prefix,
            "gcc_options": cc.gcc_options,
            "gcc_option_tokens": list(cc.gcc_option_tokens),
            "sysroot": str(cc.sysroot) if cc.sysroot is not None else None,
            "nostdinc": cc.nostdinc,
            "frontend_context": cc.frontend_context,
        },
        sort_keys=True,
    )


def effective_header_backend(header_backend: str, compile: object | None) -> str:
    """The backend request ``run_dump`` honours: an explicit (non-``auto``)
    ``compile.frontend`` wins over the bare ``header_backend`` argument
    (``workflows.dump.native._run_dump_uncached``)."""
    cc = _normalized_compile_context(compile)
    if cc is not None and cc.frontend != "auto":
        return str(cc.frontend)
    return header_backend


def _manifest_all_tus_required(dump_manifest: Any) -> bool:
    """Whether every translation unit in *dump_manifest* is ``required=True``.

    Typed ``Any`` (like :func:`_manifest_cache_paths`/
    :func:`_manifest_public_headers`) so :func:`_dump_is_cacheable`'s own
    untyped ``dump_manifest: object | None`` parameter doesn't need an
    unsound cast just to read this.
    """
    return all(tu.required for tu in dump_manifest.translation_units)


def _manifest_frontend_context(dump_manifest: Any) -> str:
    """*dump_manifest*'s own ``frontend_context`` (ADR-050 D5), typed
    ``Any`` for the same reason as :func:`_manifest_all_tus_required`."""
    return dump_manifest.frontend_context  # type: ignore[no-any-return]


def _manifest_roots(dump_manifest: Any) -> str:
    """*dump_manifest*'s own ``roots``, as an order-preserving JSON list.

    :func:`_manifest_cache_paths` folds ``roots`` into a single deduplicated
    ``headers`` list shared with every TU's ``forced_includes`` and the
    manifest's ``public_header_paths`` -- adequate for *content*-hash
    invalidation (any of those files changing must bust the cache), but it
    is blind to which list a header came from. A header already reachable
    via a TU's ``forced_includes`` that is (or isn't) *also* declared a
    ``root`` produces an identical flattened path set either way, yet
    ``roots`` is what :func:`abicheck.dumper.dump` uses as the manifest's
    own declared-surface ``headers`` (``headers=list(dump_manifest.roots)``)
    -- driving provenance/public classification independently of file
    content. Without its own key component, two manifests differing only in
    which already-parsed headers are declared ``roots`` would hash
    identically and silently share a cached snapshot classified under the
    *other* manifest's declared surface (Codex review, PR #636).
    """
    return json.dumps([str(p) for p in dump_manifest.roots])


def _manifest_cache_paths(dump_manifest: Any) -> tuple[list[Path], list[Path]]:
    """Derive ``(headers, includes)``-shaped path lists from *dump_manifest*
    for :func:`abicheck.workflows.dump.cache.key._cache_key`'s existing content-hash
    walk to reuse as-is, rather than teaching that function a second,
    manifest-specific file-discovery mechanism.

    ``headers`` is every declared root, plus every translation unit's own
    ``forced_includes``, plus the manifest's own ``public_header_paths``
    (deduplicated, first-declared order); ``includes`` is every TU's own
    ``-I`` search directory, plus the manifest's ``public_header_dirs``,
    plus each declared header's own *implicit* parent-directory include
    root (:func:`abicheck.header_utils._implicit_header_includes` — the same
    "a declared header's own directory is implicitly searchable via
    quote-includes, with no ``-I`` needed" rule ADR-050 D1 already
    documents, and the identical helper the legacy CLI path's own
    :func:`abicheck.header_utils.resolve_inferred_header_roots` uses) — a
    header force-included with no matching TU ``includes``/manifest
    ``public_header_dirs`` entry can still quote-include an unnamed sibling
    support header from that same directory, and that sibling's own content
    must still bust the cache (Codex review, PR #636). This only feeds
    :func:`abicheck.workflows.dump.cache.key._cache_key`'s *content* hashing (so an
    edit to any file a manifest-driven dump could actually read busts the
    cache -- including a ``public_header_paths`` entry that names neither a
    root nor any TU's forced-include, e.g. a provenance-only header) — the
    manifest's own *structural* identity (TU names/order/
    ``required``/``contributes_to_abi``/``project_owned``) is a separate,
    additional ``extra``-key input (see :func:`cached_run_dump`), since a
    content walk over these same paths cannot see it at all: renaming a TU,
    or flipping ``contributes_to_abi``, changes nothing here hashes.
    """
    from ....header_utils import _implicit_header_includes

    seen_headers: set[Path] = set()
    headers: list[Path] = []
    for h in dump_manifest.roots:
        if h not in seen_headers:
            seen_headers.add(h)
            headers.append(h)
    for tu in dump_manifest.translation_units:
        for h in tu.forced_includes:
            if h not in seen_headers:
                seen_headers.add(h)
                headers.append(h)
    for h in dump_manifest.public_header_paths:
        if h not in seen_headers:
            seen_headers.add(h)
            headers.append(h)

    seen_includes: set[Path] = set()
    includes: list[Path] = []
    for tu in dump_manifest.translation_units:
        for entry in tu.includes:
            if entry.path not in seen_includes:
                seen_includes.add(entry.path)
                includes.append(entry.path)
    for d in dump_manifest.public_header_dirs:
        if d not in seen_includes:
            seen_includes.add(d)
            includes.append(d)
    for d in _implicit_header_includes(headers):
        if d not in seen_includes:
            seen_includes.add(d)
            includes.append(d)
    return headers, includes


def _manifest_public_headers(dump_manifest: Any) -> tuple[list[Path], list[Path]]:
    """``(public_header_paths, public_header_dirs)`` from *dump_manifest*,
    typed ``Any`` (like :func:`_manifest_cache_paths`) so callers whose own
    ``dump_manifest`` parameter is the untyped ``object | None`` this module
    otherwise uses don't need an unsound cast just to read these two fields.
    """
    return list(dump_manifest.public_header_paths), list(
        dump_manifest.public_header_dirs
    )


def _public_include_search_dirs_key_field(
    public_include_search_dirs: list[Path] | None, sep: str
) -> str:
    """Cache-key material for *public_include_search_dirs* that keeps
    ``None`` (omitted -- the dump falls back to widening off ``includes``)
    distinct from an explicit ``[]`` (given, and disables widening
    entirely) -- both previously collapsed to the identical empty-string
    material via a bare ``... or []``, so two otherwise-identical cacheable
    dumps differing only in which of the two the caller meant could share a
    persisted snapshot even though their declarations receive different
    provenance origins (Codex review, fresh evidence).

    A leading presence marker (``"0"``/``"1"``, never a path character)
    keeps the two apart regardless of what directories (if any) are given.
    """
    if public_include_search_dirs is None:
        return "0"
    return "1" + sep + sep.join(sorted(str(p) for p in public_include_search_dirs))


def _dump_cache_extra_key(
    binary_fmt: str,
    header_backend: str,
    public_headers: list[Path] | None,
    public_header_dirs: list[Path] | None,
    lang: str = "c++",
    *,
    uses_ast: bool = True,
    lang_explicit: bool = False,
    public_include_search_dirs: list[Path] | None = None,
    compile: object | None = None,
) -> str:
    """Build the ``extra`` cache-key material for a cacheable dump — every
    input to ``run_dump`` that affects its output besides the binary content
    / headers / includes / version / lang that
    :func:`abicheck.workflows.dump.cache.key._cache_key` already hashes directly.

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
    layout facts — into the merged result (Codex review).

    Also hashed for a genuinely-unpinned ``"auto"`` request (raw
    ``header_backend == "auto"`` AND ``ABICHECK_AST_FRONTEND`` unset) when
    ``ABICHECK_ALLOW_AST_FALLBACK`` explicitly enables fallback, even though
    :func:`_resolve_header_backend` optimistically resolves that to
    ``"castxml"``: ``dumper._header_ast_parser``'s G16 logic may then
    runtime-fallback such a request from castxml to clang (a toolchain-
    version mismatch or a direct-include ``#error`` guard) — invisible to
    this static, content-blind resolution, which can't know in advance
    whether castxml will actually succeed for these specific headers. That
    opted-in fallback's resulting snapshot is clang-sourced and gets the identical
    ``attach_clang_layout`` enrichment an explicit ``--ast-frontend clang``
    dump would, so its cache key must depend on the layout tool's identity
    too (Codex review). An EXPLICIT ``castxml`` request (or an
    ``ABICHECK_AST_FRONTEND=castxml`` pin) never triggers this fallback
    (the castxml failure surfaces verbatim instead), so it's correctly
    excluded — the tool truly never runs for those.

    ``lang_explicit`` (G31 Phase C follow-up): whether *lang* is a genuinely
    explicit request rather than the request-level default (see
    :attr:`abicheck.workflows.service_requests.DumpRequest.lang_explicit`). Two calls with the
    identical *lang* string but different ``lang_explicit`` can resolve to a
    genuinely different force-C++-vs-auto-detect decision on a
    language-ambiguous header, and therefore a different parsed AST — folded
    into the key so they can never collide.

    ``public_include_search_dirs`` (round 13): a real, provenance-affecting
    input on its own (see ``_run_dump_uncached``'s own docstring) — folded
    in the same way ``public_headers``/``public_header_dirs`` already are,
    so two calls differing only in it can never share a cache entry.
    """
    from ....extract.header_ast_backend import _resolve_header_backend

    header_backend = effective_header_backend(header_backend, compile)
    compile_field = compile_context_cache_field(compile)
    _cc = _normalized_compile_context(compile)
    gcc_path = _cc.gcc_path if _cc is not None else None
    gcc_prefix = _cc.gcc_prefix if _cc is not None else None
    resolved_backend = _resolve_header_backend(header_backend)
    if not uses_ast:
        # A binary-only dump never invokes an AST frontend or compiler. Do not
        # execute PATH-selected tools merely to construct an irrelevant cache key.
        sep = "\x00"
        return sep.join(
            [
                binary_fmt,
                resolved_backend,
                "no-ast",
                sep.join(sorted(str(p) for p in (public_headers or []))),
                sep.join(sorted(str(p) for p in (public_header_dirs or []))),
                str(lang_explicit),
                _public_include_search_dirs_key_field(public_include_search_dirs, sep),
                compile_field,
            ]
        )

    from ....extract.headers.ast_config import _resolve_compiler_binary
    from ....extract.headers.toolchain import (
        _ast_fallback_enabled,
        _auto_ast_fallback_eligible,
        _tool_identity,
    )

    auto_may_fallback_to_clang = (
        _auto_ast_fallback_eligible(header_backend) and _ast_fallback_enabled()
    )
    layout_tool = ""
    if resolved_backend in ("clang", "hybrid") or auto_may_fallback_to_clang:
        from ....extract.headers.clang.layout_tool import find_layout_tool_bin

        layout_tool_bin = find_layout_tool_bin()
        layout_tool = _tool_identity(layout_tool_bin) if layout_tool_bin else ""

    compiler = "cc" if lang == "c" else "c++"

    frontend_tools: list[str] = []
    if resolved_backend in ("castxml", "hybrid"):
        frontend_tools.append(_tool_identity("castxml"))
    if resolved_backend in ("clang", "hybrid") or auto_may_fallback_to_clang:
        try:
            frontend_tools.append(
                _tool_identity(_clang_driver(compiler, gcc_path, gcc_prefix))
            )
        except Exception as exc:  # missing optional backend remains key material
            frontend_tools.append(f"clang-unavailable:{type(exc).__name__}:{exc}")
    compiler_identity = ""
    if resolved_backend in ("castxml", "hybrid"):
        try:
            compiler_bin, _ = _resolve_compiler_binary(compiler, gcc_path, gcc_prefix)
            compiler_identity = _tool_identity(compiler_bin)
        except Exception as exc:
            compiler_identity = f"compiler-unavailable:{type(exc).__name__}:{exc}"

    # G29 Phase A's header-graph attach (service._attach_header_graph) always
    # runs its own internal clang AST pass (clang_header_dump) to build the
    # L2 semantic graph -- unconditionally, regardless of which backend
    # `resolved_backend` above is (a plain castxml dump still gets one). That
    # pass resolves clang with the compile context's gcc_path/gcc_prefix
    # override (None when absent) -- resolve the same way here and hash the actual
    # resolved binary path (not just the bare driver name), so a cache
    # written while clang was missing/at a different PATH entry can't be
    # replayed once clang becomes available/changes, silently keeping a
    # snapshot with degraded or stale header-graph coverage (Codex review).
    import shutil

    from ....errors import SnapshotError

    header_graph_clang = ""
    try:
        clang_driver = _clang_driver(compiler, gcc_path, gcc_prefix)
        resolved_path = shutil.which(clang_driver) or clang_driver
        # The resolved PATH string alone survives an in-place binary swap at
        # the same path (an apt/package upgrade, or a symlink retargeted to a
        # different clang install) -- fold in an mtime+size fingerprint too
        # (Codex review), matching this codebase's existing mtime-based
        # invalidation style (_cache_key's header walk) rather
        # than shelling out to `clang --version` on every cacheable dump's
        # lookup, which would add real latency to the hot path this cache
        # exists to avoid.
        try:
            st = os.stat(resolved_path)
            header_graph_clang = f"{resolved_path}:{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            header_graph_clang = resolved_path
    except SnapshotError:
        header_graph_clang = ""

    sep = "\x00"
    return sep.join(
        [
            binary_fmt,
            resolved_backend,
            *frontend_tools,
            compiler_identity,
            layout_tool,
            header_graph_clang,
            sep.join(sorted(str(p) for p in (public_headers or []))),
            sep.join(sorted(str(p) for p in (public_header_dirs or []))),
            str(lang_explicit),
            _public_include_search_dirs_key_field(public_include_search_dirs, sep),
            compile_field,
        ]
    )


def _clang_driver(compiler: str, gcc_path: str | None, gcc_prefix: str | None) -> str:
    """The clang driver a dump would run for *compiler*, resolved by its owner
    ``extract/compiler_driver``. Raises if clang is not on ``PATH``."""
    from ....extract.compiler_driver import resolve_clang_bin

    return resolve_clang_bin(compiler, gcc_path, gcc_prefix)


def _cache_key(
    binary_path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    extra: str = "",
) -> str:
    """Compute a deterministic cache key from all inputs that affect the snapshot.

    ``extra`` is an opaque, caller-assembled string folding in any additional
    inputs that affect the resulting snapshot but aren't one of this
    function's named parameters (e.g. the binary format, header-AST backend,
    or public-header scoping set) — kept generic here so this module doesn't
    need to know every option a caller's dump pipeline exposes.
    """
    h = hashlib.sha256()
    # Binary content hash — chunked to avoid loading huge files into memory
    try:
        with open(binary_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""  # uncacheable
    # Hash contents, not only mtimes: restored timestamps must not resurrect a
    # stale snapshot. Directory-valued ``-H`` inputs and explicit include roots
    # contribute every header-like descendant reachable by the parser.
    hash_files: set[Path] = set()
    # ADR-050 D1/D2: *not* sorted — headers/includes order is real,
    # load-bearing search-precedence/preprocessing order (the same "order is
    # a real compile difference" rule comparability.py's profile_fingerprint
    # already enforces), not cosmetic. Two dumps requesting the same
    # headers/includes in a different order can legitimately resolve to a
    # different snapshot (e.g. -I a -I b vs -I b -I a with a same-named
    # header in both, or a macro one header defines before another is
    # included) — sorting here would let a warm cache silently serve the
    # wrong order's snapshot. `hash_files` (the transitively-discovered
    # descendants of a directory input) stays a sorted set below: those are
    # an unordered content aggregate, not a caller-ordered sequence.
    for hdr in headers:
        h.update(str(hdr).encode())
        try:
            if hdr.is_dir():
                hash_files.update(iter_cache_header_files(hdr))
            else:
                hash_files.add(hdr)
        except OSError:
            h.update(b"UNREADABLE_HEADER_INPUT")
    for inc in includes:
        h.update(str(inc).encode())
        try:
            hash_files.update(iter_cache_header_files(inc))
        except OSError:
            h.update(b"UNREADABLE_INCLUDE_DIR")
    for hdr in sorted(hash_files):
        h.update(str(hdr).encode())
        try:
            fd = os.open(hdr, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode):
                os.close(fd)
                h.update(b"NONREGULAR")
                continue
            # Preserve the existing mtime invalidation contract as well as
            # hashing contents. The latter catches restored/coarse timestamps;
            # the former also treats a touched header as a changed input.
            h.update(str(file_stat.st_mtime_ns).encode())
            with os.fdopen(fd, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
        except OSError:
            h.update(b"MISSING")
    # Compiler params
    h.update(version.encode())
    h.update(lang.encode())
    h.update(extra.encode())
    h.update(salt.SNAPSHOT_CACHE_VERSION.encode())
    # The snapshot schema version too, automatically: a schema bump means a
    # new field or encoding, and an entry cached before it was written
    # without that field -- loading it back would present "not captured" as
    # this build's answer. Folding it in closes that whole class instead of
    # relying on a hand bump of SNAPSHOT_CACHE_VERSION for each field.
    h.update(f"schema={_snapshot_schema_version()}".encode())
    # And the code that produced it: a snapshot is extraction *output*, so an
    # extractor change with no matching version bump must still miss.
    h.update(f"code={abicheck_code_fingerprint()}".encode())
    return h.hexdigest()


def _snapshot_schema_version() -> int:
    from ....storage.snapshot_schema_versions import SCHEMA_VERSION

    return SCHEMA_VERSION
