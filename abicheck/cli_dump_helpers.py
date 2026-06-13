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

from pathlib import Path
from typing import TYPE_CHECKING

import click

from .dumper import dump
from .errors import AbicheckError

if TYPE_CHECKING:
    pass


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
    expand_header_inputs: object,
    populate_dependency_info: object,
    stamp_provenance: object,
    write_snapshot_output: object,
) -> None:
    """Run the ELF dump pipeline and write output.

    All helper callables (expand_header_inputs, populate_dependency_info,
    stamp_provenance, write_snapshot_output) are passed in from cli.py to avoid
    an import cycle — cli_dump_helpers must not import from cli.
    """
    from collections.abc import Callable
    from typing import cast

    _expand = cast("Callable[[list[Path]], list[Path]]", expand_header_inputs)
    _populate = cast("Callable[..., None]", populate_dependency_info)
    _stamp = cast("Callable[..., None]", stamp_provenance)
    _write = cast("Callable[..., None]", write_snapshot_output)

    compiler = "cc" if lang == "c" else "c++"
    resolved_headers = _expand(list(headers)) if headers else []
    try:
        snap = dump(
            so_path=so_path,
            headers=resolved_headers,
            extra_includes=list(includes),
            version=version,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=effective_gcc_options,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang if lang == "c" else None,
            dwarf_only=dwarf_only,
            debug_format=effective_debug_format,
            public_headers=list(public_headers),
            public_header_dirs=list(public_header_dirs),
        )
    except (AbicheckError, RuntimeError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    # Record that the header AST was parsed with the real build context (ADR-029)
    if effective_compile_db and resolved_headers:
        snap.parsed_with_build_context = True

    if follow_deps:
        _populate(snap, so_path, list(search_paths), sysroot, ld_library_path)

    _stamp(snap, git_tag=git_tag, build_id=build_id, no_git=no_git)
    _write(snap, output, build_info, sources, build_config, allow_build_query, collect_mode)
