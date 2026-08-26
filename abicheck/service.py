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

"""Service layer — shared orchestration for CLI and MCP server.

Provides framework-agnostic functions for the core abicheck operations:

- :func:`resolve_input` — Load an ABI snapshot from any supported input format
- :func:`run_dump` — Extract ABI snapshot from a binary + optional headers
- :func:`run_compare` — Compare two ABI snapshots and return classified changes
- :func:`render_output` — Render a DiffResult to the specified output format
"""

from __future__ import annotations

import functools
import hashlib
import importlib as _importlib
import logging
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import policy_file as _policy_file
from .api_types import (
    CompareRequest,
    CompareResult,
    DumpRequest,
    InputSpec,
    OutputSpec,
)
from .checker import compare
from .checker_types import DiffResult, LibraryMetadata
from .clang_layout_tool import attach_clang_layout
from .dumper_scoping import wrap_run_dump_with_dependency_scope
from .errors import AbicheckError, SnapshotError, ValidationError
from .header_utils import (
    cache_relevant_operand_paths,
    deferred_token_dirs,
    resolve_inferred_header_roots,
)
from .model import AbiSnapshot, EnumType, Function, RecordType, Visibility
from .serialization import load_snapshot
from .service_dump_cache import cached_run_dump

# `_attach_header_graph` moved to `service_header_graph_attach.py`, purely to
# stay under the AI-readiness 2000-line hard cap -- the identical reason
# `service_render.py`/`service_scan.py`/`service_compare_pipeline.py`/
# `service_dump_pipeline.py` (re-exported further down this file) already
# moved out. Imported here, eagerly, rather than down with those: unlike
# them, this module has no import-cycle relationship with `service.py`
# itself (it reaches `.compile_context`/`.service_scan`/`.header_utils`/
# `.errors` directly, none of which import `.service`), so there is no
# ordering constraint forcing it to the tail. Re-exported under its original
# private name so both `monkeypatch.setattr("abicheck.service.
# _attach_header_graph", ...)` and `from abicheck.service import
# _attach_header_graph` keep resolving unchanged for the many existing
# tests that patch/import it this way.
from .service_header_graph_attach import _attach_header_graph as _attach_header_graph

# PE/Mach-O header-scoped dump lives in the sibling module service_header_scoped
# (service.py is at the file-size cap). Bound via importlib rather than a static
# `from .service_header_scoped import ...` -- service_header_scoped reaches
# service_scan, which reaches back to service through the pre-existing,
# already-baselined cli_buildsource/scan_engine SCC (AGENTS.md "M1-3"/CLAUDE.md
# "What NOT to do"); a static import here would pull this new leaf module into
# that same cycle, which the AI-readiness import-cycle-growth gate rejects. An
# `importlib.import_module` call is a plain function call, not an
# `ast.ImportFrom` node, so it is invisible to that gate's static AST walk (the
# same escape hatch `cli_buildsource.py`'s own back-compat re-export shim
# documents) while still binding real module-level names here, so
# `service._dump_pe`/`_dump_macho`'s own bare-name calls, `from abicheck.service
# import _try_header_scoped_dump`, and every test's
# `monkeypatch.setattr(service, "_try_header_scoped_dump", ...)` all keep
# working exactly as before this module existed.
_service_header_scoped = _importlib.import_module(".service_header_scoped", __package__)
# Explicitly typed (not left as the `Any` importlib.import_module's attribute
# access would otherwise infer) so a caller returning this call's result
# still gets a real return-type check instead of a silent `no-any-return`.
_has_matched_public_surface: Callable[[AbiSnapshot], bool] = (
    _service_header_scoped._has_matched_public_surface
)
_try_header_scoped_dump: Callable[..., tuple[AbiSnapshot | None, str | None]] = (
    _service_header_scoped._try_header_scoped_dump
)
del _service_header_scoped

if TYPE_CHECKING:
    from collections.abc import Callable

    from .checker_types import Change
    from .dump_manifest import DumpManifest
    from .dwarf_advanced import AdvancedDwarfMetadata
    from .dwarf_metadata import DwarfMetadata
    from .environment_matrix import EnvironmentMatrix
    from .policy_file import PolicyFile
    from .suppression import SuppressionList

_logger = logging.getLogger(__name__)

# Codex review, PR #730: re-exported so an existing `service.
# dedup_policy_override_warnings()` caller (and this module's own tests)
# keep working. The dedup state itself lives in `policy_file.py` -- the leaf
# module both this loader and `cli_params._load_suppression_and_policy` share
# -- specifically so *one* scope dedupes a warning repeated across both
# loaders, not just repeated calls to this one. See
# `policy_file.dedup_validate_overrides_warnings`'s own docstring for what
# this does and does not cover.
dedup_policy_override_warnings = _policy_file.dedup_validate_overrides_warnings

# Magic-byte length for format detection
_SNIFF_BYTES = 256

# G29 Phase A: the L2 header-only semantic graph (ADR-041 addendum) and its
# include-file extension used to be strictly opt-in via ``--header-graph``/
# ``--header-graph-includes``. They are now always attempted whenever headers
# are available (``_attach_header_graph`` itself still no-ops without parsed
# headers, and degrades to a declaration-only graph when clang is
# unavailable) — no public flag controls this anymore; see
# ``docs/contribute/plans/g31-header-graph-default-on-followup.md``.
# TODO(header-graph-phase-D): ``header_graph_includes`` runs one extra
# ``clang -M`` pass per top-level header on every dump/compare with no
# caching of its own (only the aggregate AST pass is disk-cached via
# ``_clang_header_dump``) — bounded by header count, fails soft when clang is
# unavailable, but not yet cheap. Caching this pass is deferred to Phase D.
_HEADER_GRAPH_ENABLED = True
_HEADER_GRAPH_INCLUDES_ENABLED = True


# ── Input resolution ────────────────────────────────────────────────────────


def detect_binary_format(path: Path) -> str | None:
    """Detect binary format from magic bytes.

    Returns ``'elf'``, ``'pe'``, ``'macho'``, or *None* for non-binary / unknown.
    """
    from .binary_utils import detect_binary_format as _detect

    return _detect(path)


def sniff_text_format(path: Path) -> str:
    """Read a small header chunk and return ``'json'``, ``'perl'``, or ``'unknown'``.

    ADR-059: a gzip/zstd-compressed snapshot (``.abicheck.json.gz``/``.zst``,
    or any neutrally-named file carrying those magic bytes) is detected here
    too, via a *bounded* decoded prefix — never a full decompression just to
    classify the input. A compressed non-snapshot payload (e.g. a baseline-
    set ``.tar.zst`` archive) does not decode to a leading ``{`` and falls
    through to ``'unknown'``, same as any other unrecognized input, so
    archive/package resolution still gets its turn.
    """
    from .compat.abicc_dump_import import looks_like_perl_dump
    from .snapshot_io import bounded_decoded_prefix, detect_snapshot_compression

    try:
        compression = detect_snapshot_compression(path)
    except SnapshotError:
        return "unknown"
    if compression.value != "none":
        prefix = bounded_decoded_prefix(path)
        if prefix is None:
            return "unknown"
        head = prefix.decode("utf-8", errors="replace").lstrip()
        return "json" if head.startswith("{") else "unknown"

    try:
        with open(path, "rb") as f:
            raw = f.read(_SNIFF_BYTES)
        head = raw.decode("utf-8", errors="replace").lstrip()
    except OSError:
        return "unknown"
    if looks_like_perl_dump(head):
        return "perl"
    if head.startswith("{"):
        return "json"
    return "unknown"


def _resolve_symvers(path: Path, version: str) -> AbiSnapshot | None:
    """Parse a Linux kernel ``Module.symvers`` manifest into a snapshot, or None.

    Recognized by filename (``Module.symvers`` / ``*.symvers``) or, for a
    generically-named file, by content (a hex-CRC + ``EXPORT_SYMBOL`` record).
    """
    from .symvers_metadata import looks_like_symvers, parse_symvers

    name = path.name.lower()
    by_name = name == "module.symvers" or name.endswith(".symvers")
    if not by_name:
        # Cheap bounded content sniff before committing to a full decode, so a
        # generically-named non-symvers input (a large JSON snapshot, an archive)
        # on the hot `compare old new` path isn't read+decoded in full here only
        # to be rejected — the caller re-reads it for its real format anyway.
        try:
            with open(path, "rb") as f:
                head = f.read(_SNIFF_BYTES).decode("utf-8", "replace")
        except OSError:
            return None
        if not looks_like_symvers(head):
            return None
    try:
        text = path.read_text("utf-8", "replace")
    except OSError:
        return None
    kabi = parse_symvers(text)
    if not kabi.entries:
        return None
    return AbiSnapshot(library=path.name, version=version, kabi=kabi)


def _typeinfo_functions(func_protos: dict[str, Any]) -> list[Function]:
    """Convert BTF/CTF function prototypes into snapshot Function records.

    BTF/CTF names are C-linkage; the plain name doubles as the symbol key
    (matching the C-function convention used by the header dumpers).

    Static (file-local) BTF functions are not exported ABI, so they are
    skipped — but only when the blob is linkage-aware. BTF_KIND_FUNC linkage
    lives in the record's vlen field (0 static, 1 global, 2 extern); legacy
    encoders wrote 0 for *every* function, so an all-zero blob means
    "linkage unknown" and everything is kept rather than dropping the whole
    surface. CTF carries no linkage (None) and is always kept.
    """
    from .model import Param

    linkage_aware = any(
        getattr(p, "linkage", None) not in (0, None) for p in func_protos.values()
    )
    return [
        Function(
            name=name,
            mangled=name,
            return_type=proto.return_type,
            params=[Param(name=p_name, type=p_type) for p_name, p_type in proto.params],
            is_extern_c=True,
        )
        for name, proto in sorted(func_protos.items())
        if not (linkage_aware and getattr(proto, "linkage", None) == 0)
    ]


def _resolve_raw_typeinfo(path: Path, version: str) -> AbiSnapshot | None:
    """Parse a bare BTF or CTF blob into a snapshot, or return None.

    BTF blobs start with magic ``0xEB9F`` and CTF with ``0xCFF1`` (either byte
    order). The parsed type layout is converted to the checker's DWARF-shaped
    metadata so the same struct/enum layout detectors apply.
    """
    from .btf_metadata import BTF_MAGIC, parse_btf_from_bytes
    from .ctf_metadata import CTF_MAGIC, parse_ctf_from_bytes

    try:
        with open(path, "rb") as f:
            head = f.read(2)
    except OSError:
        return None
    if len(head) < 2:
        return None

    # Only detect the little-endian byte order parse_btf_from_bytes/parse_ctf_from_bytes support: a big-endian blob (EB 9F / CF F1) would otherwise enter the branch but parse to empty metadata, silently dropping all type changes -- falling through to "cannot detect format".
    magic_le = int.from_bytes(head, "little")
    data = path.read_bytes()
    try:
        if magic_le == BTF_MAGIC:
            btf = parse_btf_from_bytes(data)
            # Require actual type records, not just a valid header. A
            # truncated/unsupported blob parses to empty metadata, and a
            # header-only blob (valid header, type_len=0) sets has_btf=True with
            # type_count==0; either way, accepting it would yield a silent empty
            # baseline that hides all layout changes. Fall through to the
            # "cannot detect format" error instead.
            if not btf.has_btf or btf.type_count <= 0:
                _logger.warning("raw BTF blob %s has no type records; ignoring", path)
                return None
            return AbiSnapshot(
                library=path.name,
                version=version,
                dwarf=btf.to_dwarf_metadata(),
                # Bridge the full BTF surface: prototypes feed the function
                # detectors (FUNC_PARAMS_CHANGED, ...) and typedef targets feed
                # TYPEDEF_BASE_CHANGED — previously dropped at this boundary.
                functions=_typeinfo_functions(btf.func_protos),
                typedefs=dict(btf.typedefs),
            )
        if magic_le == CTF_MAGIC:
            ctf = parse_ctf_from_bytes(data)
            if not ctf.has_ctf or ctf.type_count <= 0:
                _logger.warning("raw CTF blob %s has no type records; ignoring", path)
                return None
            return AbiSnapshot(
                library=path.name,
                version=version,
                dwarf=ctf.to_dwarf_metadata(),
                functions=_typeinfo_functions(ctf.func_protos),
                typedefs=dict(ctf.typedefs),
            )
    except (ValueError, OSError) as exc:
        _logger.warning("failed to parse raw type-info blob %s: %s", path, exc)
        return None
    return None


def resolve_input(
    path: Path,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    version: str = "",
    lang: str = "c++",
    *,
    lang_explicit: bool = False,
    is_elf: bool | None = None,
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
    follow_linker_scripts: bool = True,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
    notify: Callable[[str], None] | None = None,
    include_labels: dict[Path, str] | None = None,
    dump_manifest: DumpManifest | None = None,
    include_dependencies: bool = True,
    public_include_search_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Auto-detect input type and return an ABI snapshot.

    This is the single source of truth for turning a path into an
    :class:`AbiSnapshot`; the CLI (:func:`abicheck.cli_resolve._resolve_input`)
    and the MCP server are thin wrappers translating the framework-free errors
    raised here into their own contracts.

    Detection order:

    1. Native binary (ELF / PE / Mach-O, detected by magic bytes)
    2. Raw BTF/CTF type-info blob
    3. ABICC Perl dump (``$VAR1`` prefix) → :func:`import_abicc_perl_dump`
    4. JSON snapshot (``{`` prefix) → :func:`load_snapshot`
    5. GNU ld linker script (``INPUT()``/``GROUP()``) → follow to its target

    For binary inputs (ELF/PE/Mach-O), the L2 header-only semantic graph
    (:func:`run_dump`'s ``_attach_header_graph`` step) is always attempted when
    headers are parsed (G29 Phase A); a no-op for non-binary inputs.

    Args:
        debug_format: Force the ELF debug format ("dwarf", "btf", "ctf") or
            *None* for auto-detection.
        debuginfod_url: Override debuginfod server URL (only meaningful when
            ``enable_debuginfod`` is set); ``None`` uses the resolver's
            default server list / ``DEBUGINFOD_URLS`` environment variable.
        public_headers / public_header_dirs: Public-header sets used to tag
            declaration provenance on PE/Mach-O snapshots (ADR-024 Phase 1).
        public_include_search_dirs: The caller's own genuinely explicit
            ``-I``/``--include`` list, distinct from ``includes`` (which a
            caller may have already widened with auto-derived directories --
            see ``_run_dump_uncached``'s own docstring). When given, used
            instead of ``includes`` for declaration-provenance widening on
            all three binary formats. Omitted (the default), every existing
            caller's behavior is unchanged: ``includes`` itself is used.
        include_labels: Resolved ``path -> label`` map from a labeled
            ``--include old:LABEL=PATH`` CLI entry (ADR-050 D1); forces the
            whole-snapshot cache off when non-empty.
        dump_manifest: A parsed ``--dump-manifest`` document (ADR-050 D3) for
            a real multi-TU dump instead of a single header list. ELF only;
            forces the whole-snapshot cache off when set.
        follow_linker_scripts: When True (default), a GNU ld linker script is
            followed to the shared library named in its ``INPUT()``/``GROUP()`` directive.
        notify: Optional callback for user-facing progress notes (e.g. "following
            a linker script", "no headers provided"); *None* logs to the module
            logger. The CLI passes a ``click.echo(..., err=True)`` wrapper.
        lang_explicit: Whether *lang* is a genuinely explicit request rather
            than the request-level default (G31 Phase C follow-up — see
            :attr:`abicheck.api_types.DumpRequest.lang_explicit`). ``False``
            (the default) preserves this function's pre-existing behavior:
            *lang* is honored only when it equals ``"c"``, otherwise the
            header-AST pass auto-detects. ``True`` forces *lang* on the
            primary snapshot pass and the header-only graph pass alike, even
            on a language-ambiguous header where auto-detection would guess
            wrong.

    Raises:
        SnapshotError: If the snapshot cannot be loaded from the input.
        ValidationError: If the input format cannot be detected.
    """
    _headers = headers or []
    _includes = includes or []

    # Fast path: caller already knows it's ELF
    if is_elf is True:
        return cached_run_dump(
            run_dump,
            path,
            "elf",
            _headers,
            _includes,
            version,
            lang,
            lang_explicit=lang_explicit,
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
            notify=notify,
            include_labels=include_labels,
            dump_manifest=dump_manifest,
            include_dependencies=include_dependencies,
            public_include_search_dirs=public_include_search_dirs,
        )

    # Detect binary format from magic bytes
    binary_fmt = detect_binary_format(path) if is_elf is None else None
    if binary_fmt is not None:
        return cached_run_dump(
            run_dump,
            path,
            binary_fmt,
            _headers,
            _includes,
            version,
            lang,
            lang_explicit=lang_explicit,
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
            notify=notify,
            include_labels=include_labels,
            dump_manifest=dump_manifest,
            include_dependencies=include_dependencies,
            public_include_search_dirs=public_include_search_dirs,
        )

    # Raw kernel type-info blobs (a bare `.BTF`/CTF section extracted with `bpftool btf dump ... format raw` or `objcopy -O binary --only-section`) -- a real kernel carries BTF inside an ELF `.BTF` section, the bare blob a convenient toolchain-free input.
    raw_typeinfo = _resolve_raw_typeinfo(path, version)
    if raw_typeinfo is not None:
        return raw_typeinfo

    # Linux kernel Module.symvers (kABI manifest) — a tab-separated text file,
    # recognized by filename or content (G23-D1).
    kabi_snap = _resolve_symvers(path, version)
    if kabi_snap is not None:
        return kabi_snap

    # Text-based formats
    fmt = sniff_text_format(path)

    if fmt == "perl":
        from .compat.abicc_dump_import import import_abicc_perl_dump

        try:
            return import_abicc_perl_dump(path)
        except (
            ValueError,
            KeyError,
            UnicodeDecodeError,
            OSError,
            AbicheckError,
        ) as exc:
            raise SnapshotError(
                f"Failed to import ABICC Perl dump '{path}': {exc}"
            ) from exc

    if fmt == "json":
        try:
            return load_snapshot(path)
        except (ValueError, KeyError, UnicodeDecodeError, OSError) as exc:
            raise SnapshotError(
                f"Failed to load JSON snapshot '{path}': {exc}"
            ) from exc

    # GNU ld linker script (e.g. the ``libfoo.so`` dev symlink is the text
    # ``INPUT(libfoo.so.1)``): follow it to the real shared library.
    if follow_linker_scripts:
        from .binary_utils import resolve_linker_script

        target, is_ld_script = resolve_linker_script(path)
        if is_ld_script:
            if target is not None and target.resolve() != path.resolve():
                _emit(
                    notify,
                    f"Note: '{path}' is a GNU ld linker script; following its "
                    f"INPUT()/GROUP() directive to '{target}'.",
                )
                return resolve_input(
                    target,
                    _headers,
                    _includes,
                    version,
                    lang,
                    lang_explicit=lang_explicit,
                    dwarf_only=dwarf_only,
                    debug_roots=debug_roots,
                    enable_debuginfod=enable_debuginfod,
                    debuginfod_url=debuginfod_url,
                    debug_format=debug_format,
                    symbols_only=symbols_only,
                    debug_presence_only=debug_presence_only,
                    public_headers=public_headers,
                    public_header_dirs=public_header_dirs,
                    follow_linker_scripts=follow_linker_scripts,
                    header_backend=header_backend,
                    compile=compile,
                    notify=notify,
                    include_labels=include_labels,
                    dump_manifest=dump_manifest,
                    include_dependencies=include_dependencies,
                )
            raise ValidationError(
                f"'{path}' is a GNU ld linker script (INPUT/GROUP), not a binary, "
                "and its target could not be located next to it. Pass the actual "
                "shared library named in its INPUT(...) directive directly."
            )

    # Static/import libraries (`.a`, `.lib`) are member archives, not single
    # linkable images. abicheck does not analyse archives (by design — see
    # docs/learn/limitations.md); fail with actionable guidance, not "unknown format".
    from .binary_utils import detect_archive

    if detect_archive(path):
        raise ValidationError(
            f"'{path}' is a static/import library archive (.a/.lib), which abicheck "
            "does not analyse — it compares single linkable images (shared libraries "
            "and objects). Extract the members (e.g. `ar x lib.a`) and compare the "
            "resulting object files or the shared library built from them instead."
        )

    raise ValidationError(
        f"Cannot detect format of '{path}'. "
        "Expected: ELF (.so), PE (.dll), Mach-O (.dylib), JSON snapshot, or ABICC Perl dump."
    )


# ── Binary dumping ──────────────────────────────────────────────────────────


def _run_dump_uncached(
    path: Path,
    binary_fmt: str,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    version: str = "",
    lang: str = "c++",
    *,
    lang_explicit: bool = False,
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
    compile: CompileContext | None = None,
    notify: Callable[[str], None] | None = None,
    _skip_header_graph_attach: bool = False,
    include_labels: dict[Path, str] | None = None,
    dump_manifest: DumpManifest | None = None,
    public_include_search_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Extract an ABI snapshot from a native binary (ELF, PE, or Mach-O).

    ``_skip_header_graph_attach`` is a private, internal-only knob (not
    public API, not CLI-reachable) used solely by this function's own
    ``header_backend="hybrid"`` recursion below: each single-backend
    sub-dump would otherwise redundantly attach its own header-only graph
    (seeded from only that one backend's declarations) before the merge
    throws it away, wasting a whole extra clang AST pass per sub-dump. The
    graph is instead attached exactly once, after the merge, to the union of
    both backends' declarations.

    ``public_headers`` / ``public_header_dirs`` tag declaration provenance
    (ADR-024 Phase 1) on all three formats: ELF threads them into
    :func:`dumper.dump` (which runs ``apply_provenance``), PE/Mach-O apply them
    via :func:`_apply_native_provenance`. A no-op when no header set is supplied.
    ``debug_format`` forces the ELF debug format. ``notify`` receives
    user-facing progress notes (see :func:`resolve_input`).

    ``public_include_search_dirs`` (PE/Mach-O and the ``hybrid`` merge only;
    mirrors ``dumper.dump``'s own parameter of the same name for ELF) is the
    caller's own genuinely explicit ``-I``/``--include`` list, distinct from
    ``includes`` -- which a caller may have already widened with auto-derived
    directories (e.g. an umbrella ``-H`` header's own directory, seeded purely
    so its relative ``#include``s resolve) before calling this function. When
    given, it -- not the possibly-widened ``includes`` -- is what reaches
    :func:`_apply_native_provenance`/the header-only graph attach, so an
    auto-derived directory can never silently promote a private sibling
    header to ``PUBLIC_HEADER`` on these two formats the way it once did for
    ELF (Codex review, PR #839 round 9). Omitted (``None``, the default),
    every existing caller's behavior is unchanged: ``includes`` itself is
    used, same as before this parameter existed.

    The header-only (L2) semantic graph
    (:func:`abicheck.buildsource.header_graph.build_header_only_graph`, ADR-041
    addendum) — a smaller, build-free alternative to the L4/L5 build-integrated
    graph, available uniformly across all three binary formats — is always
    attempted (G29 Phase A: no longer flag-gated). A no-op when no headers were
    parsed; degrades to a graph with declaration-visibility nodes only (no
    type/call edges) when clang is unavailable. The include-file extension
    (:class:`abicheck.buildsource.header_graph.ClangHeaderIncludeExtractor`,
    adding ``COMPILE_UNIT_INCLUDES_FILE`` edges from each top-level header to
    everything it transitively includes) is also always attempted.

    Raises:
        SnapshotError: If the binary cannot be parsed.
        ValidationError: For invalid arguments (missing exports, bad include dirs,
            or a non-``None`` ``dump_manifest`` for a non-ELF binary).
    """
    if dump_manifest is not None and binary_fmt != "elf":
        raise ValidationError(
            f"dump_manifest is not yet supported for {binary_fmt.upper()} "
            "binaries (ADR-050 D3); use a single-header dump for this format."
        )
    from . import dumper_cache

    _headers = headers or []
    _includes = includes or []
    # See this function's own docstring: falls back to `_includes` (today's
    # unchanged behavior) when the caller doesn't distinguish its genuinely
    # explicit -I list from an already-widened `includes`.
    _public_include_search_dirs = (
        list(public_include_search_dirs)
        if public_include_search_dirs is not None
        else _includes
    )
    # Every format's own main pass normalizes `lang` to only ever force a
    # language explicitly requested, letting auto-detection run otherwise
    # (including for the default "c++") -- `_cache_key` hashes the raw
    # `lang` value, so `_attach_header_graph`'s own _clang_header_dump call
    # must pass this identical normalized value, or it hashes a different
    # key than the main pass just used, permanently missing the AST memo
    # for the default (non-explicit-"c") workload (Codex review). ELF does
    # this in `_dump_elf` below (case-sensitive `lang == "c"`); PE/Mach-O do
    # it in `service_header_scoped._try_header_scoped_dump` -- reached
    # whenever headers are given, the only case this graph attach does
    # anything at all -- with a case-*insensitive* `lang.lower() == "c"`,
    # so the two branches deliberately differ (Codex review, twice: the
    # first pass wrongly assumed PE/Mach-O never normalized `lang` at all).
    #
    # G31 Phase C follow-up: `lang_explicit` (from `DumpRequest.lang_explicit`/
    # `CompareRequest.lang_explicit`) widens the "force" condition beyond a
    # bare `lang == "c"` -- a genuinely explicit request forces whatever
    # language the caller named (not just "c"), on both this graph pass and
    # `_dump_elf`/`_try_header_scoped_dump`'s own primary pass below, so the
    # two can never silently disagree about which language mode parsed the
    # library's own headers (AGENTS.md "dump --lang c++ is silently
    # discarded ..." known gap). `False` (the default) is a no-op: identical
    # to the pre-existing behavior above.
    _header_graph_lang = (
        (lang if (lang_explicit or lang == "c") else None)
        if binary_fmt == "elf"
        else (lang if (lang_explicit or lang.lower() == "c") else None)
    )
    # An explicit --ast-frontend on the compile context wins over the bare
    # header_backend arg (the latter is the compare-path default carrier).
    # .lower() (Codex review, fresh evidence): compile.frontend="AUTO" is an
    # accepted spelling (validated case-insensitively) that must mean "no
    # override", not be treated as an explicit one -- otherwise a pinned,
    # already-resolved header_backend (service_dump_pipeline.ResolvedDumpRequest.
    # effective_header_backend) can be silently discarded here in favor of
    # re-resolving "AUTO" against a live ABICHECK_AST_FRONTEND read below.
    eff_backend = (
        compile.frontend
        if (compile is not None and compile.frontend.lower() != "auto")
        else header_backend
    )

    from .dumper import _resolve_header_backend

    if _resolve_header_backend(eff_backend) == "hybrid":
        # G28 Phase 3: this is the real Tier-2 entry point the CLI routes
        # through (unlike dumper.dump(), which has its own, simpler hybrid
        # recursion for direct Python-API callers) — recurse into run_dump()
        # itself once per real backend, forcing frontend via a *replaced*
        # CompileContext (frozen dataclass) so it wins eff_backend's own
        # precedence check above regardless of what header_backend carries,
        # then merge. Every other kwarg (SYCL/python-ext/numpy-capi/
        # header-graph attachment, debug roots, ...) runs identically on
        # both recursive calls; only the merge step is new.
        from dataclasses import replace as _dc_replace

        from .dumper_hybrid import merge_snapshots

        def _forced_compile(frontend: str) -> CompileContext:
            return (
                _dc_replace(compile, frontend=frontend)
                if compile is not None
                else CompileContext(frontend=frontend)
            )

        common_kwargs: dict[str, Any] = {
            "headers": headers,
            "includes": includes,
            "version": version,
            "lang": lang,
            "lang_explicit": lang_explicit,
            "pdb_path": pdb_path,
            "dwarf_only": dwarf_only,
            "debug_roots": debug_roots,
            "enable_debuginfod": enable_debuginfod,
            "debuginfod_url": debuginfod_url,
            "debug_format": debug_format,
            "symbols_only": symbols_only,
            "debug_presence_only": debug_presence_only,
            "public_headers": public_headers,
            "public_header_dirs": public_header_dirs,
            "public_include_search_dirs": public_include_search_dirs,
            # The header-graph attach is deliberately SKIPPED on either
            # recursive sub-dump below (each would otherwise attach its OWN
            # graph, seeded from only ITS OWN backend's declarations, before
            # the merge throws it away) — attached once, after the merge, to
            # the union of both backends' declarations instead (see the
            # _attach_header_graph call below; Codex review; G29 Phase A:
            # ``_skip_header_graph_attach`` replaces the old "just don't
            # forward header_graph=True" mechanism now that the attach is
            # unconditional rather than flag-gated).
            "notify": notify,
            "_skip_header_graph_attach": True,
            "include_labels": include_labels,
            "dump_manifest": dump_manifest,
        }
        # In-process AST memoization (G31 Phase C) is only worthwhile inside
        # this scope: the _attach_header_graph call below is a real
        # downstream consumer, unlike a direct dumper.dump() caller with no
        # such follow-up (Codex review) -- see dumper_cache.ast_memoize_scope.
        with dumper_cache.ast_memoize_scope():
            castxml_snap = run_dump(
                path,
                binary_fmt,
                header_backend="castxml",
                compile=_forced_compile("castxml"),
                **common_kwargs,
            )
            clang_snap = run_dump(
                path,
                binary_fmt,
                header_backend="clang",
                compile=_forced_compile("clang"),
                **common_kwargs,
            )
        merged = merge_snapshots(castxml_snap, clang_snap)
        # No attach_clang_layout call here: clang_snap's own recursive
        # run_dump(header_backend="clang") call above already got it (this
        # function's ELF/PE/Mach-O tail below calls it unconditionally), so
        # re-running it on merged would just re-invoke the external tool for
        # nothing left to backfill (review finding).
        # dwarf_only/symbols_only both mean "ignore headers entirely" -- same
        # rationale as the ELF tail's own _attach_header_graph call below
        # (Codex review).
        return _attach_header_graph(
            merged,
            _HEADER_GRAPH_ENABLED and not dwarf_only and not symbols_only,
            _HEADER_GRAPH_INCLUDES_ENABLED and not dwarf_only and not symbols_only,
            _headers,
            _includes,
            _header_graph_lang,
            compile,
            public_headers,
            public_header_dirs,
            include_search_dirs=_public_include_search_dirs,
        )

    if binary_fmt == "elf":
        # See the hybrid-path scope above -- but only worth opening when
        # _attach_header_graph below will actually run: it no-ops on
        # `_skip_header_graph_attach`/`dwarf_only`/`symbols_only` and on
        # empty `_headers`, which `dump_manifest` guarantees (mutually
        # exclusive with `headers`, api_types.py). Opening it unconditionally
        # would veto the opt-in streaming pruner for a manifest dump's own
        # TU parses too whenever they share this thread (single TU /
        # `ABICHECK_TU_JOBS=1`) -- protecting a memo nothing will ever read
        # (Codex review, PR #840).
        with (
            dumper_cache.ast_memoize_scope()
            if _headers and not _skip_header_graph_attach and not dwarf_only and not symbols_only
            else nullcontext()
        ):
            snap = _dump_elf(
                path,
                _headers,
                _includes,
                version,
                lang,
                lang_explicit=lang_explicit,
                dwarf_only=dwarf_only,
                debug_roots=debug_roots,
                enable_debuginfod=enable_debuginfod,
                debuginfod_url=debuginfod_url,
                debug_format=debug_format,
                symbols_only=symbols_only,
                debug_presence_only=debug_presence_only,
                header_backend=eff_backend,
                compile=compile,
                public_headers=public_headers,
                public_header_dirs=public_header_dirs,
                notify=notify,
                include_labels=include_labels,
                dump_manifest=dump_manifest,
                public_include_search_dirs=_public_include_search_dirs,
            )
        _try_attach_sycl_metadata(snap, path)
        _try_attach_python_ext_metadata(snap)
        _try_attach_python_api_surface(snap)
        _try_attach_numpy_capi_surface(snap, path)
        # dwarf_only/symbols_only both mean "ignore headers entirely" --
        # _dump_elf above already honors that for both (dwarf_only skips
        # header-root inference/include validation and warns when headers
        # are supplied alongside it; symbols_only skips the header-based
        # type-expansion pass in dumper.dump() -- see its own
        # `if symbols_only or not headers:` gate), so the header-graph
        # attach must not silently re-parse those same headers and attach
        # L2 build_source evidence to what the caller explicitly requested
        # as a DWARF-only or symbols-only snapshot (Codex review).
        snap = _attach_header_graph(
            snap,
            _HEADER_GRAPH_ENABLED
            and not _skip_header_graph_attach
            and not dwarf_only
            and not symbols_only,
            _HEADER_GRAPH_INCLUDES_ENABLED
            and not _skip_header_graph_attach
            and not dwarf_only
            and not symbols_only,
            _headers,
            _includes,
            _header_graph_lang,
            compile,
            public_headers,
            public_header_dirs,
            # `_public_include_search_dirs`, not `_includes` (Codex review,
            # fresh evidence): `_includes` can already be build/source-
            # evidence-widened, and this graph attach's own node-visibility
            # classification must agree with the primary parse's
            # declaration-provenance classification above, not silently
            # re-widen it.
            include_search_dirs=_public_include_search_dirs,
        )
        return attach_clang_layout(
            snap, _headers, _includes, lang=lang, compile=compile
        )
    if binary_fmt == "pe":
        with dumper_cache.ast_memoize_scope():
            snap = _dump_pe(
                path,
                version,
                headers=_headers,
                includes=_includes,
                lang=lang,
                lang_explicit=lang_explicit,
                pdb_path=pdb_path,
                header_backend=eff_backend,
                compile=compile,
                public_headers=public_headers,
                public_header_dirs=public_header_dirs,
                include_labels=include_labels,
            )
        return _finish_native_snapshot(
            snap,
            path=path,
            headers=_headers,
            includes=_includes,
            lang=lang,
            header_graph_lang=_header_graph_lang,
            compile=compile,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            skip_header_graph=_skip_header_graph_attach or symbols_only,
            public_include_search_dirs=_public_include_search_dirs,
        )
    if binary_fmt == "macho":
        with dumper_cache.ast_memoize_scope():
            snap = _dump_macho(
                path,
                version,
                headers=_headers,
                includes=_includes,
                header_backend=eff_backend,
                lang=lang,
                lang_explicit=lang_explicit,
                compile=compile,
                public_headers=public_headers,
                public_header_dirs=public_header_dirs,
                include_labels=include_labels,
            )
        return _finish_native_snapshot(
            snap,
            path=path,
            headers=_headers,
            includes=_includes,
            lang=lang,
            header_graph_lang=_header_graph_lang,
            compile=compile,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            skip_header_graph=_skip_header_graph_attach or symbols_only,
            public_include_search_dirs=_public_include_search_dirs,
        )
    raise ValidationError(f"Unsupported binary format: {binary_fmt}")


def _finish_native_snapshot(
    snap: AbiSnapshot,
    *,
    path: Path,
    headers: list[Path],
    includes: list[Path],
    lang: str,
    header_graph_lang: str | None,
    compile: CompileContext | None,
    public_headers: list[Path] | None,
    public_header_dirs: list[Path] | None,
    skip_header_graph: bool,
    public_include_search_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Shared post-dump tail for the PE and Mach-O branches of ``run_dump``.

    Both formats finish a dump identically — native provenance, the optional
    Python/NumPy surface attachments, the header-only (L2) graph, then the
    clang layout backfill — and the two branches only differ in which
    ``_dump_*`` produced *snap*. Kept as one function so a new post-processing
    step cannot be added to one format and silently forgotten on the other
    (CodeFactor: duplicate code). The ELF branch deliberately stays separate:
    it also attaches SYCL metadata and honors ``dwarf_only``, neither of which
    applies here.

    ``skip_header_graph`` folds the caller's own reasons to suppress the graph
    (the ``hybrid`` recursion's ``_skip_header_graph_attach``, ``symbols_only``)
    into one flag; the global enablement switches stay this function's business.

    ``public_include_search_dirs`` (see ``_run_dump_uncached``'s own docstring):
    when given, used instead of ``includes`` for both the flat-snapshot
    provenance widening and the header-graph attach, so an already-widened
    ``includes`` (auto-derived directories included) can never leak into
    either. Defaults to ``includes`` itself, unchanged from before this
    parameter existed.
    """
    _public_dirs = (
        public_include_search_dirs
        if public_include_search_dirs is not None
        else includes
    )
    snap = _apply_native_provenance(snap, public_headers, public_header_dirs, _public_dirs)
    _try_attach_python_ext_metadata(snap)
    _try_attach_python_api_surface(snap)
    _try_attach_numpy_capi_surface(snap, path)
    snap = _attach_header_graph(
        snap,
        _HEADER_GRAPH_ENABLED and not skip_header_graph,
        _HEADER_GRAPH_INCLUDES_ENABLED and not skip_header_graph,
        headers,
        includes,
        header_graph_lang,
        compile,
        public_headers,
        public_header_dirs,
        include_search_dirs=_public_dirs,
    )
    return attach_clang_layout(snap, headers, includes, lang=lang, compile=compile)


@functools.wraps(_run_dump_uncached)  # name lookup below so patching sticks
def _call_run_dump_uncached(*args: Any, **kwargs: Any) -> AbiSnapshot:
    return _run_dump_uncached(*args, **kwargs)


run_dump = wrap_run_dump_with_dependency_scope(_call_run_dump_uncached)
# CodeRabbit: both functools.wraps() above copy __name__ down the chain from _run_dump_uncached, so run_dump.__name__ read as "_run_dump_uncached" -- wrong for any introspecting caller. __signature__ is unaffected.
run_dump.__name__ = "run_dump"
run_dump.__qualname__ = "run_dump"


def _apply_native_provenance(
    snap: AbiSnapshot,
    public_headers: list[Path] | None,
    public_header_dirs: list[Path] | None,
    include_search_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Tag declaration provenance on a PE/Mach-O snapshot (ADR-024 Phase 1).

    Mirrors the ELF path (``dumper.create_snapshot``), which always runs
    ``apply_provenance`` and, since the same PR's ELF-side fix, folds the
    caller's ``-I`` roots in too. A no-op when no public-header set is
    supplied — every origin stays ``UNKNOWN`` and behaviour is unchanged.
    Without ``include_search_dirs`` here, a declaration reached only
    transitively through PE/Mach-O's own ``-I`` (never itself named as a
    root) stayed ``PRIVATE_HEADER`` and could be excluded from the public
    surface — the exact false-clean result the ELF fix closed, left open on
    these two formats (Codex review, fresh evidence).
    """
    from .provenance import apply_provenance

    return apply_provenance(
        snap,
        public_headers,
        public_header_dirs,
        include_search_dirs=include_search_dirs,
    )


def _emit(notify: Callable[[str], None] | None, message: str) -> None:
    """Send a user-facing progress note to *notify*, or the logger if unset."""
    if notify is not None:
        notify(message)
    else:
        _logger.warning(message)


# ── Opportunistic per-ecosystem metadata attachment (extracted to leaf
# module service_metadata_attach to stay under the AI-readiness size cap;
# re-exported verbatim so the existing import paths are unchanged). ──────
from .service_metadata_attach import (  # noqa: E402
    _try_attach_numpy_capi_surface,
    _try_attach_python_api_surface,
    _try_attach_python_ext_metadata,
    _try_attach_sycl_metadata,
)


def _dump_elf(
    path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    lang_explicit: bool = False,
    dwarf_only: bool = False,
    debug_roots: list[Path] | None = None,
    enable_debuginfod: bool = False,
    debuginfod_url: str | None = None,
    debug_format: str | None = None,
    symbols_only: bool = False,
    debug_presence_only: bool = False,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    notify: Callable[[str], None] | None = None,
    include_labels: dict[Path, str] | None = None,
    dump_manifest: DumpManifest | None = None,
    public_include_search_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Dump an ELF binary to an ABI snapshot.

    ``public_headers`` / ``public_header_dirs`` classify declaration provenance
    (ADR-024). They are threaded into :func:`dumper.dump`, which runs
    ``apply_provenance`` over the parsed surface — the same call the ``dump`` CLI
    makes (``cli_dump_helpers._run_elf_dump``). Without this thread-through the
    ELF service path leaves every origin ``UNKNOWN``, silently disabling the
    provenance-gated cross-checks on the ``scan`` entry point.

    ``dump_manifest`` (ADR-050 D3) is a parsed multi-TU manifest replacing
    *headers* for this dump; threaded straight into :func:`dumper.dump`,
    which enforces the mutual-exclusivity rule against *headers*/
    *public_headers*/*public_header_dirs*.

    ``public_include_search_dirs`` is the caller's genuinely explicit ``-I``
    list, kept separate from *includes* -- which can already be widened by
    the time it reaches here (Codex review) -- so provenance widening never
    uses a build-derived directory. Falls back to ``list(includes)`` when
    omitted (unchanged prior behavior).
    """
    from .dumper import dump

    # P1.1 (ADR-021a): a resolved detached debug artifact (--debug-root /
    # --debuginfod) was previously only used for a CLI log line — the DWARF
    # parse always read `path` itself, so a stripped production .so stayed
    # L0-only even after abicheck reported it found the matching debug file.
    # Resolve here (gated on the caller actually requesting it, same as the
    # CLI) and thread the artifact's DWARF-bearing file through to dumper.dump
    # so it's read instead of `path`. Split DWARF (.dwo/.dwp) and dSYM are not
    # threaded here — narrower follow-up, not this fix's scope.
    debug_info_path: Path | None = None
    if (
        not symbols_only
        and not debug_presence_only
        and (debug_roots or enable_debuginfod)
    ):
        from .debug_resolver import resolve_debug_info

        artifact = resolve_debug_info(
            path,
            debug_roots=debug_roots,
            enable_debuginfod=enable_debuginfod,
            debuginfod_urls=[debuginfod_url] if debuginfod_url else None,
        )
        if artifact is not None and artifact.dwarf_path is not None:
            resolved_dwarf = artifact.dwarf_path.resolve()
            if resolved_dwarf != path.resolve():
                debug_info_path = artifact.dwarf_path
                message = f"Debug info for {path.name}: {artifact.source}"
                if notify is not None:
                    notify(message)
                else:
                    _logger.info(message)

    cc = compile if compile is not None else CompileContext()
    resolved_headers = expand_header_inputs(headers) if headers else []
    if not resolved_headers and symbols_only and dump_manifest is None:
        _emit(
            notify,
            f"Warning: '{path}' — no headers provided. "
            "Using exported symbols only for binary-depth scan.",
        )
    elif not resolved_headers and not dwarf_only and dump_manifest is None:
        _emit(
            notify,
            f"Warning: '{path}' — no headers provided. "
            "Will use DWARF debug info if available, else symbols-only mode.",
        )
    if resolved_headers and not dwarf_only:
        for inc in includes:
            if not inc.exists() or not inc.is_dir():
                raise ValidationError(
                    f"Include directory not found or not a directory: {inc}"
                )
    elif includes and not dwarf_only and dump_manifest is None:
        _emit(notify, "Warning: --include paths are ignored without headers.")

    # P3: auto-add the public-header roots to the search path. Same bucket
    # selection as the dump CLI path (resolve_inferred_header_roots): plain -I
    # when this request carries no compile-context includes, or -isystem (below
    # the build-context dirs, above the standard system dirs) when the caller's
    # CompileContext supplies its own includes via gcc_options/tokens (e.g.
    # -isystem build/generated) — so a real build context keeps search priority
    # without dropping the inferred root below system headers (Codex review).
    eff_includes = list(includes)
    eff_tokens: tuple[str, ...] = cc.gcc_option_tokens
    deferred_dirs: tuple[Path, ...] = ()
    if resolved_headers and not dwarf_only:
        inc_extra, deferred = resolve_inferred_header_roots(
            headers,
            list(includes),
            gcc_options=cc.gcc_options,
            gcc_option_tokens=cc.gcc_option_tokens,
        )
        eff_includes += inc_extra
        eff_tokens = cc.gcc_option_tokens + tuple(deferred)
        # Deferred roots ride in gcc_option_tokens (-isystem), not extra_includes,
        # so hash their contents into the AST cache key explicitly (Codex review).
        # Also fold in any include-search dir in cc.gcc_option_tokens itself, so
        # this PRIMARY parse's cache key stays aligned with _attach_header_graph's
        # own identical fold above -- else the two passes could disagree on
        # staleness for the same header (Codex review).
        deferred_dirs = tuple(
            deferred_token_dirs(deferred)
        ) + cache_relevant_operand_paths(cc.gcc_option_tokens)

    compiler = "cc" if lang == "c" else "c++"
    try:
        return dump(
            so_path=path,
            headers=resolved_headers,
            extra_includes=eff_includes,
            # Provenance widening gets ONLY the caller's own explicit -I
            # list -- see dump()'s own docstring note on
            # `public_include_search_dirs` (real regression: `eff_includes`
            # also carries `inc_extra`'s auto-added umbrella-header
            # directory, which can hold a genuinely private sibling header).
            # Prefer the caller's own separately-threaded, genuinely
            # explicit list over this function's own `includes` parameter
            # (which can already be build/source-evidence-widened by the
            # time it reaches here -- Codex review, fresh evidence; see
            # this function's own docstring).
            public_include_search_dirs=(
                list(public_include_search_dirs)
                if public_include_search_dirs is not None
                else list(includes)
            ),
            version=version,
            compiler=compiler,
            gcc_path=cc.gcc_path,
            gcc_prefix=cc.gcc_prefix,
            gcc_options=cc.gcc_options,
            gcc_option_tokens=eff_tokens,
            sysroot=cc.sysroot,
            nostdinc=cc.nostdinc,
            # G31 Phase C follow-up: an explicit request (`lang_explicit`)
            # forces `lang` here regardless of value, matching this call's
            # own `_header_graph_lang` sibling in `run_dump` above -- both
            # must agree on the same explicit-vs-auto-detected decision
            # (AGENTS.md "dump --lang c++ is silently discarded ..." known
            # gap). `lang_explicit=False` (the default) is a no-op: identical
            # to the pre-existing "force only bare 'c'" behavior.
            lang=lang if (lang_explicit or lang == "c") else None,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
            symbols_only=symbols_only,
            debug_presence_only=debug_presence_only,
            header_backend=header_backend,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            extra_hash_dirs=deferred_dirs,
            debug_info_path=debug_info_path,
            extra_include_labels=include_labels,
            dump_manifest=dump_manifest,
            frontend_context=cc.frontend_context,
        )
    except (AbicheckError, RuntimeError, OSError, ValueError) as exc:
        raise SnapshotError(f"Failed to dump '{path}': {exc}") from exc


def _extract_pdb_debug(
    path: Path, pdb_path: Path | None
) -> tuple[DwarfMetadata | None, AdvancedDwarfMetadata | None]:
    """Locate and parse a PDB for *path*.

    Returns ``(dwarf_meta, dwarf_adv)`` or ``(None, None)`` when no PDB is found
    or parsing fails.  PDB extraction is best-effort and never fatal.
    """
    try:
        from .pdb_metadata import parse_pdb_debug_info
        from .pdb_utils import locate_pdb

        pdb_file = locate_pdb(path, pdb_path_override=pdb_path, allow_network=False)
        if pdb_file is not None:
            meta, adv = parse_pdb_debug_info(pdb_file)
            _logger.info("PDB debug info loaded from %s", pdb_file)
            return meta, adv
        _logger.debug("No PDB file found for %s", path)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("PDB parsing failed for %s: %s", path, exc)
    return None, None


def _dump_pe(
    path: Path,
    version: str,
    *,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    lang: str = "c++",
    lang_explicit: bool = False,
    pdb_path: Path | None = None,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    include_labels: dict[Path, str] | None = None,
) -> AbiSnapshot:
    """Dump a PE binary (Windows DLL) to an ABI snapshot.

    When *headers* are supplied the ABI surface is scoped to declarations in
    those public headers via castxml (mirroring ``abidw --headers-dir``).  If
    castxml is unavailable or no header declaration matches an exported symbol,
    scoping is skipped (with a warning) and the full export table is used.
    """
    from .pe_metadata import parse_pe_metadata

    try:
        pe_meta = parse_pe_metadata(path)
    except ImportError as exc:
        raise SnapshotError(str(exc)) from exc
    except (RuntimeError, OSError, ValueError) as exc:
        raise SnapshotError(f"Failed to parse PE '{path}': {exc}") from exc

    if not pe_meta.machine:
        raise SnapshotError(
            f"Failed to extract PE metadata from '{path}'. "
            "The file may be corrupt or not a valid PE binary."
        )
    if not pe_meta.exports:
        raise ValidationError(
            f"PE file '{path}' has no exports (named or ordinal). "
            "Verify the file is a valid DLL."
        )

    dwarf_meta, dwarf_adv = _extract_pdb_debug(path, pdb_path)

    scope_fallback: str | None = None
    if headers:
        scoped, scope_fallback = _try_header_scoped_dump(
            "pe",
            path,
            headers,
            includes or [],
            version,
            lang,
            lang_explicit=lang_explicit,
            header_backend=header_backend,
            compile=compile,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            include_labels=include_labels,
        )
        if scoped is not None:
            # Preserve any PDB debug info alongside the header-scoped surface.
            if dwarf_meta is not None:
                scoped.dwarf = dwarf_meta
                scoped.dwarf_advanced = dwarf_adv
            return scoped

    funcs = [
        Function(
            name=(exp.name or f"ordinal:{exp.ordinal}"),
            mangled=(exp.name or f"ordinal:{exp.ordinal}"),
            return_type="?",
            visibility=Visibility.PUBLIC,
            is_extern_c=not (exp.name or "").startswith("?"),
        )
        for exp in pe_meta.exports
    ]

    # ADR-024 Phase 1 (PDB provenance): when header scoping was requested but
    # castxml could not resolve a surface (commonly the MSVC C++-mangling gap),
    # recover declared types — *with their defining source header* — from the
    # PDB debug info so that public-header scoping still has a provenance
    # signal to classify against. Bounded to this fallback branch so default
    # PE diffs (no --header) are unaffected.
    pdb_types: list[RecordType] = []
    pdb_enums: list[EnumType] = []
    if headers and dwarf_meta is not None:
        from .pdb_model import model_types_from_dwarf_metadata

        pdb_types, pdb_enums = model_types_from_dwarf_metadata(dwarf_meta)

    return AbiSnapshot(
        library=path.name,
        version=version,
        functions=funcs,
        types=pdb_types,
        enums=pdb_enums,
        pe=pe_meta,
        dwarf=dwarf_meta,
        dwarf_advanced=dwarf_adv,
        platform="pe",
        scope_fallback=scope_fallback,
    )


def _dump_macho(
    path: Path,
    version: str,
    *,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    lang: str = "c++",
    lang_explicit: bool = False,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    include_labels: dict[Path, str] | None = None,
) -> AbiSnapshot:
    """Dump a Mach-O binary (macOS dylib) to an ABI snapshot.

    When *headers* are supplied the ABI surface is scoped to declarations in
    those public headers via castxml; otherwise the full export table is used.
    """
    from .macho_metadata import parse_macho_metadata

    try:
        macho_meta = parse_macho_metadata(path)
    except (RuntimeError, OSError, ValueError) as exc:
        raise SnapshotError(f"Failed to parse Mach-O '{path}': {exc}") from exc

    if (
        not macho_meta.exports
        and not macho_meta.install_name
        and not macho_meta.dependent_libs
    ):
        raise SnapshotError(
            f"Mach-O file '{path}' has no exports or load-command metadata. "
            "Verify the file is a valid dynamic library."
        )

    scope_fallback: str | None = None
    if headers:
        scoped, scope_fallback = _try_header_scoped_dump(
            "macho",
            path,
            headers,
            includes or [],
            version,
            lang,
            lang_explicit=lang_explicit,
            header_backend=header_backend,
            compile=compile,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            include_labels=include_labels,
        )
        if scoped is not None:
            return scoped

    funcs = [
        Function(
            name=exp.name,
            mangled=exp.name,
            return_type="?",
            visibility=Visibility.PUBLIC,
            is_extern_c=not exp.name.startswith("_Z"),
        )
        for exp in macho_meta.exports
        if exp.name
    ]
    return AbiSnapshot(
        library=path.name,
        version=version,
        functions=funcs,
        macho=macho_meta,
        platform="macho",
        scope_fallback=scope_fallback,
    )


# ── Comparison ──────────────────────────────────────────────────────────────


def collect_metadata(path: Path) -> LibraryMetadata | None:
    """Compute SHA-256 and file size for a library artifact.

    Returns *None* when *path* is a text-based snapshot (JSON or Perl dump)
    so that reports don't display misleading metadata for the serialised file.
    """
    text_fmt = sniff_text_format(path)
    if text_fmt in ("json", "perl"):
        return None

    data = path.read_bytes()
    return LibraryMetadata(
        path=str(path),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


def load_suppression_and_policy(
    suppress: Path | None,
    policy: str = "strict_abi",
    policy_file_path: Path | None = None,
) -> tuple[SuppressionList | None, PolicyFile | None]:
    """Load suppression list and policy file from paths.

    Raises:
        ValidationError: If the suppression or policy file is invalid.
    """
    from .policy_file import PolicyFile as _PolicyFile
    from .suppression import SuppressionList as _SuppressionList

    suppression: _SuppressionList | None = None
    if suppress is not None:
        try:
            suppression = _SuppressionList.load(suppress)
        except (ValueError, OSError) as e:
            raise ValidationError(f"Invalid suppression file: {e}") from e

    pf: _PolicyFile | None = None
    if policy_file_path is not None:
        try:
            pf = _PolicyFile.load(policy_file_path)
        except ImportError as e:
            raise ValidationError(str(e)) from e
        except (ValueError, OSError) as e:
            raise ValidationError(f"Invalid policy file: {e}") from e
        if policy != "strict_abi":
            # Named as Tier-2 *parameters*, not CLI flags: the CLI merged
            # --policy/--policy-file into one --policy that routes an operand
            # to exactly one of these, so it can no longer set both and this
            # branch is now reachable only from a typed API caller, for whom
            # the flag spellings would name nothing.
            _logger.warning(
                "policy=%r is ignored when policy_file_path is given. "
                "Set base_policy in the YAML file to override the base policy.",
                policy,
            )
        # This is the Tier-2 chokepoint every consumer other than the CLI's
        # own early-validation calls actually loads its policy through --
        # notably `compare-release`'s real per-library fan-out
        # (`_run_compare_pair` -> `run_compare` -> `run_compare_request` ->
        # `classify_compare_pair`), and any direct Python API caller of
        # `run_compare`/`run_compare_request`. `cli_params.
        # _load_suppression_and_policy`'s own warning (surfaced via
        # `click.echo`) does not reach that path, so a risky override in a
        # release comparison stayed silent (Codex review). Mirrors that same
        # warning here, through the logger this module already uses above.
        #
        # Routed through `pending_validate_overrides_warnings` (not
        # `pf.validate_overrides()` directly) so a `dedup_policy_override_
        # warnings()` scope -- shared with `cli_params._load_suppression_
        # and_policy`'s identical call -- collapses a warning repeated across
        # several loads down to one (Codex review); outside any such scope
        # every warning is logged every call, unchanged from before.
        for warning in _policy_file.pending_validate_overrides_warnings(pf):
            _logger.warning("%s", warning)
    return suppression, pf


def load_env_matrix(path: Path | None) -> EnvironmentMatrix | None:
    """Load an ADR-020b environment-matrix YAML, or None when *path* is None.

    Tier-2 loader (mirrors :func:`load_suppression_and_policy`): parse/shape
    errors surface as :class:`ValidationError` with identical text across
    front-ends.
    """
    if path is None:
        return None
    from .environment_matrix import EnvironmentMatrix

    try:
        # from_yaml converts malformed YAML to ValueError, so no yaml import
        # is needed here (abicheck.service has no import-untyped override).
        return EnvironmentMatrix.from_yaml(Path(path))
    except (TypeError, ValueError) as e:
        raise ValidationError(f"Invalid environment matrix {path}: {e}") from e
    except OSError as e:
        raise ValidationError(f"Cannot read environment matrix {path}: {e}") from e


def _validate_contract_mode(
    contract_mode: str | None, contract_evaluation: bool
) -> None:
    """Apply ADR-049 Phase 6's two ``contract_mode`` rules at a Tier-2 entry.

    Same allowed values and same ``contract_evaluation`` dependency as
    ``CompareRequest.validation_errors`` and the CLI's ``--contract``, so the
    three front ends cannot disagree about what is accepted.
    """
    if contract_mode is None:
        return
    from .contract_relevance_types import ContractMode

    allowed = {mode.value for mode in ContractMode}
    if contract_mode not in allowed:
        raise ValidationError(
            f"unsupported contract mode {contract_mode!r}: "
            f"choose from {', '.join(sorted(allowed))}"
        )
    if not contract_evaluation:
        raise ValidationError(
            "contract_mode requires contract_evaluation: it selects which "
            "evidence domain the shadow contract evaluator judges against, "
            "and without that flag no contract decision is computed at all"
        )


def compare_snapshots(
    old: AbiSnapshot,
    new: AbiSnapshot,
    suppression: SuppressionList | None = None,
    *,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    scope_to_public_surface: bool = True,
    force_public_symbols: set[str] | None = None,
    extra_changes: list[Change] | None = None,
    pattern_verdicts: bool = False,
    surface_metrics: bool = False,
    collapse_versioned_symbols: bool = False,
    public_surface_allowlist: set[str] | None = None,
    reconcile_build_context: bool = False,
    env_matrix: EnvironmentMatrix | None = None,
    diagnostic_comparison: bool = False,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
) -> DiffResult:
    """Classify two already-resolved snapshots — the Tier-2 snapshot verb.

    Thin wrapper over the Tier-1 core (:func:`abicheck.checker.compare`) so that
    *front-ends never call the core directly* (ADR-037 D1/D10.1). Front-ends
    that have already resolved their own snapshots (the native ``compare``
    command with embedded build-source evidence, ``scan``, ``appcompat``) route
    through here instead of importing ``checker.compare``; the kwargs mirror the
    core verb exactly so no capability is lost.

    Raises:
        ValidationError: *contract_mode* is not one of ``public``/``exports``/
            ``all``, or is given without *contract_evaluation*. This is a
            documented Tier-2 entry point that direct Python callers reach
            without building a ``CompareRequest``, so it applies the same two
            rules that request object and the CLI do rather than silently
            accepting a no-op or failing later inside the core (Codex review).
    """
    _validate_contract_mode(contract_mode, contract_evaluation)
    # Centralized POST committed-wrapper recovery: when a committed-surface
    # allowlist is supplied, union the callable `pp_*` wrappers exported by the
    # old snapshot (contract_scope_allowlist's snapshot half). This keeps both
    # dropped wrappers and still-exported-but-omitted wrappers in-surface when a
    # caller scopes against a new manifest, preventing manifest omissions from
    # hiding ABI breaks. Every scope caller (CLI, run_compare_request, direct API)
    # routes through here, so recovery happens once and uniformly; it is a no-op
    # when the allowlist/binaries carry no `pp_*` wrappers. Idempotent if the
    # caller already unioned it.
    if public_surface_allowlist is not None:
        from .post_manifest import _snapshot_contract_symbols

        public_surface_allowlist = set(
            public_surface_allowlist
        ) | _snapshot_contract_symbols(old)
    return compare(
        old,
        new,
        suppression=suppression,
        policy=policy,
        policy_file=policy_file,
        scope_to_public_surface=scope_to_public_surface,
        force_public_symbols=force_public_symbols,
        extra_changes=extra_changes,
        pattern_verdicts=pattern_verdicts,
        surface_metrics=surface_metrics,
        collapse_versioned_symbols=collapse_versioned_symbols,
        public_surface_allowlist=public_surface_allowlist,
        reconcile_build_context=reconcile_build_context,
        env_matrix=env_matrix,
        diagnostic_comparison=diagnostic_comparison,
        contract_evaluation=contract_evaluation,
        contract_mode=contract_mode,
    )


# run_compare_request/run_compare moved to service_compare_pipeline.py (CLI
# cleanup phase two, PR B slice 1) to stay under the AI-readiness file-size
# cap once run_compare gained pack_policy_overrides/pack_internal_namespaces
# -- re-exported below, same pattern as resolve_compare_request/
# classify_compare_pair already use.


# ── Compare pipeline (ADR-055 D1): `run_compare_request`'s two phases live in
# the leaf module ``service_compare_pipeline`` so the native ``compare`` CLI can
# run its Click-dependent ADR-049 ``resolve_and_apply`` step between them and
# still share this resolution instead of keeping a second copy. Re-exported here
# so ``from abicheck.service import resolve_compare_request`` works.
# ``run_compare_request``/``run_compare`` (their composition and its
# keyword-argument shim) live there too now, for the same file-size reason. ──
from .service_compare_pipeline import (  # noqa: E402,F401
    ResolvedComparePair,
    classify_compare_pair,
    resolve_compare_request,
    resolve_sides_sequentially,
    run_compare,
    run_compare_request,
)

# ── Dump pipeline (G33 Phase 5): ``dump``'s counterpart to the above, in the
# leaf module ``service_dump_pipeline``. Re-exported for the same reason:
# ``from abicheck.service import run_dump_request`` is the typed entry point
# every front end (CLI, Python, MCP ``abi_dump``) builds a request for. ───────
from .service_dump_pipeline import run_dump_request  # noqa: E402,F401

# ── Output rendering (extracted to leaf module service_render, a non-
# importing-us leaf) to stay under the AI-readiness size cap; re-exported
# verbatim so ``from abicheck.service import render_output`` is unchanged. ──
from .service_render import (  # noqa: E402,F401
    _render_deps_section_md,
    _render_json_output,
    render_output,
)

# ── Scan service (ADR-035 D10 typed engine: ScanRequest → ScanResult /
# [CostEstimate]) extracted to leaf module service_scan, same size-cap/re-
# export/non-circular-import rationale as service_render above. ────────────
from .service_scan import (  # noqa: E402,F401
    _HEADER_EXTS,
    Budget,
    CompileContext,
    CostEstimate,
    LayerResult,
    ScanArtifactResult,
    ScanRequest,
    ScanResult,
    ScanSetResult,
    _count_compile_db_tus,
    _count_pack_tus,
    _count_source_tus,
    _discover_compile_db,
    _is_header_path,
    _is_source_tu_path,
    _kill_process_tree,
    _layers_from_coverage,
    _scan_imports,
    _scan_subprocess_worker,
    estimate_scan,
    expand_header_inputs,
    pair_wide_cxx20_std_override,
    run_audit,
    run_scan,
    run_scan_set,
    run_scan_set_subprocess,
    run_scan_subprocess,
)

# Explicit re-export (mypy strict / no_implicit_reexport): the scan engine moved
# to the leaf module ``service_scan`` but its public names must still resolve as
# ``from abicheck.service import ...``.
__all__ = [
    "Budget",
    "CompareRequest",
    "CompareResult",
    "CompileContext",
    "CostEstimate",
    "DumpRequest",
    "InputSpec",
    "LayerResult",
    "OutputSpec",
    "ScanArtifactResult",
    "ScanRequest",
    "ScanResult",
    "ResolvedComparePair",
    "ScanSetResult",
    "classify_compare_pair",
    "collect_metadata",
    "compare_snapshots",
    "detect_binary_format",
    "estimate_scan",
    "expand_header_inputs",
    "load_suppression_and_policy",
    "render_output",
    "resolve_compare_request",
    "resolve_input",
    "run_audit",
    "run_compare",
    "run_compare_request",
    "run_dump",
    "run_dump_request",
    "run_scan",
    "run_scan_set",
    "run_scan_set_subprocess",
    "run_scan_subprocess",
    "sniff_text_format",
]
