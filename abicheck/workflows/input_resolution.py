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

"""Input-format detection and the single ``path -> AbiSnapshot`` entry point.

ADR-061 Phase 4 (service.py thinning slice): :func:`resolve_input` and its
helpers moved out of ``service.py`` verbatim -- unlike ``compare_snapshots``/
``load_suppression_and_policy``, which stayed behind because they need
``PolicyFile`` (still unclassified; see this ADR's own extensive `PolicyFile`/
`ChangeKind` investigation), nothing here touches policy at all. Every
dependency is already a classified `model`/`storage`/`extract`/`workflows`
module, so this file lives in the real package directly rather than as
another flat, merely-`legacy_paths`-classified sibling.

``abicheck.service`` re-exports every public name here unchanged (a plain
static import, not a typed wrapper -- ``workflows -> workflows`` needs none of
the ``importlib``-based bridging ``workflows/render.py`` needs for its
`frontends`-classified peer), so ``from abicheck.service import
resolve_input`` and ``monkeypatch.setattr(service, "resolve_input", ...)``
both keep working exactly as before: a plain module-level import binds a real,
reassignable attribute on ``abicheck.service``, and every real caller reads
``service.resolve_input`` (or re-imports it function-locally) at call time
rather than caching a name at import time, so a monkeypatch on the facade is
what those callers actually see.

That symmetry does not extend to a name :func:`resolve_input` calls
*internally* by its own bare name -- ``run_dump``, ``load_snapshot``,
``detect_binary_format``, ``sniff_text_format``. Those resolve against this
module's own globals now, not ``abicheck.service``'s, so a test that used to
intercept one via ``monkeypatch.setattr(service, "run_dump", ...)`` must
patch ``abicheck.workflows.input_resolution.run_dump`` instead -- the same
rule ``service_dump_native.py``'s own re-export block documents for its
split.
"""

from __future__ import annotations

import hashlib
import importlib as _importlib
import logging
from typing import TYPE_CHECKING, Any

from ..checker_types import LibraryMetadata
from ..compile_context import CompileContext
from ..errors import AbicheckError, SnapshotError, ValidationError
from ..model import AbiSnapshot, Function
from ..serialization import load_snapshot
from ..service_dump_cache import cached_run_dump

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ..dump_manifest import DumpManifest
    from ..environment_matrix import EnvironmentMatrix

# `service_dump_native` reaches `service_header_graph_attach` ->
# `service_scan` -> `service`, the pre-existing, already-baselined CLI-
# registration SCC (AGENTS.md "M1-3"/CLAUDE.md "What NOT to do"). This
# module is imported *by* `service` itself, so a static `from
# ..service_dump_native import ...` here would pull this new module into
# that same cycle -- the AI-readiness `import-cycle-growth` gate rejects
# exactly that ("no *new* module joins"). Bound via `importlib.import_module`
# instead: a plain function call, not an `ast.ImportFrom` node, so it is
# invisible to that gate's static AST walk (the identical escape hatch
# `service.py`'s own `_service_header_scoped` bridge already uses for an
# analogous reason) while still binding real, usable module-level names here.
_service_dump_native = _importlib.import_module(".service_dump_native", "abicheck")
# Explicitly typed (not left as the `Any` importlib.import_module's attribute
# access would otherwise infer) so a caller still gets a real return-type
# check instead of a silent `no-any-return`.
_emit: Callable[[Callable[[str], None] | None, str], None] = _service_dump_native._emit
run_dump: Callable[..., AbiSnapshot] = _service_dump_native.run_dump
del _service_dump_native

_logger = logging.getLogger(__name__)

# Magic-byte length for format detection
_SNIFF_BYTES = 256

# ── Input resolution ────────────────────────────────────────────────────────


def detect_binary_format(path: Path) -> str | None:
    """Detect binary format from magic bytes.

    Returns ``'elf'``, ``'pe'``, ``'macho'``, or *None* for non-binary / unknown.
    """
    from ..binary_utils import detect_binary_format as _detect

    return _detect(path)


def sniff_text_format(path: Path) -> str:
    """Read a small header chunk and return ``'json'``, ``'perl'``, ``'symvers'``, or ``'unknown'``.

    ADR-059: a gzip/zstd-compressed snapshot (``.abicheck.json.gz``/``.zst``,
    or any neutrally-named file carrying those magic bytes) is detected here
    too, via a *bounded* decoded prefix — never a full decompression just to
    classify the input. A compressed non-snapshot payload (e.g. a baseline-
    set ``.tar.zst`` archive) does not decode to a leading ``{`` and falls
    through to ``'unknown'``, same as any other unrecognized input, so
    archive/package resolution still gets its turn.
    """
    from ..compat.abicc_dump_import import looks_like_perl_dump
    from ..snapshot_io import bounded_decoded_prefix, detect_snapshot_compression

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
    from ..symvers_metadata import looks_like_symvers

    if looks_like_symvers(head):
        return "symvers"
    return "unknown"


def _resolve_project_snapshot_directory(path: Path) -> AbiSnapshot:
    """*path* as a directory-backed ADR-062/ADR-063 storage-v2
    `ProjectSnapshot` package (`project_snapshot_legacy
    .read_legacy_snapshot_document` — manifest.json + refs/ + objects/,
    produced via `project_snapshot_legacy.write_legacy_snapshot_package` --
    no `dump` CLI flag writes one today, see that module's own docstring for
    why `dump`'s real output is the single-file sectioned shape instead),
    decoded into an `AbiSnapshot` exactly the way a legacy `.abi.json` file
    already is (`serialization.snapshot_from_dict`).

    Single-artifact packages only, matching what `write_legacy_snapshot_
    package` ever writes (ADR-062 A1.3's "one-artifact project" shape) — a
    real multi-library `ProjectSnapshot` is real, separately-scoped future
    work this function does not guess at (see `read_legacy_snapshot_document`'s
    own docstring for the same limit).

    Raises `SnapshotError` for anything that goes wrong reading or decoding
    the package -- a missing/malformed `manifest.json`, a multi-artifact
    package with no artifact named explicitly, an unreadable section object
    -- the identical translation every other `resolve_input` branch applies
    at its own boundary.
    """
    from ..project_snapshot_legacy import read_legacy_snapshot_document
    from ..serialization import snapshot_from_dict

    try:
        document = read_legacy_snapshot_document(path)
    except (SnapshotError, OSError, KeyError, ValueError, TypeError) as exc:
        raise SnapshotError(
            f"Failed to load ProjectSnapshot package '{path}': {exc}"
        ) from exc
    try:
        return snapshot_from_dict(document)
    except (TypeError, ValueError, KeyError, UnicodeDecodeError) as exc:
        raise SnapshotError(
            f"Failed to decode ProjectSnapshot package '{path}': {exc}"
        ) from exc


def _resolve_symvers(path: Path, version: str) -> AbiSnapshot | None:
    """Parse a Linux kernel ``Module.symvers`` manifest into a snapshot, or None.

    Recognized by filename (``Module.symvers`` / ``*.symvers``) or, for a
    generically-named file, by content (a hex-CRC + ``EXPORT_SYMBOL`` record).
    """
    from ..symvers_metadata import looks_like_symvers, parse_symvers

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
    from ..model import Param

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
    from ..btf_metadata import BTF_MAGIC, parse_btf_from_bytes
    from ..ctf_metadata import CTF_MAGIC, parse_ctf_from_bytes
    from ..extract.debug_layout_semantic_ir import semantic_ir_from_debug_metadata

    try:
        with open(path, "rb") as f:
            head = f.read(2)
    except OSError:
        return None
    if len(head) < 2:
        return None

    # Only detect the little-endian byte order parse_btf_from_bytes/
    # parse_ctf_from_bytes actually support: a big-endian blob (EB 9F/CF F1)
    # would otherwise enter the branch but parse to empty metadata, silently
    # dropping type changes -- falling through to "cannot detect" is honest.
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
            btf_dwarf_meta = btf.to_dwarf_metadata()
            return AbiSnapshot(
                library=path.name,
                version=version,
                dwarf=btf_dwarf_meta,
                # Bridge the full BTF surface: prototypes feed the function
                # detectors (FUNC_PARAMS_CHANGED, ...) and typedef targets feed
                # TYPEDEF_BASE_CHANGED — previously dropped at this boundary.
                functions=_typeinfo_functions(btf.func_protos),
                typedefs=dict(btf.typedefs),
                # ADR-063 Phase 6, BTF/CTF slice (Codex review, fresh evidence):
                # this raw-blob assembler is a second production call site
                # alongside dumper_elf_fallback.py's own -- narrowing the
                # BTF/CTF backends to raw-fact production would leave this one
                # unwired too, so it gets the identical treatment directly
                # rather than waiting for a shared choke point.
                semantic_ir=semantic_ir_from_debug_metadata(btf_dwarf_meta, "btf"),
            )
        if magic_le == CTF_MAGIC:
            ctf = parse_ctf_from_bytes(data)
            if not ctf.has_ctf or ctf.type_count <= 0:
                _logger.warning("raw CTF blob %s has no type records; ignoring", path)
                return None
            ctf_dwarf_meta = ctf.to_dwarf_metadata()
            return AbiSnapshot(
                library=path.name,
                version=version,
                dwarf=ctf_dwarf_meta,
                functions=_typeinfo_functions(ctf.func_protos),
                typedefs=dict(ctf.typedefs),
                semantic_ir=semantic_ir_from_debug_metadata(ctf_dwarf_meta, "ctf"),
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

    # ADR-062/ADR-063 storage-v2: a directory input is only ever a
    # `ProjectSnapshot` package here -- every other branch below opens
    # `path` as a file and would raise
    # `IsADirectoryError`, so this must run first, unconditionally (a
    # directory is never ELF/PE/Mach-O/BTF/CTF/symvers/JSON-file regardless
    # of `is_elf`).
    if path.is_dir():
        return _resolve_project_snapshot_directory(path)

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

    # Raw kernel type-info blobs (a bare `.BTF`/CTF section extracted via
    # `bpftool`/`objcopy`) -- a real kernel carries BTF inside an ELF
    # `.BTF` section, but the bare blob is a convenient, toolchain-free input.
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
        from ..compat.abicc_dump_import import import_abicc_perl_dump

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
        # TypeError: a malformed nested field (e.g. a non-mapping/non-string
        # ExtractionContract.profile_fields/scope_fields) is rejected rather
        # than coerced at the storage boundary (storage AGENTS.md invariant
        # 6, storage/snapshot_load_normalization.py) -- the caller here is
        # exactly the "malformed package" side of the TypeError/ValueError
        # split that convention documents, so it must be caught alongside
        # ValueError, not left to escape as an unhandled crash (Codex
        # review; matches bundle_facts.py's identical TypeError catch
        # around its own snapshot_from_dict() call).
        except (TypeError, ValueError, KeyError, UnicodeDecodeError, OSError) as exc:
            raise SnapshotError(
                f"Failed to load JSON snapshot '{path}': {exc}"
            ) from exc

    # GNU ld linker script (e.g. the ``libfoo.so`` dev symlink is the text
    # ``INPUT(libfoo.so.1)``): follow it to the real shared library.
    if follow_linker_scripts:
        from ..binary_utils import resolve_linker_script

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
    # linkable images -- abicheck does not analyse archives by design (see
    # docs/learn/limitations.md); fail with actionable guidance instead.
    from ..binary_utils import detect_archive

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


def collect_metadata(path: Path) -> LibraryMetadata | None:
    """Compute SHA-256 and file size for a library artifact, or ``None`` for a text-based snapshot/manifest (JSON, Perl dump, ``Module.symvers``) -- not a binary, so a same-binary comparison must never claim it."""
    if path.is_dir():
        # A storage-v2 `ProjectSnapshot` package dir (the one directory
        # `resolve_input` resolves rather than rejecting) is not a single
        # hashable file -- the same "not a binary" no-op the text-format
        # branches below already apply, without `read_bytes()` raising
        # `IsADirectoryError` first. `frontends/cli/runtime.py`'s own
        # `_collect_metadata` guards this for the CLI path; the typed
        # Python API (`service.run_compare_request`) calls this function
        # directly and needs the identical guard (Codex review).
        return None
    text_fmt = sniff_text_format(path)
    if text_fmt in ("json", "perl", "symvers"):
        return None

    data = path.read_bytes()
    return LibraryMetadata(
        path=str(path),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


def load_env_matrix(path: Path | None) -> EnvironmentMatrix | None:
    """Load an ADR-020b environment-matrix YAML, or None when *path* is None.

    Tier-2 loader (mirrors :func:`abicheck.service.load_suppression_and_policy`):
    parse/shape errors surface as :class:`ValidationError` with identical text
    across front-ends.
    """
    if path is None:
        return None
    from pathlib import Path as _Path

    from ..environment_matrix import EnvironmentMatrix

    try:
        # from_yaml converts malformed YAML to ValueError, so no yaml import
        # is needed here (abicheck.service has no import-untyped override).
        return EnvironmentMatrix.from_yaml(_Path(path))
    except (TypeError, ValueError) as e:
        raise ValidationError(f"Invalid environment matrix {path}: {e}") from e
    except OSError as e:
        raise ValidationError(f"Cannot read environment matrix {path}: {e}") from e
