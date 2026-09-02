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

"""Input resolution & native-dump dispatch for the CLI.

This is the leaf module of the ``compare`` / ``dump`` input pipeline: given a
path, it detects the format (ELF / PE / Mach-O / JSON snapshot / ABICC Perl
dump / GNU ld linker script), follows linker scripts, dispatches native dumps
to the per-format builders, and loads or builds the resulting
:class:`~abicheck.model.AbiSnapshot`.

It is imported (and re-exported) by :mod:`abicheck.cli`; it deliberately does
**not** import ``cli`` so the dependency runs one way (``cli`` → ``cli_resolve``)
and stays cycle-free. Errors surface as ``click`` exceptions because every
caller is a CLI entry point — the parallel, framework-free contract lives in
:func:`abicheck.service.resolve_input` (which raises ``SnapshotError`` /
``ValidationError`` instead).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import click

from .errors import SnapshotError
from .workflows.extraction import PRUNED_HEADER_DIR_SEGMENTS, iter_directory_headers

if TYPE_CHECKING:
    from pathlib import Path

    from .model import AbiSnapshot
    from .service_scan import CompileContext
    from .workflows.extraction import DumpManifest


def _click_notify(message: str) -> None:
    """Emit a service-layer progress note to stderr via click.

    Passed as the ``notify`` callback to :func:`abicheck.service.resolve_input` /
    :func:`abicheck.service.run_dump` so their user-facing notes (linker-script
    following, "no headers provided", "--include ignored") reach the CLI's stderr
    exactly as they did when this logic lived in the CLI.
    """
    click.echo(message, err=True)


# Number of bytes to read when sniffing file format (covers ELF magic + JSON/Perl head)
_SNIFF_BYTES = 256


def _expand_header_inputs(inputs: list[Path]) -> list[Path]:
    """Expand header inputs where each item can be a file or a directory.

    Directories are scanned recursively for known header extensions, via the same
    shared walker the ``scan``/service path uses (``header_utils`` —
    canonical :data:`~abicheck.header_utils.HEADER_SUFFIXES`, pruned-dir walk) so
    the two front-ends never disagree on what counts as a header.
    """
    out: list[Path] = []
    for p in inputs:
        if not p.exists():
            raise click.ClickException(f"Header file not found or not a file: {p}")
        if p.is_file():
            out.append(p)
            continue
        if p.is_dir():
            found = iter_directory_headers(p, PRUNED_HEADER_DIR_SEGMENTS)
            if not found:
                raise click.ClickException(
                    f"Header directory contains no supported header files: {p}"
                )
            out.extend(found)
            continue
        raise click.ClickException(f"Header path is neither file nor directory: {p}")

    # Deduplicate while preserving deterministic order
    seen: set[str] = set()
    deduped: list[Path] = []
    for h in out:
        k = str(h.resolve())
        if k in seen:
            continue
        seen.add(k)
        deduped.append(h)
    return deduped


def _sniff_text_format(path: Path) -> str:
    """Read a small header chunk and return 'json', 'perl', 'symvers', or 'unknown'. ADR-059: a gzip/zstd-compressed snapshot is recognized via a bounded decoded prefix, mirroring ``service.sniff_text_format`` (kept as a separate copy here rather than importing that one, matching this module's existing "no cross-import for this exact helper" shape)."""
    from .workflows.storage import bounded_decoded_prefix, detect_snapshot_compression

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
    from .workflows.extraction import looks_like_perl_dump, looks_like_symvers

    # Check Perl dump BEFORE JSON — a Perl dump can start with $VAR1 = {
    # which would incorrectly match the JSON heuristic after the '{'
    if looks_like_perl_dump(head):
        return "perl"
    if head.startswith("{"):
        return "json"
    return "symvers" if looks_like_symvers(head) else "unknown"


def _detect_binary_format(path: Path) -> str | None:
    """Detect binary format from magic bytes.

    Returns 'elf', 'pe', 'macho', or None for non-binary / unknown.
    """
    from .workflows.extraction import detect_binary_format

    return detect_binary_format(path)


def _resolve_linker_script(path: Path) -> tuple[Path | None, bool]:
    """Resolve a GNU ld linker script to the shared library it points at.

    Returns ``(resolved_path, is_linker_script)``. ``is_linker_script`` is True
    when *path* looks like a GNU ld script (so callers can emit a targeted hint
    even when no target file could be located); ``resolved_path`` is the first
    ``INPUT()``/``GROUP()`` member that exists next to the script, or *None*.
    """
    from .workflows.extraction import resolve_linker_script

    return resolve_linker_script(path)


def _maybe_follow_linker_script(path: Path) -> Path:
    """Return the linker-script target if *path* is a resolvable GNU ld script.

    Emits a one-line note when it follows a script; otherwise returns *path*
    unchanged. Used by entry points that dispatch on binary format directly
    (e.g. ``dump``) rather than through :func:`_resolve_input`.
    """
    target, is_ld = _resolve_linker_script(path)
    if is_ld and target is not None and target.resolve() != path.resolve():
        click.echo(
            f"Note: '{path}' is a GNU ld linker script; following its "
            f"INPUT()/GROUP() directive to '{target}'.",
            err=True,
        )
        return target
    return path


def _normalize_binary_input(path: Path) -> tuple[Path, str | None]:
    """Detect a binary input's format, following GNU ld linker scripts.

    Returns ``(resolved_path, format)``. When *path* is a linker script that
    resolves to a real shared library, the resolved path and *its* format are
    returned so downstream metadata collection and dependency analysis operate
    on the actual DSO rather than the text script.
    """
    fmt = _detect_binary_format(path)
    if fmt is None:
        resolved = _maybe_follow_linker_script(path)
        if resolved != path:
            return resolved, _detect_binary_format(resolved)
    return path, fmt


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
    See ``service._apply_native_provenance``'s identical parameter for why
    (Codex review, fresh evidence).
    """
    from .workflows.extraction import apply_provenance

    return apply_provenance(
        snap,
        public_headers,
        public_header_dirs,
        include_search_dirs=include_search_dirs,
    )


def _dump_native_binary(
    path: Path,
    binary_fmt: str,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    lang_explicit: bool = False,
    pdb_path: Path | None = None,
    dwarf_only: bool = False,
    debug_format: str | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
    include_dependencies: bool = True,
    public_include_search_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Dump an ABI snapshot from a native binary (ELF, PE, or Mach-O).

    Thin CLI wrapper over :func:`abicheck.service.run_dump` — the single source
    of truth for native dumping. It supplies a ``click.echo`` notifier so the
    "no headers" / "--include ignored" notes still reach stderr, and translates
    the framework-free errors into the CLI's ``click`` exceptions, preserving
    exit codes: ``ValidationError`` (unusable input / bad arguments) →
    :class:`click.UsageError` (exit 64); ``SnapshotError`` (operational failure)
    → :class:`click.ClickException` (exit 1).

    ``public_headers`` / ``public_header_dirs`` classify declaration provenance
    (ADR-024 Phase 1) on PE/Mach-O snapshots; a no-op for ELF and when empty.
    ``public_include_search_dirs`` (see ``service.run_dump``'s own docstring)
    is the caller's own genuinely explicit ``-I`` list, kept distinct from
    ``includes`` (which a caller may have already widened with auto-derived
    directories) so provenance widening never picks up a directory the
    caller didn't actually declare.
    ``compile`` carries the L2 cross-toolchain context (ADR-037 D3); ``run_dump``
    threads it into the PE/Mach-O header-scoping path (``_try_header_scoped_dump``).
    ``run_dump``'s header-only-graph attach (G29 Phase A: always attempted, no
    longer flag-gated) applies uniformly across ELF/PE/Mach-O — the sole reason
    this wrapper exists is to route through ``run_dump`` rather than duplicate
    its per-format dispatch.
    """
    from . import service
    from .errors import SnapshotError, ValidationError

    try:
        return service.run_dump(
            path,
            binary_fmt,
            headers,
            includes,
            version,
            lang,
            lang_explicit=lang_explicit,
            pdb_path=pdb_path,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            header_backend=header_backend,
            compile=compile,
            notify=_click_notify,
            include_dependencies=include_dependencies,
            public_include_search_dirs=public_include_search_dirs,
        )
    except ValidationError as exc:
        raise click.UsageError(str(exc)) from exc
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


def _resolve_input(
    path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    is_elf: bool | None = None,
    pdb_path: Path | None = None,
    dwarf_only: bool = False,
    debug_format: str | None = None,
    debug_roots: list[Path] | None = None,
    enable_debuginfod: bool = False,
    debuginfod_url: str | None = None,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    include_labels: dict[Path, str] | None = None,
    dump_manifest: DumpManifest | None = None,
    include_dependencies: bool = True,
) -> AbiSnapshot:
    """Auto-detect input type and return an AbiSnapshot.

    Thin CLI wrapper over :func:`abicheck.service.resolve_input` — the single
    source of truth for format detection, linker-script following, and native
    dumping. It supplies a ``click.echo`` notifier so progress notes reach
    stderr unchanged, and maps the framework-free errors to ``click`` exceptions
    so exit codes are preserved: ``ValidationError`` (unrecognised / unusable
    input) → :class:`click.UsageError` (exit 64); ``SnapshotError`` (operational
    failure loading or building the snapshot) → :class:`click.ClickException`
    (exit 1).

    Args:
        path: Path to the input file.
        headers: Public header files (required for ELF inputs).
        includes: Extra include directories (used for ELF inputs).
        version: Version label to embed in the resulting snapshot.
        lang: Language mode for castxml (``c++`` or ``c``).
        is_elf: Pre-computed ELF detection result; if *None*, the service layer
            detects the format from magic bytes.
        dwarf_only: If True, force DWARF-only mode (ADR-003).
        debug_format: Force debug format ("dwarf", "btf", "ctf") or None for auto.
        debug_roots / enable_debuginfod / debuginfod_url: Detached-debug-artifact
            resolution (ADR-021a) for this side — forwarded to
            ``service.resolve_input`` so a resolved build-id-tree/path-mirror
            ``.debug`` file actually feeds the DWARF parse for a stripped ELF
            input, not just a log line (P1.1). ``debuginfod_url`` overrides the
            default debuginfod server(s) — without threading it here, a custom
            server could be used for the (log-only) resolution probe elsewhere
            while the actual DWARF fetch silently fell back to the default.
        public_headers / public_header_dirs: Public-header set used to tag
            declaration provenance (ADR-024/ADR-015). Callers that already
            treat *headers* as the public contract (e.g. ``compare``'s
            ``--header``, which is documented as "Public header file or
            directory") should pass the same paths here too.
        include_labels: Resolved ``path -> label`` map from a labeled
            ``--include old:LABEL=PATH``/``new:LABEL=PATH`` entry (ADR-050
            D1), consulted when building this side's declared-``-I``
            ``IncludeDir`` list for ``comparability.compute_extraction_contract``.
            A path with no entry gets ``label=None``, unchanged.
        dump_manifest: A parsed ``--dump-manifest`` document for this side
            (ADR-050 D3, side-scoped on ``compare`` via ``old=``/``new=``),
            for a real multi-TU dump in place of a single header list. ELF
            only so far.

    ``service.resolve_input`` always attempts the L2 header-only semantic
    graph for a binary input (G29 Phase A: no longer flag-gated).
    """
    from . import service
    from .errors import SnapshotError, ValidationError

    try:
        return service.resolve_input(
            path,
            headers,
            includes,
            version,
            lang,
            is_elf=is_elf,
            pdb_path=pdb_path,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
            debug_roots=debug_roots,
            enable_debuginfod=enable_debuginfod,
            debuginfod_url=debuginfod_url,
            header_backend=header_backend,
            compile=compile,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            include_labels=include_labels,
            dump_manifest=dump_manifest,
            notify=_click_notify,
            include_dependencies=include_dependencies,
        )
    except ValidationError as exc:
        raise click.UsageError(str(exc)) from exc
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


def _populate_dependency_info(
    snap: AbiSnapshot,
    so_path: Path,
    search_paths: list[Path],
    sysroot: Path | None,
    ld_library_path: str,
) -> None:
    """Back-compat alias — the implementation now lives in the service layer.

    ADR-055 D1's second slice gave ``run_compare_request`` ``--follow-deps``
    parity, so this gained a second caller outside the CLI. It reads only
    leaf modules (``binder``/``model``/``resolver``), so it moved to the leaf
    ``dependency_info`` module both layers can depend on rather than either
    importing the other (AGENTS.md's rule for exactly this shape). Kept as a
    name here because ``cli.py`` imports and re-exports it.
    """
    from .dependency_info import populate_dependency_info

    populate_dependency_info(snap, so_path, search_paths, sysroot, ld_library_path)


def _is_supported_compare_input(path: Path) -> bool:
    """Return True for files accepted by compare-release directory scanning.

    Delegates to :func:`abicheck.classify.is_supported_compare_input` which
    runs a composable classifier pipeline (binary extensions → magic bytes →
    ABI JSON fingerprint → Perl dump → fallback sniff).

    To add support for a new ABI snapshot format, edit ``abicheck/classify.py``
    rather than this function.
    """
    from .workflows.extraction import is_supported_compare_input

    return is_supported_compare_input(path)


def _looks_like_application(path: Path) -> bool:
    """Positively identify an ELF *application* (executable), not a library.

    Returns True only when we are confident the file is an executable:
    ``ET_EXEC``, or a PIE (``ET_DYN`` with a ``PT_INTERP`` segment and a
    non-``.so`` filename). Anything inconclusive (unreadable, malformed program
    headers, a versioned ``.so`` name) returns False so the operand stays on the
    normal single-artifact path — we never *guess* a binary is an app (ADR-037
    D7: when the kind is genuinely ambiguous, the caller asks the user rather
    than mis-dispatching).
    """
    import struct

    from .workflows.extraction import (
        _ELF_MAGIC,
        _ET_DYN,
        _has_interp_segment,
        _has_shared_object_name,
    )

    _ET_EXEC = 2
    try:
        with open(path, "rb") as f:
            if f.read(4) != _ELF_MAGIC:
                return False
            ei_class_raw = f.read(1)
            ei_data_raw = f.read(1)
            if len(ei_class_raw) != 1 or len(ei_data_raw) != 1:
                return False
            ei_class = ei_class_raw[0]
            ei_data = ei_data_raw[0]
            # Unknown class/endianness ⇒ inconclusive: return False rather than
            # fall through to big-endian parsing and risk misreading e_type.
            if ei_class not in (1, 2) or ei_data not in (1, 2):
                return False
            f.seek(16)
            byte_order = "<" if ei_data == 1 else ">"
            e_type = struct.unpack(f"{byte_order}H", f.read(2))[0]
            if e_type == _ET_EXEC:
                return True
            if e_type == _ET_DYN:
                # PIE executable: ET_DYN + an interpreter + not a .so-style name.
                has_interp = _has_interp_segment(f, ei_class, byte_order)
                return has_interp is True and not _has_shared_object_name(path)
            return False
    except (OSError, struct.error, IndexError):
        return False


def classify_compare_operand(path: Path) -> str:
    """Classify a ``compare`` operand for ADR-037 D7 input-type dispatch.

    Returns one of:

    * ``"package"``   — a recognised archive/package (RPM/Deb/tar/conda/wheel);
      a *set* input that fans out to per-library comparison.
    * ``"directory"`` — a plain directory of libraries; also a set input.
    * ``"app"``       — an ELF application/executable (or ambiguous PIE) that
      ``compare`` cannot pair as a library (hint the user at ``appcompat``).
    * ``"file"``      — a single ``.so`` / JSON snapshot / Perl dump: the default
      single-pair path, unchanged.
    """
    from .workflows.extraction import is_package

    if path.is_dir():
        return "directory"
    if is_package(path):
        return "package"
    norm, fmt = _normalize_binary_input(path)
    if fmt == "elf" and _looks_like_application(norm):
        return "app"
    return "file"


def _resolve_compare_snapshots(
    old_input: Path,
    new_input: Path,
    old_fmt: str | None,
    new_fmt: str | None,
    old_h: list[Path],
    new_h: list[Path],
    old_inc: list[Path],
    new_inc: list[Path],
    old_version: str,
    new_version: str,
    lang: str,
    pdb_path: Path | None,
    old_pdb_path: Path | None,
    new_pdb_path: Path | None,
    dwarf_only: bool,
    debug_format: str | None,
    follow_deps: bool,
    search_paths: tuple[Path, ...],
    ld_library_path: str,
    header_backend: str = "auto",
    old_header_backend: str | None = None,
    new_header_backend: str | None = None,
    compile_context: CompileContext | None = None,
    old_debug_roots: list[Path] | None = None,
    new_debug_roots: list[Path] | None = None,
    enable_debuginfod: bool = False,
    debuginfod_url: str | None = None,
    include_labels: dict[Path, str] | None = None,
    old_dump_manifest: DumpManifest | None = None,
    new_dump_manifest: DumpManifest | None = None,
    include_dependencies: bool = False,
    lang_explicit: bool = False,
) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Load both ABI snapshots and (optionally) populate ELF dependency info.

    ``include_dependencies`` (default ``False``, mirroring ``dump``'s own
    default): both sides are filtered via
    ``dumper_scoping.resolve_dependency_scope`` the same way ``dump``
    filters by default -- this is what makes ``dump old.so -o base.json``
    then ``compare base.json new.so`` compare consistently by default,
    instead of the historical asymmetry (a filtered ``dump`` baseline vs.
    compare's always-unfiltered live-binary dumping).

    ``lang_explicit`` (G31 Phase C follow-up): whether ``lang`` reflects a
    genuinely explicit ``--lang`` on the command line rather than Click's
    own default (which is the identical, indistinguishable string) — see
    ``cli.compare_cmd``'s own detection and
    :attr:`abicheck.api_types.CompareRequest.lang_explicit`. ``False`` (the
    default) preserves this function's pre-existing behavior for both sides.

    ``header_backend`` is the both-sides default; ``old_header_backend`` /
    ``new_header_backend`` override it for one side only (``None`` = inherit).
    A per-side override lets a release whose new headers need the host
    toolchain parse on ``clang`` while the old release keeps the ``castxml``
    schema reference — the backend mirror of ``--old-header``/``--new-header``.

    ``compile_context`` carries the both-sides L2 cross-toolchain knobs
    (``--gcc-*``/``--sysroot``/``--nostdinc``, ADR-037 D3) merged with the project
    ``compile:`` block; it applies to both sides. Its ``frontend`` field is unused
    here — the frontend is driven by the explicit ``header_backend`` so the per-side
    override above still wins.

    ``old_dump_manifest`` / ``new_dump_manifest`` (ADR-050 D3): a parsed
    ``--dump-manifest`` document for that side only, in place of a single
    header list — side-scoped since old/new commonly live under different
    roots.

    ``old_debug_roots`` / ``new_debug_roots`` / ``enable_debuginfod`` /
    ``debuginfod_url`` (P1.1, ADR-021a): per-side detached-debug-artifact
    resolution (``--debug-root old=/new=``, ``--debuginfod``,
    ``--debuginfod-url``), forwarded to each side's ``_resolve_input`` so a
    resolved ``.debug`` file actually feeds that side's DWARF parse — a custom
    debuginfod server must reach the actual fetch, not just the (log-only)
    resolution probe elsewhere.

    Both sides always attempt the L2 header-only semantic graph (G29 Phase A:
    no longer flag-gated) — the existing build-source-pack graph diff already
    handles a ``SourceGraphSummary`` from any evidence tier uniformly, so
    populating it here from headers alone (no build system required) makes it
    reachable from plain ``compare``.

    **ADR-055 D1: this no longer resolves anything itself.** It assembles a
    :class:`~abicheck.api_types.CompareRequest` from ``compare``'s loose
    arguments and hands it to the shared
    :func:`abicheck.service.resolve_compare_request`, which is the same
    resolution the typed Python API and the MCP ``abi_compare`` tool run. It
    kept a parallel implementation until now only because
    ``run_compare_request`` was one function with no seam for ``compare``'s
    Click-dependent ADR-049 ``resolve_and_apply`` step to sit in; splitting
    that function into its two phases removed the reason for the copy. What
    stays CLI-specific is exactly what is genuinely CLI-specific: the
    ``click.echo`` notifier, translating the framework-free errors into
    ``click`` exceptions (the contract ``_resolve_input`` documented), and
    ``allow_parallel=False`` to keep both sides resolving sequentially the way
    this path always has.
    """
    from .api_types import CompareRequest, InputSpec
    from .errors import PlanningError, SnapshotError, ValidationError

    def _side_compile(backend_override: str | None) -> CompileContext | None:
        # The per-side --ast-frontend old=/new= rides on that side's own
        # CompileContext.frontend, which `_run_dump_uncached` documents as
        # outranking the bare both-sides `header_backend`. The caller has
        # already neutralised `compile_context.frontend` to "auto" for exactly
        # this reason, so there is nothing to lose by replacing it.
        if backend_override is None:
            return compile_context
        import dataclasses

        from .compile_context import CompileContext as _CompileContext

        base = compile_context if compile_context is not None else _CompileContext()
        return dataclasses.replace(base, frontend=backend_override)

    request = CompareRequest(
        old=InputSpec(
            path=old_input,
            headers=tuple(old_h),
            includes=tuple(old_inc),
            version=old_version,
            pdb=old_pdb_path if old_pdb_path else pdb_path,
            debug_roots=tuple(old_debug_roots or ()),
            include_dependencies=include_dependencies,
            dump_manifest=old_dump_manifest,
            compile=_side_compile(old_header_backend),
        ),
        new=InputSpec(
            path=new_input,
            headers=tuple(new_h),
            includes=tuple(new_inc),
            version=new_version,
            pdb=new_pdb_path if new_pdb_path else pdb_path,
            debug_roots=tuple(new_debug_roots or ()),
            include_dependencies=include_dependencies,
            dump_manifest=new_dump_manifest,
            compile=_side_compile(new_header_backend),
        ),
        lang=lang,
        lang_explicit=lang_explicit,
        frontend=header_backend,
        dwarf_only=dwarf_only,
        debug_format=debug_format,
        include_labels=tuple((include_labels or {}).items()),
        follow_dependencies=follow_deps,
        dependency_search_paths=tuple(search_paths),
        ld_library_path=ld_library_path,
        enable_debuginfod=enable_debuginfod,
        debuginfod_url=debuginfod_url,
    )
    from . import service

    try:
        pair = service.resolve_compare_request(
            request, notify=_click_notify, allow_parallel=False
        )
    except (ValidationError, PlanningError) as exc:  # PlanningError: ADR-063 Phase 4
        raise click.UsageError(str(exc)) from exc
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc
    return pair.old, pair.new


# ── Set-input (directory/package) compare guards (ADR-037 D3/D12) ─────────────
#
# The per-library release fan-out forwards only release-comparison kwargs, and
# does not collect inline build/source evidence per pair (L3-L5) — those flags
# would be silently dropped on a directory/package compare, so they are
# rejected loudly instead (Codex review). Kept here (not in cli.py) so cli.py
# stays under the file-size hard cap.
#
# The both-sides L2 compile context (--ast-frontend/--compiler/
# --compiler-prefix/--compiler-option/--sysroot/--nostdinc/--frontend-context)
# *is* now threaded through the fan-out (cli_compare_helpers.run_compare
# resolves one CompileContext for the whole release, the same way a
# single-pair compare resolves its own, and forwards it to every library pair
# — see cli_compare_release._run_compare_pair's compile_context parameter).
# Only the *sided* --ast-frontend old=/new= override has no home here: it
# means "parse the old library's headers with a different frontend than the
# new one", which has no per-library-pair-within-a-release equivalent to
# mirror (a release fan-out already compares each library's own old vs. new
# under one shared context) — so it stays rejected below.


#: Build/source evidence *input* flags (param dest → flag): the four
#: per-side --sources/--build-info. ``--depth`` deliberately isn't here
#: (D1): see :func:`~abicheck.cli_compare_options._reject_depth_for_set_inputs`.
#: ADR-040 L1: keyed on the *side-aware* CLI param dests (``sources`` /
#: ``build_info``) — the rejection runs on the raw Click params (before the
#: sided values are normalised into per-side kwargs), so it must check the
#: dest the user actually typed to.
_EVIDENCE_SET_INPUT_FLAGS: dict[str, str] = {
    "sources": "--sources",
    "build_info": "--build-info",
    "dump_manifest": "--dump-manifest",
}


def _reject_evidence_flags_for_set_inputs(ctx: click.Context) -> str | None:
    """Reject inline build/source evidence flags for directory/package compares.

    The release fan-out forwards only release-comparison kwargs, so the
    per-side ``--old/new-sources`` / ``--old/new-build-info`` would be
    accepted and silently dropped (no L3-L5 collected). Fail loudly so the
    user knows to compare libraries individually to collect deep evidence
    (Codex review). ``--depth`` is handled separately (D1, moved to
    :mod:`abicheck.cli_compare_options`); its return is returned here too.

    G29 Phase A: the L2 header-only semantic graph is structurally skipped
    for directory/package (set-input) compares instead of rejected here,
    since the fan-out never calls a graph-attaching single-pair path
    (unchanged); see ``docs/contribute/plans/g31-header-graph-default-on-followup.md``.
    """
    from .cli_compare_options import _reject_depth_for_set_inputs

    used = [
        flag
        for dest, flag in _EVIDENCE_SET_INPUT_FLAGS.items()
        if ctx.get_parameter_source(dest) == click.core.ParameterSource.COMMANDLINE
    ]
    if used:
        raise click.UsageError(
            ", ".join(sorted(used))
            + " "
            + ("is" if len(used) == 1 else "are")
            + " not supported for directory/package (release) comparisons: the "
            "per-library fan-out does not collect inline build/source evidence. "
            "Compare the libraries individually (or pre-dump snapshots with "
            "`dump --sources/--build-info`) to collect L3-L5 evidence."
        )
    return _reject_depth_for_set_inputs(ctx)


def _reject_compile_context_for_set_inputs(ctx: click.Context) -> None:
    """Guard the *sided* per-side L2 compile context for directory/package compares.

    The both-sides compile context (--ast-frontend/--compiler/
    --compiler-prefix/--compiler-option/--sysroot/--nostdinc/
    --frontend-context, and the project ``.abicheck.yml`` ``compile:`` block)
    is threaded through the release fan-out — see this module's own comment
    above. Only a sided ``--ast-frontend old=/new=`` override has no
    per-library-pair-within-a-release meaning, so it is rejected loudly here
    (a `UsageError`, mirroring the `--exit-code-scheme` guard) rather than
    silently ignored.

    Detected via :func:`cli_options.sided_frontend_explicit`, not a plain
    ``ctx.get_parameter_source("old_header_backend")`` dict lookup:
    ``old_header_backend``/``new_header_backend`` are kwargs
    ``normalize_sided_options`` synthesizes into the command's own kwargs
    dict, never real Click-registered parameters — so `get_parameter_source`
    for either name is never ``COMMANDLINE``, making a dict-keyed check on
    them silently inert (a prior revision of this guard carried exactly that
    dead check).
    """
    # Lazy import: cli_options already imports this module (lazily) for
    # classify_compare_operand/_reject_evidence_flags_for_set_inputs, so a
    # top-level `from .cli_options import ...` here would close that cycle
    # (see cli_options.py's own "cli_options -> cli_resolve -> ..." comment).
    from .cli_options import sided_frontend_explicit

    if sided_frontend_explicit(ctx):
        raise click.UsageError(
            "--ast-frontend old=/new= is not supported for directory/package "
            "(release) comparisons: a sided override has no per-library-pair "
            "meaning across a release (every library's own old vs. new is "
            "already compared under one shared, both-sides compile context). "
            "Compare the libraries individually to use a sided override."
        )


def resolve_directory_compile_context(
    ctx: click.Context,
    *,
    gcc_options: str | None,
    sysroot: Any,
    nostdinc: bool,
    header_backend: str,
    includes: Any,
    build_config: Any,
    frontend_context: str,
    compiler_path: str | None,
    compiler_prefix: str | None,
    compiler_option_tokens: tuple[str, ...],
) -> Any:
    """Resolve the both-sides L2 compile context for a directory/package
    compare's release fan-out -- the identical ``resolve_compile_context``
    call the single-pair path uses, folding the project ``.abicheck.yml``
    ``compile:`` block in the same way (CLI > config). Returns
    ``(CompileContext, merged_includes)`` -- the caller must forward
    *both*: dropping the merged-includes half silently drops
    ``compile.include_dirs`` for every library (Codex review).
    """
    from .cli_options import resolve_compile_context

    return resolve_compile_context(
        ctx,
        gcc_options=gcc_options,
        sysroot=sysroot,
        nostdinc=nostdinc,
        header_backend=header_backend,
        includes=includes,
        build_config=build_config,
        frontend_context=frontend_context,
        compiler_path=compiler_path,
        compiler_prefix=compiler_prefix,
        compiler_option_tokens=compiler_option_tokens,
    )
