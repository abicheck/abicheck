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

from .buildsource.build_query import PRUNED_HEADER_DIR_SEGMENTS
from .compat.abicc_dump_import import looks_like_perl_dump
from .header_utils import iter_directory_headers

if TYPE_CHECKING:
    from pathlib import Path

    from .model import AbiSnapshot
    from .service_scan import CompileContext


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
    """Read a small header chunk and return 'json', 'perl', or 'unknown'."""
    try:
        with open(path, "rb") as f:
            raw = f.read(_SNIFF_BYTES)
        head = raw.decode("utf-8", errors="replace").lstrip()
    except OSError:
        return "unknown"
    # Check Perl dump BEFORE JSON — a Perl dump can start with $VAR1 = {
    # which would incorrectly match the JSON heuristic after the '{'
    if looks_like_perl_dump(head):
        return "perl"
    if head.startswith("{"):
        return "json"
    return "unknown"


def _detect_binary_format(path: Path) -> str | None:
    """Detect binary format from magic bytes.

    Returns 'elf', 'pe', 'macho', or None for non-binary / unknown.
    """
    from .binary_utils import detect_binary_format

    return detect_binary_format(path)


def _resolve_linker_script(path: Path) -> tuple[Path | None, bool]:
    """Resolve a GNU ld linker script to the shared library it points at.

    Returns ``(resolved_path, is_linker_script)``. ``is_linker_script`` is True
    when *path* looks like a GNU ld script (so callers can emit a targeted hint
    even when no target file could be located); ``resolved_path`` is the first
    ``INPUT()``/``GROUP()`` member that exists next to the script, or *None*.
    """
    from .binary_utils import resolve_linker_script

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
) -> AbiSnapshot:
    """Tag declaration provenance on a PE/Mach-O snapshot (ADR-024 Phase 1).

    Mirrors the ELF path (``dumper.create_snapshot``), which always runs
    ``apply_provenance``. A no-op when no public-header set is supplied —
    every origin stays ``UNKNOWN`` and behaviour is unchanged.
    """
    from .provenance import apply_provenance

    return apply_provenance(snap, public_headers, public_header_dirs)


def _dump_native_binary(
    path: Path,
    binary_fmt: str,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    pdb_path: Path | None = None,
    dwarf_only: bool = False,
    debug_format: str | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
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
            pdb_path=pdb_path,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            header_backend=header_backend,
            compile=compile,
            notify=_click_notify,
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
            notify=_click_notify,
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
    """Resolve transitive deps and store DependencyInfo in the snapshot."""
    from .binder import BindingStatus, compute_bindings
    from .model import DependencyInfo
    from .resolver import resolve_dependencies

    graph = resolve_dependencies(
        so_path,
        search_paths=search_paths or None,
        sysroot=sysroot,
        ld_library_path=ld_library_path,
    )
    bindings = compute_bindings(graph)

    summary: dict[str, int] = {}
    for b in bindings:
        summary[b.status.value] = summary.get(b.status.value, 0) + 1

    missing = [
        {"consumer": b.consumer, "symbol": b.symbol, "version": b.version}
        for b in bindings
        if b.status == BindingStatus.MISSING
    ]

    snap.dependency_info = DependencyInfo(
        nodes=[
            {
                "path": str(node.path),
                "soname": node.soname,
                "needed": node.needed,
                "depth": node.depth,
                "resolution_reason": node.resolution_reason,
            }
            for node in sorted(graph.nodes.values(), key=lambda n: (n.depth, n.soname))
        ],
        edges=[
            {"consumer": consumer, "provider": provider}
            for consumer, provider in graph.edges
        ],
        unresolved=[
            {"consumer": consumer, "soname": soname}
            for consumer, soname in graph.unresolved
        ],
        bindings_summary=summary,
        missing_symbols=missing,
    )


def _is_supported_compare_input(path: Path) -> bool:
    """Return True for files accepted by compare-release directory scanning.

    Delegates to :func:`abicheck.classify.is_supported_compare_input` which
    runs a composable classifier pipeline (binary extensions → magic bytes →
    ABI JSON fingerprint → Perl dump → fallback sniff).

    To add support for a new ABI snapshot format, edit ``abicheck/classify.py``
    rather than this function.
    """
    from .classify import is_supported_compare_input

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

    from .package import (
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
    from .package import is_package

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
) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Load both ABI snapshots and (optionally) populate ELF dependency info.

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
    """
    old_backend = old_header_backend or header_backend
    new_backend = new_header_backend or header_backend
    # compare's --header is documented as "Public header file or directory"
    # (unlike dump's split -H/--public-header, compare has no lower-level
    # "parse only, don't classify" mode) — so the same paths given via
    # --header double as the public-header set for provenance tagging.
    old = _resolve_input(
        old_input,
        old_h,
        old_inc,
        old_version,
        lang,
        is_elf=True if old_fmt == "elf" else None,
        pdb_path=old_pdb_path if old_pdb_path else pdb_path,
        dwarf_only=dwarf_only,
        debug_format=debug_format,
        debug_roots=old_debug_roots,
        enable_debuginfod=enable_debuginfod,
        debuginfod_url=debuginfod_url,
        header_backend=old_backend,
        compile=compile_context,
        public_headers=old_h,
    )
    new = _resolve_input(
        new_input,
        new_h,
        new_inc,
        new_version,
        lang,
        is_elf=True if new_fmt == "elf" else None,
        pdb_path=new_pdb_path if new_pdb_path else pdb_path,
        dwarf_only=dwarf_only,
        debug_format=debug_format,
        debug_roots=new_debug_roots,
        enable_debuginfod=enable_debuginfod,
        debuginfod_url=debuginfod_url,
        header_backend=new_backend,
        compile=compile_context,
        public_headers=new_h,
    )
    if follow_deps:
        if old_fmt == "elf":
            _populate_dependency_info(
                old, old_input, list(search_paths), None, ld_library_path
            )
        if new_fmt == "elf":
            _populate_dependency_info(
                new, new_input, list(search_paths), None, ld_library_path
            )
    return old, new


# ── Set-input (directory/package) compare guards (ADR-037 D3/D12) ─────────────
#
# The per-library release fan-out forwards only release-comparison kwargs; it
# does not thread the single-pair L2 compile context or inline build/source
# evidence per pair. So the corresponding flags would be silently dropped on a
# directory/package compare — reject them loudly instead (Codex review). Kept
# here (not in cli.py) so cli.py stays under the file-size hard cap.

#: Compile-context flag dest → spelling, for the set-input rejection guard.
_COMPILE_CONTEXT_SET_INPUT_FLAGS: dict[str, str] = {
    "gcc_path": "--gcc-path",
    "gcc_prefix": "--gcc-prefix",
    "gcc_options": "--gcc-options",
    "gcc_option_tokens": "--gcc-option",
    "sysroot": "--sysroot",
    "nostdinc": "--nostdinc",
    "header_backend": "--ast-frontend",
    "old_header_backend": "--old-ast-frontend",
    "new_header_backend": "--new-ast-frontend",
}

#: Build/source evidence flags (param dest → flag). ``depth`` is the
#: evidence-depth dial; the four per-side --sources/--build-info are the
#: inline evidence inputs.
#: ADR-040 L1: keyed on the *side-aware* CLI param dests (``sources`` /
#: ``build_info``) — the rejection runs on the raw Click params (before the
#: sided values are normalised into per-side kwargs), so it must check the
#: dest the user actually typed to.
_EVIDENCE_SET_INPUT_FLAGS: dict[str, str] = {
    "depth": "--depth",
    "sources": "--sources",
    "build_info": "--build-info",
}


def _reject_evidence_flags_for_set_inputs(ctx: click.Context) -> None:
    """Reject inline build/source evidence flags for directory/package compares.

    The release fan-out forwards only release-comparison kwargs, so
    ``--depth`` and the per-side ``--old/new-sources`` / ``--old/new-build-info``
    would be accepted and silently dropped (no L3-L5 collected). Fail loudly
    so the user knows to compare libraries individually to collect deep
    evidence (Codex review).

    G29 Phase A: the L2 header-only semantic graph no longer has a CLI flag
    to reject here — it is structurally skipped for directory/package
    (set-input) compares instead, since the per-library fan-out never calls
    ``resolve_input``/``run_dump`` with a graph-attaching single-pair path in
    the first place (unchanged from before this change); see
    ``docs/development/plans/g31-header-graph-default-on-followup.md`` for
    the Phase B+ plan to extend graph coverage to set inputs.
    """
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


def _config_has_compile_block(project_cfg: Any) -> bool:
    """True if a loaded ``.abicheck.yml`` carries any ``compile:`` setting.

    Used to flag that a project's L2 compile context would be dropped by the
    per-library release fan-out (which the single-pair path honors).
    """
    if project_cfg is None:
        return False
    return bool(
        getattr(project_cfg, "compile_frontend", None)
        or getattr(project_cfg, "compile_std", None)
        or getattr(project_cfg, "compile_defines", None)
        or getattr(project_cfg, "compile_include_dirs", None)
        or getattr(project_cfg, "compile_sysroot", None)
        or getattr(project_cfg, "compile_nostdinc", False)
    )


def _reject_compile_context_for_set_inputs(
    ctx: click.Context, project_cfg: Any
) -> None:
    """Guard the L2 compile context for directory/package compares.

    The per-library fan-out (release backend) runs each pair through
    `service.run_compare` without a `CompileContext`, so the L2 cross-toolchain /
    frontend context is not applied per library — unlike the single-pair path that
    now honors it. Two cases, never silent (Codex review):

    * An **explicitly-passed** compile-context flag is rejected loudly (a
      `UsageError`, mirroring the `--exit-code-scheme` guard): the user asked for
      it, so erroring beats ignoring it.
    * An **ambient** project ``.abicheck.yml`` ``compile:`` block only *warns*:
      a plain ``compare dir1 dir2`` in a configured project shouldn't hard-fail,
      but the user must know those settings apply to single-library compares and
      not to this fan-out (so per-library snapshots may differ).

    Either way, compare libraries individually (or pre-dump snapshots) to apply
    the context.
    """
    used = [
        flag
        for dest, flag in _COMPILE_CONTEXT_SET_INPUT_FLAGS.items()
        if ctx.get_parameter_source(dest) == click.core.ParameterSource.COMMANDLINE
    ]
    if used:
        raise click.UsageError(
            ", ".join(sorted(used))
            + " "
            + ("is" if len(used) == 1 else "are")
            + " not supported for directory/package (release) comparisons: the "
            "per-library fan-out does not thread the L2 compile context to each "
            "pair's header dump. Compare the libraries individually to use them."
        )
    if _config_has_compile_block(project_cfg):
        click.echo(
            "Warning: the .abicheck.yml compile: block is not applied to "
            "directory/package (release) comparisons — the per-library fan-out "
            "does not thread the L2 compile context. It affects single-library "
            "compares only; compare libraries individually for consistent "
            "per-library snapshots.",
            err=True,
        )
