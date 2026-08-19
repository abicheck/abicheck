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

Two further real call-site reachability gaps (Codex review, fresh evidence),
both closed here: (1) both real call sites require ``sources``/``build_info``
in the first place -- ``l2_seed``'s own guard is
``(sources is None and build_info is None) or not headers``, and
``_write_snapshot_output`` never calls ``embed_build_source`` at all without
one of them -- so a bare ``--build-query`` with neither given can never
reach the query regardless of collect mode/headers. (2) a ``--build-info``
that is a ``BuildSourcePack`` directory (``is_pack_dir``) is folded into
``collect_inline_pack``'s ``base_build`` *before* ``_resolve_compile_db`` is
even considered (``l2_seed._l2_seed_pack_inputs``/
``cli_buildsource.embed_build_source``'s own ``base_build=bi_pack.
build_evidence``) -- when that pack already carries L3 compile units,
``collect_inline_pack`` skips ``_resolve_compile_db`` (and therefore
``cfg.query``) entirely, which the plain-file/dir ``_compile_db_at`` check
above does not catch (a pack directory has no top-level
``compile_commands.json`` for it to find). A pack that carries no compile
units (e.g. a source_abi-only pack) does *not* short-circuit this way, since
``raw_build_info`` becomes ``None`` once identified as a pack -- resolution
falls through to ``cfg.query`` exactly as if no ``--build-info`` were given
at all, so this check does not report "will NOT run" for that case.

Two more real call-site precedence gaps (Codex review, fresh evidence),
closing what is now the exhaustive set of ways ``collect_inline_pack``
bypasses ``_resolve_compile_db``: (3) a ``--build-info`` file that
``sniff_build_info_format`` recognizes as a pre-captured Bazel aquery/cquery
jsonproto is routed to ``_maybe_collect_bazel_build_info`` *before*
``_resolve_compile_db`` is ever reached (and, once recognized, always
returns ``True`` regardless of how many compile units the capture yields --
see that function's own docstring) -- ``_compile_db_at`` cannot see this
either, since the file is not a compile-commands array. (4) a ``--sources``
tree that is itself a pack directory folds into ``base_build`` the identical
way a ``--build-info`` pack does (``l2_seed._l2_seed_pack_inputs``), but
**only when no ``--build-info`` was also given** -- an explicit
``--build-info`` always wins L3 over a ``--sources`` pack (Codex review:
"a raw --build-info must still be resolved... not skipped by folding the
pack into base_build", `_l2_seed_pack_inputs`'s own docstring). Unlike the
``--build-info``-is-a-pack case, an empty ``--sources`` pack does *not* make
this module's own "will NOT run" guard above fire either, since the
original (pre-transform) ``sources``/``build_info`` values it reads stay
non-``None`` -- resolution still reaches ``cfg.query`` exactly as coded.

Two final refinements (Codex review, fresh evidence): (5) an empty ``--sources``/
``--build-info`` pack (no L3 compile units) does not by itself rule out
``embed_build_source`` -- but when it is a **``--build-info``** pack with no
``--sources`` given, or a **``--sources``** pack (build_info is ``None`` in
that branch), ``embed_build_source``'s own ``raw_build_info``/``raw_sources``
both collapse to ``None`` *unconditionally* (independent of collect mode),
so its dispatch guard (`raw_build_info is not None or raw_sources is not
None`) always fails -- leaving only the L2-seed path, which itself still
needs ``headers``. (6) ``shlex.split()`` on a whitespace-only ``build.query``
returns an empty list; ``_run_build_query`` itself checks ``if not argv:
return None`` before ever invoking anything, so this module now reports the
same "will NOT run" rather than an execution claim with an empty ``argv:
[]``.

This closes the full set of paths into ``collect_inline_pack`` this module
is aware of; a new bypass mechanism added to that function in the future
would need a matching addition here, the same way each of these did.
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
        is_pack_dir,
        load_build_config,
    )

    # Neither real call site is even attempted without --sources/--build-info
    # at all -- `l2_seed`'s own guard is `(sources is None and build_info is
    # None) or not headers`, and `_write_snapshot_output` never calls
    # `embed_build_source` without one of them either (Codex review, fresh
    # evidence).
    if sources is None and build_info is None:
        result.add(
            _SECTION,
            "build.query: will NOT run -- neither --sources nor --build-info "
            "was given, so no build-evidence collection is attempted at all",
        )
        return

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

    if build_info is not None and is_pack_dir(build_info):
        # `_l2_seed_pack_inputs`/`embed_build_source`'s own `base_build=
        # bi_pack.build_evidence` fold a --build-info pack's own L3 compile
        # units in *before* _resolve_compile_db is even considered --
        # collect_inline_pack skips it entirely once merged.compile_units is
        # non-empty (Codex review, fresh evidence). A pack with no compile
        # units at all does not short-circuit this way (raw_build_info
        # becomes None, same as if --build-info were absent), so this only
        # reports "will NOT run" when the pack actually carries L3 evidence.
        from .buildsource.pack import BuildSourcePack

        try:
            pack_evidence = BuildSourcePack.load(build_info).build_evidence
        except (OSError, ValueError) as exc:
            result.add(
                _SECTION, f"build.query: could not load --build-info pack {build_info}: {exc}"
            )
            return
        if pack_evidence is not None and pack_evidence.compile_units:
            result.add(
                _SECTION,
                f"build.query: will NOT run -- --build-info ({build_info}) is "
                "a pack that already carries L3 compile units, which take "
                "precedence over build.query",
            )
            return
        if sources is None and not headers:
            # embed_build_source's own raw_build_info becomes None once
            # --build-info is a pack (regardless of collect mode), and
            # raw_sources is already None with no --sources given -- its
            # dispatch condition (`raw_build_info is not None or raw_sources
            # is not None`) therefore fails unconditionally, leaving only the
            # L2 seed path, which itself needs headers (Codex review, fresh
            # evidence).
            result.add(
                _SECTION,
                "build.query: will NOT run -- --build-info is a pack with no "
                "L3 compile units, and neither --sources nor headers give "
                "another path to collect_inline_pack",
            )
            return
    elif build_info is not None:
        # A pre-captured Bazel aquery/cquery jsonproto is routed to the
        # adapter before _resolve_compile_db is ever reached, and always
        # bypasses it once recognized -- regardless of how many compile
        # units the capture itself yields (Codex review, fresh evidence).
        # sniff_build_info_format never executes anything (its own
        # docstring), matching this module's read-only contract.
        from .buildsource.inline import sniff_build_info_format

        if build_info.is_file() and sniff_build_info_format(build_info) in (
            "bazel_aquery",
            "bazel_cquery",
        ):
            result.add(
                _SECTION,
                f"build.query: will NOT run -- --build-info ({build_info}) is "
                "a pre-captured Bazel aquery/cquery jsonproto, which takes "
                "precedence over build.query",
            )
            return
        # `_resolve_compile_db`'s own first branch: an explicit --build-info
        # that already resolves to a real compile database is returned
        # immediately -- cfg.query, trusted or not, is never even consulted
        # (Codex review).
        found = _compile_db_at(build_info)
        if found is not None:
            result.add(
                _SECTION,
                f"build.query: will NOT run -- --build-info already resolves "
                f"to a compile database ({found}), which takes precedence "
                "over build.query",
            )
            return
    elif sources is not None and is_pack_dir(sources):
        # `_l2_seed_pack_inputs` folds a --sources pack into base_build the
        # identical way a --build-info pack does, but only when no
        # --build-info was also given (an explicit --build-info always wins
        # L3 over a --sources pack) -- reached only in this elif branch,
        # since build_info is None here (Codex review, fresh evidence).
        from .buildsource.pack import BuildSourcePack

        try:
            pack_evidence = BuildSourcePack.load(sources).build_evidence
        except (OSError, ValueError) as exc:
            result.add(_SECTION, f"build.query: could not load --sources pack {sources}: {exc}")
            return
        if pack_evidence is not None and pack_evidence.compile_units:
            result.add(
                _SECTION,
                f"build.query: will NOT run -- --sources ({sources}) is a "
                "pack that already carries L3 compile units, which take "
                "precedence over build.query",
            )
            return
        if not headers:
            # build_info is None in this branch (elif chain), so
            # embed_build_source's raw_build_info is already None; raw_sources
            # becomes None too once --sources is a pack, unconditionally --
            # its dispatch condition fails regardless of collect mode, leaving
            # only the L2 seed path, which itself needs headers (Codex
            # review, fresh evidence).
            result.add(
                _SECTION,
                "build.query: will NOT run -- --sources is a pack with no L3 "
                "compile units and no headers give another path to "
                "collect_inline_pack",
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
    if not argv:
        # `_run_build_query`'s own `if not argv: return None` -- a
        # whitespace-only query parses to an empty argv and is never
        # actually run (Codex review, fresh evidence).
        result.add(
            _SECTION,
            f"build.query: {effective_query!r} -- will NOT run "
            "(parses to an empty command)",
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
