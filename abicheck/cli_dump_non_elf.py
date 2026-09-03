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

"""``handle_non_elf_dump`` (the PE/Mach-O ``dump`` CLI path), split out of
``cli_dump_helpers.py`` purely to stay under the AI-readiness 2000-line hard
cap -- the identical reason `resolve_dump_depth`/`resolve_dump_collect_context`
moved to `cli_dump_depth.py` (see that module's own docstring for the
precedent this follows). No behavior change: this is the same function body,
same signature, same call site (`cli.py`'s `dump_cmd`).

``_StampProvenance``/``_WriteSnapshotOutput`` are shared with
``perform_elf_dump`` (``cli_dump_helpers.py``); both modules import them from
the standalone leaf module ``cli_dump_protocols.py`` rather than one
importing them from the other -- see that module's own docstring for why
(importing them back from ``cli_dump_helpers.py`` would join this module to
the pre-existing CLI-registration import-cycle SCC for no structural reason).
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .cli_dump_protocols import (
    StampProvenance as _StampProvenance,
    WriteSnapshotOutput as _WriteSnapshotOutput,
)
from .errors import AbicheckError
from .workflows.artifact import ResolvedArtifactPlan
from .workflows.extraction import include_operand_dirs, suppress_streaming_prune

if TYPE_CHECKING:
    from .model import AbiSnapshot


def handle_non_elf_dump(
    so_path: Path,
    binary_fmt: str,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    version: str,
    lang: str,
    pdb_path: Path | None,
    follow_deps: bool,
    git_tag: str | None,
    build_id: str | None,
    no_git: bool,
    output: Path | None,
    dump_native_binary: Callable[..., AbiSnapshot],
    stamp_provenance: _StampProvenance,
    write_snapshot_output: _WriteSnapshotOutput,
    public_headers: tuple[Path, ...] = (),
    public_header_dirs: tuple[Path, ...] = (),
    build_info: Path | None = None,
    sources: Path | None = None,
    build_config: Path | None = None,
    allow_build_query: bool = False,
    collect_mode: str = "source-target",
    build_query: str | None = None,
    build_compile_db: str | None = None,
    build_targets: tuple[str, ...] = (),
    header_backend: str = "auto",
    compile_context: Any = None,
    inputs_pack: Path | None = None,
    depth: str | None = None,
    compile_db_context_matched: bool = False,
    include_dependencies: bool = False,
    snapshot_compression: str = "auto",
    lang_explicit: bool = False,
) -> None:
    """Handle the PE/Mach-O native dump path and output writing (split from cli.py).

    ``dump_native_binary``/``stamp_provenance``/``write_snapshot_output`` are all
    passed in from cli.py rather than imported, mirroring ``perform_elf_dump`` —
    the AST-based import-cycle gate counts *any* import (including a lazy
    function-body ``from .cli_resolve import …`` and a ``TYPE_CHECKING`` import),
    so importing them here would close a ``cli → cli_dump_non_elf → … → cli``
    cycle. ``compile_context`` is typed ``Any`` for the same reason (its concrete
    ``CompileContext`` lives in ``service_scan``).

    ``dump_native_binary`` (``_dump_native_binary`` → ``service.run_dump``)
    always attaches the header-only graph uniformly across ELF/PE/Mach-O
    (G29 Phase A: no longer flag-gated) — previously only the ELF
    ``perform_elf_dump`` path forwarded the opt-in flag, so ``dump
    --header-graph`` silently no-opped on PE/Mach-O input (Codex review);
    that gap can no longer occur now that the graph is unconditional.

    ``compile_db_context_matched`` (Codex review): whether cli.py's
    ``_resolve_build_context_flags(effective_compile_db, headers,
    compile_db_filter)`` found a compile-DB entry for these headers —
    computed there (before this function is even called) since that is the
    one place the -p/--compile-db load happens. Mirrors
    ``perform_elf_dump``'s identically-named parameter/stamp: without it, a
    -p compile database's flags never reached the PE/Mach-O header parse at
    all, and ``snap.parsed_with_build_context`` was never set here, so a
    ``dump foo.dll -H api.h -p build --depth build`` was wrongly rejected
    as only having reached "headers".
    """
    if follow_deps:
        click.echo(
            "Warning: --follow-deps is only supported for ELF binaries.", err=True
        )
    # L2 include fallback (parity with the ELF dump path): when -H headers are given
    # with --sources/--build-info but no explicit -I, seed the build's include dirs so
    # a PE/Mach-O header scope can resolve dependency headers instead of failing or
    # falling back to export-table mode (Codex review). collect_mode "off"
    # (--depth headers/binary) gates the executing inferred build query. dump has no
    # defer_cleanup channel, so temp-build-dir cleanups come back pending and run in
    # the finally, after the header parse has consumed the dirs.
    #
    # P0.3 L3->L2 fold (AGENTS.md "The native ELF `abicheck dump` path never
    # applies L3 build context..." known gap; PE/Mach-O shared the identical
    # gap) is combined into the SAME collection as the include seed above
    # (seed_includes_and_fold_compile_context, not two separate calls) --
    # two independent collect_inline_pack calls for the same --sources tree
    # could otherwise contend on the same inferred-build-query lock and wait
    # up to its 600s timeout (Codex review; see that function's own
    # docstring).
    # Phase 1 (dedup-and-convergence plan) Milestone A follow-up: mirrors
    # perform_elf_dump's identical migration from a hand-rolled
    # `_l2_pending_cleanups: list[...] = []` + manual
    # `if _l2_pending_cleanups: _run_cleanups(...)` finally block to the
    # shared, independently-tested `ResolvedArtifactPlan` primitive
    # (`workflows/artifact/contracts.py`) -- this was the one call site the plan's own
    # Phase 1 text named as still using the old pattern. Behavior-preserving
    # only: identical cleanup thunks, identical single-drain timing (the
    # `finally` below, the only place this function's original code drained
    # cleanups).
    from .workflows.extraction import seed_includes_and_fold_compile_context

    _artifact_plan = ResolvedArtifactPlan()
    try:
        (
            eff_includes,
            l3_context_applied,
            l3_effective_ctx,
            _l3_include_dirs,
        ) = seed_includes_and_fold_compile_context(
            headers=headers,
            includes=includes,
            sources=sources,
            build_info=build_info,
            build_config=build_config,
            build_query=build_query,
            build_compile_db=build_compile_db,
            build_targets=build_targets,
            collect_mode=collect_mode,
            gcc_path=getattr(compile_context, "gcc_path", None),
            gcc_prefix=getattr(compile_context, "gcc_prefix", None),
            gcc_options=getattr(compile_context, "gcc_options", None),
            gcc_option_tokens=getattr(compile_context, "gcc_option_tokens", ()),
            sysroot=getattr(compile_context, "sysroot", None),
            nostdinc=getattr(compile_context, "nostdinc", False),
            frontend=header_backend,
            frontend_context=getattr(compile_context, "frontend_context", "host"),
            lang=lang,
            lang_explicit=lang_explicit,
            pending_cleanups=_artifact_plan.pending_cleanups,
        )
        # _l3_include_dirs is unused by design, not omission: `compile=
        # l3_effective_ctx` below hands the merged L3 context to service_
        # header_scoped._try_header_scoped_dump, which derives the identical
        # cache-relevant paths from those tokens itself (Codex review, PR D).
        # `include_dependencies` (`--include-system-declarations`) suppresses
        # the opt-in streaming pruner here too, same reason as the identical
        # guard on `perform_elf_dump` above (Codex review, PR #840).
        with suppress_streaming_prune() if include_dependencies else nullcontext():
            snap = dump_native_binary(
                so_path,
                binary_fmt,
                list(headers),
                list(eff_includes),
                version,
                lang,
                lang_explicit=lang_explicit,
                pdb_path=pdb_path,
                public_headers=list(public_headers),
                public_header_dirs=list(public_header_dirs),
                header_backend=header_backend,
                compile=l3_effective_ctx,
                # Provenance widening gets ONLY the caller's own explicit -I
                # list, never `eff_includes` (same regression class the ELF
                # `perform_elf_dump` path already avoids) -- `compile_context`
                # is never reassigned on this PE/Mach-O path, so its own
                # `gcc_option_tokens` is already the caller's unmodified,
                # explicit set (CodeRabbit review).
                public_include_search_dirs=list(includes)
                + list(
                    include_operand_dirs(
                        getattr(compile_context, "gcc_option_tokens", ())
                    )
                ),
            )
    # A ClickException already carries its user-facing message; it must reach
    # Click as itself rather than be re-wrapped by the handler below.
    except click.ClickException:  # pylint: disable=try-except-raise
        raise
    except (AbicheckError, RuntimeError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        _artifact_plan.run_cleanups()
    # Record that the header AST was parsed with the real build context
    # (mirrors perform_elf_dump's identical stamp) -- gated on
    # compile_db_context_matched, not just headers' presence, for the same
    # reason perform_elf_dump's docstring gives: a syntactically valid but
    # empty/non-matching compile database must not be recorded as real
    # build-context evidence. Also gated on snap.from_headers: unlike the ELF
    # path, service._try_header_scoped_dump() can silently fall back to a
    # fresh export-table-only snapshot (scope_fallback set, from_headers
    # False) when the header backend fails or the parsed declarations don't
    # match any exported symbol (e.g. an MSVC-mangled C++ DLL parsed with a
    # mismatched compiler) -- the original *request* still had headers and a
    # matched compile DB, but the snapshot that was actually written never
    # used either, so stamping build-context evidence on it would let
    # `--depth build` accept a plain export-table dump (Codex review).
    # `l3_context_applied` (P0.3 fold, above) is an independent OR'd source
    # for this same stamp, mirroring perform_elf_dump's identical rule.
    if (
        headers
        and (compile_db_context_matched or l3_context_applied)
        and snap.from_headers
    ):
        snap.parsed_with_build_context = True
    stamp_provenance(snap, git_tag=git_tag, build_id=build_id, no_git=no_git)
    from .workflows.extraction import resolve_source_frontend_clang_bin

    write_snapshot_output(
        snap,
        output,
        build_info,
        sources,
        build_config,
        allow_build_query,
        collect_mode,
        build_query=build_query,
        build_compile_db=build_compile_db,
        build_targets=build_targets,
        extractor=header_backend,
        inputs_pack=inputs_pack,
        depth=depth,
        include_dependencies=include_dependencies,
        header_roots=tuple(headers) + tuple(public_headers) + tuple(public_header_dirs),
        clang_bin=resolve_source_frontend_clang_bin(
            getattr(compile_context, "gcc_path", None),
            getattr(compile_context, "gcc_prefix", None),
            exclude_cl_style=False,
        ),
        snapshot_compression=snapshot_compression,
        # L4 replay classifies declarations against these roots; with none it
        # classifies everything private and links nothing (measurement in
        # `_write_snapshot_output`'s own docstring).
        public_headers=tuple(public_headers),
        public_header_dirs=tuple(public_header_dirs),
    )
