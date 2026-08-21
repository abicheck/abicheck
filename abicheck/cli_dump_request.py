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

**Scope, stated plainly**: the real ELF/PE/Mach-O run still executes through
``perform_elf_dump``/``handle_non_elf_dump``, not through
``service_dump_pipeline.execute_dump_request``. Three separate obstacles block
that half (post-processing passes driven by CLI-only inputs, the write-time
vs. resolve-time embed, and the source-only branch's own pipeline); see the
plan's PR 3A section. This module is the prerequisite that migration needs,
consumed today by ``--dry-run``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from .api_types import DumpRequest
    from .compile_context import CompileContext
    from .service_dump_pipeline import ResolvedDumpRequest

__all__ = ["build_dump_request", "resolve_dump_request_for_cli"]


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
    )


def resolve_dump_request_for_cli(request: DumpRequest) -> ResolvedDumpRequest:
    """:func:`~abicheck.service_dump_pipeline.resolve_dump_request`, with the
    CLI's error contract.

    The Tier-2 pipeline signals a bad request as
    :class:`~abicheck.errors.ValidationError`; a Click front end owes the user
    a ``UsageError`` (exit 64) instead. Translated here, at the boundary,
    rather than inside the pipeline — the same Tier-1/Tier-2 separation
    ``embed_side_build_source`` observes in the other direction.

    Worth being explicit about what this can newly reject on the ``--dry-run``
    path, since that path's own contract is "never raises on anything but a
    usage error": :meth:`DumpRequest.validate` front-runs a check
    ``dumper.dump()`` already performs at *runtime* — a ``--dump-manifest``
    combined with ``-I``/``--include``, which declares two conflicting public
    surfaces. That combination previously dry-ran as a clean report and then
    failed during the real extraction. Reporting it as a usage error in both
    places is a strictly better answer, and it stays inside the dry-run
    contract because it is precisely a usage error.
    """
    import click

    from .errors import ValidationError
    from .service_dump_pipeline import resolve_dump_request

    try:
        return resolve_dump_request(request)
    except ValidationError as exc:
        raise click.UsageError(str(exc)) from exc
