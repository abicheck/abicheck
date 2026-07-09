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

"""Helper functions for the ``dump`` CLI command (split from cli.py)."""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import click

from .dumper import dump
from .errors import AbicheckError

if TYPE_CHECKING:
    from .model import AbiSnapshot


class _ExpandHeaderInputs(Protocol):
    def __call__(self, inputs: list[Path]) -> list[Path]: ...


class _PopulateDependencyInfo(Protocol):
    def __call__(
        self,
        snap: AbiSnapshot,
        so_path: Path,
        search_paths: list[Path],
        sysroot: Path | None,
        ld_library_path: str,
    ) -> None: ...


class _StampProvenance(Protocol):
    def __call__(
        self,
        snap: AbiSnapshot,
        *,
        git_tag: str | None,
        build_id: str | None,
        no_git: bool,
    ) -> None: ...


class _WriteSnapshotOutput(Protocol):
    def __call__(
        self,
        snap: AbiSnapshot,
        output: Path | None,
        build_info: Path | None,
        sources: Path | None,
        build_config: Path | None,
        allow_build_query: bool,
        collect_mode: str,
        build_query: str | None = ...,
        build_compile_db: str | None = ...,
        extractor: str = ...,
    ) -> None: ...


def _user_define_flags(
    gcc_option_tokens: tuple[str, ...], user_gcc_options: str | None
) -> list[str]:
    """The user's *global* define-affecting flags for the ADR-039 collector.

    Combines the ``-D``/``-U`` in the ``--gcc-options`` string with the repeatable
    ``--gcc-option`` tokens, **in the same order the real dump applies them** —
    ``dumper._castxml_cmd`` appends ``gcc_options`` first, then
    ``gcc_option_tokens`` (see ``dumper.py``), so the collector must too (Codex
    review #498). Order is significant because ``defines_from_flags`` honours
    ``-D``/``-U`` sequence: ``--gcc-options=-DKEEP --gcc-option=-UKEEP`` must leave
    ``KEEP`` *inactive* on both the parse and the harvest, else the reconciler
    would add back a field the real parse pruned. These flags are applied on top
    of the compile-DB intersection, so a user ``-UKEEP`` also overrides a database
    ``-DKEEP``. The auto-derived first-header build context is deliberately
    excluded (it must not be unioned snapshot-wide).

    A malformed ``--gcc-options`` (e.g. an unbalanced quote) must not abort the
    dump — ``shlex.split`` errors are swallowed and only the tokens are used
    (CodeRabbit review)."""
    flags: list[str] = []
    if user_gcc_options:
        try:
            flags += shlex.split(user_gcc_options)
        except ValueError:
            pass  # bad optional define flags are skipped, not fatal
    flags += list(gcc_option_tokens)
    return flags


def _attach_build_context(
    snap: AbiSnapshot,
    compile_db: str | Path,
    headers: list[Path],
    extra_flags: list[str],
    source_filter: str | None = None,
) -> None:
    """ADR-039 collection layer: harvest the build's active ``-D`` set and scan the
    public headers for ``#ifdef``-guarded record fields, attaching both to *snap*.

    Best-effort and additive — a plain context-free dump (no compile DB) never
    reaches here, and an empty harvest leaves the snapshot's defaults untouched, so
    the pass is a safe no-op unless real build evidence is found. *source_filter*
    (``--compile-db-filter``) selects the same compile-DB entries the header parse
    used."""
    from .header_conditionals import collect_build_context

    bc_defines, bc_conditional = collect_build_context(
        headers, compile_db, extra_flags=extra_flags, source_filter=source_filter
    )
    if bc_defines:
        snap.build_context_defines = bc_defines
    if bc_conditional:
        snap.conditional_fields = bc_conditional


def resolve_dump_debug_format(
    debug_format_opt: str | None,
    debug_format: str | None,
) -> str | None:
    """Reconcile --debug-format selector with legacy --btf/--ctf/--dwarf flags.

    The selector supersedes the legacy flags whenever it is given: an explicit
    "auto" returns to auto-detection (None) even if a legacy flag is also
    present; only when the selector is absent do the legacy flags apply.
    """
    if debug_format_opt is not None:
        return None if debug_format_opt.lower() == "auto" else debug_format_opt
    return debug_format


def resolve_dump_depth(
    depth: str | None,
    max_depth: bool,
    default_mode: str,
) -> str:
    """Resolve the ``--depth``/``--max`` preset into the internal collect-mode value.

    ``--depth`` is the friendly evidence-depth dial (same vocabulary as
    ``scan --depth``: binary/headers/build/source/full); it expands to the
    underlying ADR-033 collect mode via the shared ``scan_levels`` mapping so the
    commands stay consistent. ``--max`` is shorthand for ``--depth full``.

    Raises :class:`click.UsageError` if ``--max`` is combined with a different
    ``--depth``. When no depth preset is supplied, the command's *default_mode* is
    returned (``dump`` embeds at ``source-target``; ``compare`` reads at ``off``).
    """
    from .buildsource.scan_levels import (
        EvidenceDepth,
        depth_to_method,
        method_to_collect_mode,
    )

    if max_depth:
        if depth is not None and depth != EvidenceDepth.FULL.value:
            raise click.UsageError(
                "--max is shorthand for --depth full; do not combine it with a "
                "different --depth."
            )
        depth = EvidenceDepth.FULL.value
    if depth is None:
        return default_mode
    method = depth_to_method(EvidenceDepth(depth))
    # headers depth reaches no source method (L2 is intrinsic) — collect nothing.
    return "off" if method is None else method_to_collect_mode(method)


def resolve_dump_compile_db(
    compile_db_path: Path | None,
    compile_db_path_alt: Path | None,
    headers: tuple[Path, ...],
) -> Path | None:
    """Resolve -p / --compile-db aliases and validate header requirement.

    Raises :class:`click.UsageError` if a compile DB is given but no headers.
    Returns the effective compile DB path (or *None*).
    """
    effective_compile_db = compile_db_path or compile_db_path_alt
    if effective_compile_db and not headers:
        raise click.UsageError(
            "Compilation database (-p / --compile-db) requires -H/--header. "
            "Without headers, CastXML has nothing to parse."
        )
    return effective_compile_db


def perform_elf_dump(
    so_path: Path,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    version: str,
    lang: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
    effective_gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    sysroot: Path | None,
    nostdinc: bool,
    dwarf_only: bool,
    effective_debug_format: str | None,
    public_headers: tuple[Path, ...],
    public_header_dirs: tuple[Path, ...],
    effective_compile_db: Path | None,
    follow_deps: bool,
    search_paths: tuple[Path, ...],
    ld_library_path: str,
    git_tag: str | None,
    build_id: str | None,
    no_git: bool,
    output: Path | None,
    build_info: Path | None,
    sources: Path | None,
    build_config: Path | None,
    allow_build_query: bool,
    collect_mode: str,
    expand_header_inputs: _ExpandHeaderInputs,
    populate_dependency_info: _PopulateDependencyInfo,
    stamp_provenance: _StampProvenance,
    write_snapshot_output: _WriteSnapshotOutput,
    build_query: str | None = None,
    build_compile_db: str | None = None,
    header_backend: str = "auto",
    user_gcc_options: str | None = None,
    compile_db_filter: str | None = None,
) -> None:
    """Run the ELF dump pipeline and write output.

    All helper callables (expand_header_inputs, populate_dependency_info,
    stamp_provenance, write_snapshot_output) are passed in from cli.py to avoid
    an import cycle — cli_dump_helpers must not import from cli.
    """
    compiler = "cc" if lang == "c" else "c++"
    resolved_headers = expand_header_inputs(list(headers)) if headers else []
    # P3: auto-add the public-header roots so a -H umbrella resolves its own
    # relative includes without a separate -I. resolve_inferred_header_roots
    # picks the search bucket: plain -I (high priority, so an umbrella that pulls
    # a system-colliding name like <endian.h> still finds the package header)
    # when there is no build context, or -isystem (below the build-context dirs
    # so generated/shim headers from -p/--gcc-options keep priority, but still
    # above the standard system dirs) when the compile context supplies its own
    # includes — see its docstring.
    from .header_utils import deferred_token_dirs, resolve_inferred_header_roots

    inc_extra, deferred = (
        resolve_inferred_header_roots(
            list(headers),
            list(includes),
            gcc_options=effective_gcc_options,
            gcc_option_tokens=tuple(gcc_option_tokens),
        )
        if resolved_headers
        else ([], [])
    )
    # Deferred roots ride in gcc_option_tokens (as -isystem), not extra_includes,
    # so their contents must be hashed into the AST cache key explicitly (Codex).
    deferred_dirs = tuple(deferred_token_dirs(deferred))
    try:
        snap = dump(
            so_path=so_path,
            headers=resolved_headers,
            extra_includes=list(includes) + inc_extra,
            version=version,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=effective_gcc_options,
            gcc_option_tokens=tuple(gcc_option_tokens) + tuple(deferred),
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang if lang == "c" else None,
            dwarf_only=dwarf_only,
            debug_format=effective_debug_format,
            public_headers=list(public_headers),
            public_header_dirs=list(public_header_dirs),
            header_backend=header_backend,
            extra_hash_dirs=deferred_dirs,
        )
    except (AbicheckError, RuntimeError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    # Record that the header AST was parsed with the real build context (ADR-029)
    if effective_compile_db and resolved_headers:
        snap.parsed_with_build_context = True

    # ADR-039 collection layer — when a compile DB is available, harvest the
    # build's active ``-D`` set and scan the public headers for ``#ifdef``-guarded
    # record fields, so the reconciler can clear a context-free header-parse false
    # positive (a guarded field the context-free castxml parse pruned). Best-effort
    # and additive: absent/empty on a plain context-free dump.
    if effective_compile_db and resolved_headers:
        # Augment the sound per-command compile-DB intersection with the user's
        # *global* flags only: the repeatable ``--gcc-option`` tokens and the
        # ``-D``/``-U`` in the ``--gcc-options`` string (``user_gcc_options``).
        # A user ``--gcc-options=-UKEEP`` must override a DB ``-DKEEP`` (Codex
        # review #498). We deliberately do NOT feed ``effective_gcc_options``,
        # which also carries the *first* resolved header's auto-derived build
        # context — unioning that snapshot-wide would mark one TU's ``-DKEEP``
        # active for every scanned header.
        _attach_build_context(
            snap,
            effective_compile_db,
            resolved_headers,
            _user_define_flags(gcc_option_tokens, user_gcc_options),
            source_filter=compile_db_filter,
        )

    # G14: recognise a CPython extension module and attach its metadata so the
    # written snapshot carries the abi3 / imported-C-API surface. The ELF `dump`
    # CLI reaches `dumper.dump` directly (not `service.run_dump`), so this is the
    # attach point for that path; `detect_python_extension` is a leaf import (no
    # cycle) and a no-op for ordinary libraries. `compare` also derives it on
    # load as a backstop for snapshots written without it.
    if snap.python_ext is None:
        from .python_ext import detect_python_extension

        snap.python_ext = detect_python_extension(snap)

    # G23: recover the Python-visible API surface from a sibling `.pyi` stub, so
    # the snapshot also carries the function/class/method signatures a consumer
    # `import`s — the surface the C-ABI export view cannot see. A no-op when no
    # stub is found alongside the binary.
    if snap.python_api is None:
        from .python_api import detect_python_api

        snap.python_api = detect_python_api(snap)

    if follow_deps:
        populate_dependency_info(snap, so_path, list(search_paths), sysroot, ld_library_path)

    stamp_provenance(snap, git_tag=git_tag, build_id=build_id, no_git=no_git)
    write_snapshot_output(
        snap, output, build_info, sources, build_config, allow_build_query,
        collect_mode, build_query=build_query, build_compile_db=build_compile_db,
        extractor=header_backend,
    )
