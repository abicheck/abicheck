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
) -> None:
    """Run the ELF dump pipeline and write output.

    All helper callables (expand_header_inputs, populate_dependency_info,
    stamp_provenance, write_snapshot_output) are passed in from cli.py to avoid
    an import cycle — cli_dump_helpers must not import from cli.
    """
    compiler = "cc" if lang == "c" else "c++"
    resolved_headers = expand_header_inputs(list(headers)) if headers else []
    # P3: auto-add the public-header roots so a -H umbrella resolves its own
    # relative includes without a separate -I. These roots are *inferred*, not
    # user-chosen, so they must never outrank a real build context — and that
    # rules out -I: the preprocessor searches *all* -I dirs before *any*
    # -isystem dir regardless of command-line order, so a build context that
    # supplies generated/shim headers via -isystem (common for compile DBs)
    # would still lose to an inferred -I root (Codex review). Emit them as
    # -idirafter instead: that bucket is searched after both -I and -isystem
    # (so every build-context include wins) but still before the standard system
    # dirs, so the pure-L2/headers-only case — where there is no other search
    # path — still resolves the umbrella's relative includes. (The service/L2
    # path has no build context and keeps the roots on extra_includes.)
    from .header_utils import _implicit_header_includes

    eff_tokens = list(gcc_option_tokens)
    if resolved_headers:
        _user = {str(i.resolve()) for i in includes}
        for d in _implicit_header_includes(list(headers)):
            if str(d.resolve()) not in _user:
                eff_tokens += ["-idirafter", str(d)]
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
            gcc_option_tokens=tuple(eff_tokens),
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang if lang == "c" else None,
            dwarf_only=dwarf_only,
            debug_format=effective_debug_format,
            public_headers=list(public_headers),
            public_header_dirs=list(public_header_dirs),
            header_backend=header_backend,
        )
    except (AbicheckError, RuntimeError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    # Record that the header AST was parsed with the real build context (ADR-029)
    if effective_compile_db and resolved_headers:
        snap.parsed_with_build_context = True

    if follow_deps:
        populate_dependency_info(snap, so_path, list(search_paths), sysroot, ld_library_path)

    stamp_provenance(snap, git_tag=git_tag, build_id=build_id, no_git=no_git)
    write_snapshot_output(
        snap, output, build_info, sources, build_config, allow_build_query,
        collect_mode, build_query=build_query, build_compile_db=build_compile_db,
        extractor=header_backend,
    )
