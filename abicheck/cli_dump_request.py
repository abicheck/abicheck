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

"""``dump_cmd``'s CLI parameters, as one typed :class:`DumpRequest`.

CLI cleanup phase two, PR 3A blocker 5 (see
``docs/contribute/plans/cli-cleanup-phase-two.md``). ``dump_cmd`` never built
a ``DumpRequest`` anywhere, on either of its branches: it resolved
``collect_mode``/``header_backend``/``includes``/``gcc_option_tokens``
directly from raw Click parameters through two CLI-only helpers
(``cli_dump_helpers.resolve_dump_collect_context`` /
``resolve_dump_compile_context``) and fed those locals to *both* the
hand-written ``--dry-run`` renderer and the real ``perform_elf_dump`` call.
So ``--dry-run`` was a second implementation of resolution, not a dry pass of
the real one, with nothing but review discipline keeping the two in step.

This module is the one place those parameters become a request object.

**It is deliberately fed the CLI's own already-resolved values** — the
resolved :class:`CompileContext` and the resolved collect mode — rather than
re-deriving them from the raw flags. That is what makes the resolved object an
honest description of the run rather than a parallel opinion about it: the
plan's own warning is that "a preview built from one resolver describing an
execution built from another is worse than two hand-synced implementations,
since it *looks* authoritative without being connected to what actually
runs." Passing the real values in removes that gap for the fields the CLI
resolves, and
``tests/test_dump_request_from_cli.py`` pins the remaining ones — the fields
``service_dump_pipeline.resolve_dump_request`` derives independently
(``evidence.headers``, ``collect_mode``, ``header_backend``) — as equal to the
CLI's own locals across the invocation shapes that matter.

Its own module, not an addition to ``cli.py`` (1800+ lines, WARN) or
``cli_dump_helpers.py`` (at the 2000-line hard cap) — AGENTS.md, "Files that
are large".

**Scope, stated plainly**: the real run for both binary formats has since
migrated (CLI cleanup phase two, PR C — ELF first, PE/Mach-O following in
ADR-063 Phase 1) — ``frontends/cli/commands/dump.py`` builds a second,
execution-scoped ``ResolvedDumpRequest`` from the object this module
produces and calls ``frontends.cli.dump_execute``, which runs it through
``service_dump_pipeline.execute_dump_request`` instead of the retired
``perform_elf_dump``/``handle_non_elf_dump`` call sites (both deleted by
ADR-063 Track 1 once that migration left them with no production caller). The legacy ``-p``/``--compile-db`` auto-match this note
used to call a blocker is threaded through as an explicit pass-through
(``execute_dump_request(..., options=DumpExecutionOptions(
legacy_compile_db_tokens=..., legacy_compile_db_matched=...))`` — ADR-063
Track T4 folded this and the other eight out-of-band execution kwargs into
that one typed value) rather than a typed-``DumpRequest``-level field — see
``docs/contribute/known-gaps.md``'s "PR C" entry for the precise mechanism.
The PE/Mach-O half was verified only via mock-based CLI/unit tests, not a
real PE/Mach-O toolchain — none was available where this was done.
This module's object is consumed by both branches today: ``--dry-run``
renders it directly, and ``dump_cmd``'s real run (either format) builds the
execution-scoped ``ResolvedDumpRequest`` described above from it before
calling ``execute_dump_cli_run``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from .api_types import DumpRequest
    from .compile_context import CompileContext

__all__ = ["build_dump_request"]


def build_dump_request(
    *,
    so_path: Path | None,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    version: str,
    lang: str,
    lang_explicit: bool,
    header_backend: str,
    compile_context: CompileContext | None,
    frontend_context: str,
    depth: str | None,
    dwarf_only: bool,
    debug_format: str | None,
    pdb_path: Path | None,
    debug_roots: tuple[Path, ...],
    debuginfod: bool,
    debuginfod_url: str | None,
    dump_manifest: Any | None,
    sources: Path | None,
    build_info: Path | None,
    build_targets: tuple[str, ...],
    include_dependencies: bool,
    follow_deps: bool,
    search_paths: tuple[Path, ...],
    ld_library_path: str,
    include_labels: dict[Path, str] | None,
    resolved_collect_mode: str | None = None,
    compile_db_filter: str | None = None,
) -> DumpRequest:
    """One :class:`DumpRequest` describing this ``abicheck dump`` invocation.

    *so_path* may be ``None``: that is the source-only shape
    (``dump --sources ./tree`` with no ``SO_PATH``), which
    :attr:`~abicheck.api_types.InputSpec.path` can express since PR 3A
    blocker 5 widened it to ``Path | None``. ``DumpRequest.validate()``
    accepts it only alongside real ``sources``/``build_info``, matching
    ``cli_buildsource.dump_source_only``'s own "a bare dump errors clearly
    here".

    *compile_context* is the context ``resolve_dump_compile_context`` already
    produced (CLI flags folded over the project's ``.abicheck.yml``
    ``compile:`` block), passed through verbatim as the side's own
    ADR-055 D1 per-input override — never re-derived here.

    *header_backend* is likewise the already-resolved frontend (CLI over
    config). It is handed in as the request-level ``frontend`` so the request
    and the run cannot disagree about which backend was chosen; the same
    value also rides on *compile_context*, which is where
    ``service_compare_evidence.effective_frontend`` reads an explicit override
    from.

    *resolved_collect_mode* is the CLI's private ``_resolved_collect_mode``
    hook verbatim (Codex review) — set only when this invocation was itself
    driven by another already-resolved decision (``compare``'s implicit-dump
    path resolves collect mode from the *pair*, a materially different rule
    from a lone ``dump``'s own ``depth``-only default; see
    ``api_types.DumpRequest.resolved_collect_mode``'s own docstring). ``None``
    for an ordinary ``dump`` invocation, which lets
    ``resolve_dump_request()`` derive it from *depth* as usual.

    *compile_db_filter* is ``dump``'s own ``--compile-db-filter`` value,
    forwarded onto :attr:`~abicheck.api_types.InputSpec.compile_db_filter`
    verbatim (PR 3A investigation, 2026-08-21) so a ``--dry-run`` resolved
    from this request reports the same
    ``compile_db_filter_scope_error`` refusal ``dump_cmd`` already raises
    directly, and so the object records the filter that would actually
    narrow the header parse were the real run migrated onto it.
    """
    from .api_types import DumpRequest, InputSpec

    return DumpRequest(
        input=InputSpec(
            path=so_path,
            headers=tuple(headers),
            includes=tuple(includes),
            version=version,
            pdb=pdb_path,
            debug_roots=tuple(debug_roots),
            include_dependencies=include_dependencies,
            sources=sources,
            build_info=build_info,
            build_targets=tuple(build_targets),
            dump_manifest=dump_manifest,
            compile=compile_context,
            compile_db_filter=compile_db_filter,
        ),
        lang=lang,
        frontend=header_backend,
        depth=depth,
        dwarf_only=dwarf_only,
        debug_format=debug_format,
        enable_debuginfod=debuginfod,
        debuginfod_url=debuginfod_url,
        include_labels=tuple((include_labels or {}).items()),
        follow_dependencies=follow_deps,
        dependency_search_paths=tuple(search_paths),
        ld_library_path=ld_library_path,
        frontend_context=frontend_context,
        lang_explicit=lang_explicit,
        resolved_collect_mode=resolved_collect_mode,
    )


# `resolve_dump_request_for_cli` deliberately does NOT live here: it is the
# one place a Tier-1 CLI caller reaches into `service_dump_pipeline`
# (`resolve_dump_request`), and this module is otherwise a pure leaf (its
# only import is `.api_types`, function-local, for building the request
# object). Adding that edge here would join this module into the
# `cli`/`cli_buildsource`/`cli_dump_helpers`/`service_dump_pipeline`
# by-design SCC (`scripts/check_ai_readiness.py`'s `IMPORT_CYCLE_ALLOWLIST`)
# as a genuinely *new* member — real SCC growth, not a lateral join through
# already-member modules, since nothing else in this file reaches back into
# that cluster. It lives in `cli_buildsource.py` instead (already a member,
# already imports `service_dump_pipeline`-adjacent machinery, and is where
# `cli.py`'s `dump_cmd` already imports `_write_snapshot_output` from) — see
# that module's own `resolve_dump_request_for_cli` for the CLI
# error-contract translation. Found and fixed after a first attempt
# allowlisted the cycle instead (Codex review, AGENTS.md's own stated bar:
# a new allowlist entry needs an ADR or explicit maintainer sign-off, not an
# inline comment) — breaking the edge is strictly better than accepting it.
