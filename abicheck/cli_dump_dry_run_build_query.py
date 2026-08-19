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

"""``dump --dry-run``'s ``build.query`` trust/execution report.

Split into its own leaf module rather than added inline to
``cli_dump_helpers.py``, which sits at its 2000-line AI-readiness hard cap
(root ``AGENTS.md`` "Files that are large" already flags it).

Closes CLI cleanup phase two's PR 3C prerequisite 3
(``docs/contribute/plans/cli-cleanup-phase-two.md``): "``dump --dry-run``
prints the exact argv, the cwd, the resulting compile-DB path, and why the
query will or will not run." Prerequisites 1 and 2 (only an explicit
``--config``/``--build-query`` authorizes executing ``build.query``; an
auto-discovered ``.abicheck.yml`` never does) were already implemented
(ADR-032 D5) -- this module only adds the missing dry-run *visibility* into
that already-enforced trust decision, mirroring it read-only rather than
re-deciding it. It never invokes the query -- resolution only, matching
every other ``dump --dry-run`` section's contract.

The trust rule mirrored here is ``cli_buildsource.embed_build_source``'s own
(``cfg_trusted_for_query = build_config is not None or build_query is not
None``) and the command/cwd construction is ``buildsource.inline.
_run_build_query``'s own (``shlex.split(cfg.query)``, cwd = the ``--sources``
tree when it is a directory) -- kept in sync by reading, not importing,
since neither of those is a public, dry-run-safe entry point. The
``--build-info``-takes-precedence check mirrors ``buildsource.inline.
_resolve_compile_db``'s own first branch, which returns *before ever looking
at* ``cfg.query`` once ``--build-info`` already resolves to a real compile
database (Codex review, fresh evidence) -- reusing that module's own
``_compile_db_at`` (a pure stat/glob, read-only, no I/O beyond that) rather
than re-deriving the same resolution a second, potentially-diverging way;
other modules already reach into this same private helper across the module
boundary (e.g. ``service_scan.py``'s ``_find_compile_db_in_dir``).

The no-collection-requested check (Codex review, fresh evidence) mirrors the
precondition that must hold before *either* real call site can even reach
``_resolve_compile_db``. There are two independent paths in
(``cli_dump_helpers.perform_elf_dump``/``handle_non_elf_dump``):
``l2_seed.seed_includes_and_fold_compile_context`` (gated on ``headers`` being
non-empty -- it returns immediately otherwise) and
``cli_buildsource.embed_build_source`` (gated on ``collection_for_ci_mode
(collect_mode)`` returning a non-empty layer set -- empty only for
``collect_mode == "off"``, e.g. from ``--depth binary``, which also clears
``headers`` to ``()`` -- or ``--depth headers``, which resolves to
``collect_mode == "off"`` too but leaves ``headers`` alone). ``build.query``
can therefore still run under ``--depth headers`` (headers non-empty reaches
the L2-seed path, which only gates the *zero-config inferred* query on
``collect_mode``, never the explicit trusted ``cfg.query`` branch) -- only
the conjunction of both empty (``--depth binary``, or no headers given at
all with ``collect_mode == "off"``) rules out both call sites.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dry_run import DryRunResult

_SECTION = "Build query (trust)"


def add_build_query_dry_run_section(
    result: DryRunResult,
    *,
    sources: Path | None,
    headers: tuple[Path, ...],
    collect_mode: str,
    build_info: Path | None,
    build_config: Path | None,
    build_query: str | None,
    build_compile_db: str | None,
) -> None:
    """Append the ``build.query`` trust/execution report to *result*."""
    from .buildsource.inline import (
        _compile_db_at,
        discover_build_config,
        load_build_config,
    )

    # Neither `l2_seed.seed_includes_and_fold_compile_context` (needs headers)
    # nor `embed_build_source` (needs a non-"off" collect mode) would even
    # call `collect_inline_pack`/`_resolve_compile_db` -- build.query, trusted
    # or not, is never reached (Codex review, fresh evidence).
    if not headers and collect_mode == "off":
        result.add(
            _SECTION,
            "build.query: will NOT run -- no evidence collection requested "
            f"(collect mode {collect_mode!r} with no headers to parse)",
        )
        return

    # `_resolve_compile_db`'s own first branch: an explicit --build-info that
    # already resolves to a real compile database is returned immediately --
    # cfg.query, trusted or not, is never even consulted (Codex review).
    if build_info is not None:
        found = _compile_db_at(build_info)
        if found is not None:
            result.add(
                _SECTION,
                f"build.query: will NOT run -- --build-info already resolves "
                f"to a compile database ({found}), which takes precedence "
                "over build.query",
            )
            return

    # Same source (source-tree-root-only, no upward walk) `embed_build_source`
    # itself resolves from for this purpose -- distinct from `discover_project_
    # config`'s upward walk, which the rest of this dry-run report already uses
    # for the generic ".abicheck.yml:" info line.
    cfg_path = build_config or discover_build_config(sources)
    trusted = build_config is not None or build_query is not None

    # The real path (`cli_buildsource.py`) always loads *cfg_path* when one is
    # found, whether or not the CLI already supplied --build-query -- an
    # explicit --build-query overrides only `cfg.query`, never `cfg.
    # compile_db` (Codex review: an earlier version of this function skipped
    # loading the config entirely once a CLI query was given, silently
    # dropping the config's own build.compile_db hint).
    cfg_compile_db: str | None = None
    if cfg_path is not None:
        try:
            cfg = load_build_config(cfg_path)
        except ValueError as exc:
            result.add(_SECTION, f"build.query: could not load {cfg_path}: {exc}")
            return
        cfg_compile_db = cfg.compile_db or None
        effective_query = build_query if build_query is not None else (cfg.query or None)
    else:
        effective_query = build_query
    compile_db_hint = build_compile_db if build_compile_db is not None else cfg_compile_db

    if not effective_query:
        result.add(_SECTION, "build.query: (none configured)")
        return

    if not trusted:
        result.add(
            _SECTION,
            f"build.query: {effective_query!r} -- will NOT run "
            "(sourced from an auto-discovered .abicheck.yml, which is never "
            "trusted to execute; pass --config to authorize it)",
        )
        return

    try:
        argv = shlex.split(effective_query)
    except ValueError as exc:
        result.add(
            _SECTION,
            f"build.query: {effective_query!r} -- will NOT run "
            f"(could not parse as a command: {exc})",
        )
        return

    trust_source = "explicit --config" if build_config is not None else "explicit --build-query"
    cwd = sources if sources is not None and sources.is_dir() else Path.cwd()
    result.add(
        _SECTION,
        f"build.query: will run (trusted -- {trust_source})",
        f"argv: {argv}",
        f"cwd: {cwd}",
        f"resulting compile-DB path: {compile_db_hint}"
        if compile_db_hint
        else "resulting compile-DB path: (build.compile_db not configured -- "
        "the query's own default output location)",
    )
