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

A seventh refinement (Codex review, fresh evidence): (7) once resolution
falls through the pack-precedence checks above with ``--sources`` itself a
pack (no compile units, headers present -- the case (5) above leaves
reachable), the query's own ``cwd`` must be derived from the same
normalized value ``collect_inline_pack`` actually receives
(``raw_sources``, nulled by ``_l2_seed_pack_inputs`` whenever ``--sources``
is a pack, unconditionally) -- not the original ``--sources`` pack
directory itself, which the real query never runs in.

An eighth refinement (Codex review, fresh evidence): (8) a recognized
``BuildSourcePack`` (``--build-info`` or ``--sources``) that fails to load
(a malformed manifest, an unreadable evidence file) now reports a
``DryRunResult.block()`` alongside its diagnostic, matching the real run's
own rejection -- ``cli_buildsource._load_pack_or_raise`` raises
``click.ClickException`` (nonzero exit) for the identical load failure, so
this dry run must not report exit ``0`` (a valid invocation) for an input
the real run would reject.

**Known, deliberately unclosed gaps** (documented rather than chased
further, per this repository's own "known gaps over risky reactive
patches" convention -- this module has now been through ten review
rounds):

- Flow-2 ``abicheck_inputs/`` packs (recognized by
  ``cli_buildsource_helpers._is_inputs_pack_dir``, a *different* recognizer
  from ``BuildSourcePack``'s own ``is_pack_dir``) fold into
  ``raw_build_info``/``raw_sources`` the identical way a ``BuildSourcePack``
  does in ``cli_buildsource.embed_build_source``, but this module does not
  detect that input shape at all -- a Flow-2 pack as the sole
  ``--build-info``/``--sources`` input, with no discoverable compile DB or
  headers, is still reported as "will run" when it would not. Closing this
  needs importing a second pack-format recognizer and a second,
  differently-shaped facts model (``InputsManifest``/``load_inputs_manifest``)
  rather than reusing anything ``BuildSourcePack``-shaped already handled
  above -- a genuinely separate input format, not a follow-up to the same
  mechanism.
- Every reachability check above reads the *raw*, unexpanded ``headers``
  tuple exactly as ``dump_cmd`` receives it from Click -- a directory entry
  counts as "headers present" even when ``_expand_header_inputs`` (called
  only inside the real, non-dry execution path, never before the
  ``--dry-run`` branch) would later find it empty and raise
  ``click.ClickException`` before either query call site is ever reached.
  This predates this module: `render_dump_dry_run` itself has never
  expanded ``-H`` directories for validation purposes, for the "Available
  data layers"/depth-feasibility sections either -- this module only
  inherits and extends that same pre-existing blind spot into a new false
  "will run" claim. Closing it needs a design decision about whether
  ``--dry-run`` should perform real directory-walk validation at all (a
  broader change to `render_dump_dry_run`'s own established "cheap,
  read-only resolution... no I/O beyond stat()/PATH lookups" contract),
  not a scoped fix to this module alone.

This closes the full set of ``BuildSourcePack``-shaped paths into
``collect_inline_pack`` this module is aware of (the two gaps above
excepted); a new bypass mechanism added to that function in the future
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
    so_path: Path | None = None,
    dump_manifest_given: bool = False,
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

    # `dump_cmd`'s own dispatch rejects --dump-manifest for a PE/Mach-O
    # binary outright (`--dump-manifest is not yet supported for {fmt}
    # binaries`, ADR-050 D3, a `click.UsageError`) before `handle_non_elf_
    # dump`/`embed_build_source` is ever reached -- so this combination can
    # never run build.query regardless of how the rest of this function
    # would otherwise resolve it (Codex review, fresh evidence). Detected
    # the same cheap, read-only way `render_dump_dry_run`'s own "Available
    # data layers" section already does (`normalize_binary_input`/
    # `detect_binary_format`), matching this module's contract.
    if dump_manifest_given and so_path is not None:
        from .binary_utils import detect_binary_format, normalize_binary_input

        try:
            _normalized_path, _binary_fmt = normalize_binary_input(so_path)
            if _binary_fmt is None:
                _binary_fmt = detect_binary_format(_normalized_path)
        except Exception:
            _binary_fmt = None
        if _binary_fmt in ("pe", "macho"):
            # `dump_cmd`'s own real (non-dry) rejection is a
            # `click.UsageError` (exit 64), not a `ClickException`/exit 1 --
            # raised directly here, matching this module's own documented
            # exit-64 contract, rather than encoded via `result.block()`
            # (Codex review, fresh evidence: an earlier revision used
            # `result.block()`, producing the wrong exit-code class).
            import click

            raise click.UsageError(
                f"--dump-manifest is not yet supported for {_binary_fmt.upper()} "
                "binaries (ADR-050 D3); use a single-header dump for this format."
            )

    # An *explicit* --config is validated unconditionally by `dump_cmd`
    # itself, before this function is ever called: `resolve_dump_compile_
    # context()`/`cli_options.merge_compile_config()` -- the L2 compile-
    # context resolver every `dump` invocation runs, regardless of build-
    # source collection, and unconditionally before the `--dry-run` branch
    # -- already raises `click.UsageError` for a malformed *explicit*
    # config, so a malformed explicit --config never reaches this function
    # at all. Verified end-to-end: `dump ... --config bad.yml --depth
    # binary` with no --sources/--build-info exits 64 before ever printing
    # a dry-run report (CodeRabbit/Codex review, fresh evidence -- this
    # function does not need its own duplicate check). This is unlike an
    # *auto-discovered* config, which that same resolver only warns about
    # and falls back from -- handled further below, gated on whether
    # `embed_build_source`'s own, stricter load is actually reached.

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

    # `embed_build_source`'s own pack loading (`_load_pack_or_raise`, a
    # `click.ClickException`, exit 1) and its own auto-discovered-config
    # load (further below, a `click.UsageError`, exit 64) are both reached
    # only *past* its own `if not layers: return` gate
    # (`collection_for_ci_mode(collect_mode)` returning no layers for an
    # "off" collect mode) -- under a collect mode that resolves to no
    # layers (e.g. `--depth headers`, which still leaves `headers`
    # non-empty and so does not return at the guard above),
    # `embed_build_source` is never called into far enough to load either,
    # and `l2_seed`'s own independent pack/config loading (reached via the
    # headers-gated L2-seed path instead) degrades any load failure to a
    # silent no-op (no seeded dirs, no fold, no query attempt) rather than
    # raising through. Verified end-to-end against a real compiled library:
    # a malformed `--sources` pack, and separately a malformed
    # auto-discovered config, both exit 0 under `--depth headers` -- the
    # dump simply proceeds without L3 seeding -- while the identical inputs
    # under the default (non-"off") collect mode exit 1/64 respectively
    # (Codex review, fresh evidence). This module therefore only
    # raises/blocks for a malformed pack or auto-discovered config when
    # `collect_mode != "off"`.
    collect_active = collect_mode != "off"

    # `embed_build_source` loads `bi_pack`/`src_pack` unconditionally and
    # independently of one another (`_load_pack_or_raise(build_info)` and
    # `_load_pack_or_raise(sources)`, both called regardless of what the
    # other operand is) -- a malformed --sources pack blocks the real run
    # even when an explicit, non-pack --build-info would otherwise take L3
    # precedence over it below. Checked here, unconditionally, rather than
    # only inside the --build-info-is-absent-or-non-pack branch further
    # down, since that branch is an `elif` a non-pack --build-info would
    # otherwise skip entirely (Codex review, fresh evidence).
    if sources is not None and is_pack_dir(sources):
        from .buildsource.pack import BuildSourcePack

        try:
            src_pack_evidence = BuildSourcePack.load(sources).build_evidence
        except (OSError, ValueError) as exc:
            if collect_active:
                result.add(
                    _SECTION, f"build.query: could not load --sources pack {sources}: {exc}"
                )
                result.block(f"--sources names an unloadable pack ({sources}): {exc}")
            else:
                result.add(
                    _SECTION,
                    f"build.query: will NOT run -- --sources names an "
                    f"unloadable pack ({sources}: {exc}), and collect mode "
                    f"{collect_mode!r} means only the best-effort L2 seed "
                    "path (which silently degrades to no L3 evidence on a "
                    "load failure, rather than raising) could otherwise "
                    "reach it",
                )
            return
    else:
        src_pack_evidence = None

    if build_info is not None and is_pack_dir(build_info):
        from .buildsource.pack import BuildSourcePack

        try:
            bi_pack_evidence = BuildSourcePack.load(build_info).build_evidence
        except (OSError, ValueError) as exc:
            # The real (non-dry) run rejects this identically -- `cli_
            # buildsource._load_pack_or_raise` raises `click.ClickException`
            # (exit 1) for the same load failure -- so a dry run reporting
            # exit 0 here would claim a broken invocation is valid (Codex
            # review, fresh evidence) -- but only when `collect_active`
            # (see the note above this precedence chain): under an "off"
            # collect mode this load is never reached by the real run at all.
            if collect_active:
                result.add(
                    _SECTION,
                    f"build.query: could not load --build-info pack {build_info}: {exc}",
                )
                result.block(f"--build-info names an unloadable pack ({build_info}): {exc}")
            else:
                result.add(
                    _SECTION,
                    f"build.query: will NOT run -- --build-info names an "
                    f"unloadable pack ({build_info}: {exc}), and collect mode "
                    f"{collect_mode!r} means only the best-effort L2 seed "
                    "path (which silently degrades to no L3 evidence on a "
                    "load failure, rather than raising) could otherwise "
                    "reach it",
                )
            return
    else:
        bi_pack_evidence = None

    # `_l2_seed_pack_inputs` nulls `raw_sources` whenever --sources is itself
    # a pack directory, unconditionally (independent of --build-info) -- both
    # config auto-discovery and the query's own cwd must use that same
    # normalized value, not the pack directory itself (Codex review, fresh
    # evidence).
    effective_sources = None if (sources is not None and is_pack_dir(sources)) else sources

    # `embed_build_source`'s own `raw_build_info`/`raw_sources` -- non-None
    # only for a *non-pack* operand -- gate whether it loads and validates
    # `cfg_path` **at all**, and it does so *before* ever calling
    # `collect_inline_pack`/`_resolve_compile_db` (the precedence questions
    # the branches below answer) -- so config validation must happen here,
    # ahead of every "will NOT run because X takes precedence" branch below,
    # not after them (Codex review, fresh evidence: an earlier revision
    # validated config only after these precedence checks had already
    # returned, so a malformed auto-discovered config combined with e.g. an
    # already-resolved --build-info compile database never got validated at
    # all -- verified end-to-end that the real run still raises for that
    # exact combination).
    config_reachable = (build_info is not None and not is_pack_dir(build_info)) or (
        effective_sources is not None
    )

    # Same source (source-tree-root-only, no upward walk) `embed_build_source`
    # itself resolves from for this purpose -- distinct from `discover_project_
    # config`'s upward walk, which the rest of this dry-run report already uses
    # for the generic ".abicheck.yml:" info line.
    cfg_path = build_config or discover_build_config(effective_sources)
    trusted = build_config is not None or build_query is not None

    # The real path (`cli_buildsource.py`) always loads *cfg_path* when one is
    # found, whether or not the CLI already supplied --build-query -- an
    # explicit --build-query overrides only `cfg.query`, never `cfg.
    # compile_db` (Codex review: an earlier version of this function skipped
    # loading the config entirely once a CLI query was given, silently
    # dropping the config's own build.compile_db hint).
    cfg = None
    cfg_compile_db: str | None = None
    if cfg_path is not None and config_reachable:
        try:
            cfg = load_build_config(cfg_path)
        except ValueError as exc:
            # `build_config is None` here -- the explicit-config case
            # already raised, unconditionally, at the very top of this
            # function. This is therefore always an *auto-discovered*
            # config, which `embed_build_source` validates strictly (a
            # `click.UsageError`, exit 64) only past its own collect-mode
            # gate -- the identical `collect_active` reachability this
            # precedence chain's pack-load checks above already gate on
            # (CodeRabbit/Codex review, fresh evidence; verified end-to-end:
            # a malformed auto-discovered config exits 0, warn-only, under
            # `--depth headers`, but exits 64 under the default collect
            # mode). Raised directly rather than encoded via
            # `result.block()`, matching this module's documented exit-64
            # contract, same as the explicit-config case above.
            if collect_active:
                import click

                raise click.UsageError(f"cannot parse build config {cfg_path}: {exc}") from exc
            result.add(_SECTION, f"build.query: could not load {cfg_path}: {exc}")
            result.add(
                _SECTION,
                "build.query: will NOT run -- the auto-discovered config "
                f"failed to load, and collect mode {collect_mode!r} means "
                "only the best-effort L2 seed path (which silently "
                "degrades on a load failure, rather than raising) could "
                "otherwise reach it",
            )
            return
        cfg_compile_db = cfg.compile_db or None

    # NOW the "does the query actually get reached" precedence chain --
    # unaffected by config validation above, since these branches answer
    # whether `collect_inline_pack`/`_resolve_compile_db` would even look at
    # `cfg.query` given the operands' own shapes.
    if build_info is not None and is_pack_dir(build_info):
        # `_l2_seed_pack_inputs`/`embed_build_source`'s own `base_build=
        # bi_pack.build_evidence` fold a --build-info pack's own L3 compile
        # units in *before* _resolve_compile_db is even considered --
        # collect_inline_pack skips it entirely once merged.compile_units is
        # non-empty (Codex review, fresh evidence). A pack with no compile
        # units at all does not short-circuit this way (raw_build_info
        # becomes None, same as if --build-info were absent), so this only
        # reports "will NOT run" when the pack actually carries L3 evidence.
        if bi_pack_evidence is not None and bi_pack_evidence.compile_units:
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
        if src_pack_evidence is not None and src_pack_evidence.compile_units:
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

    effective_query = build_query if build_query is not None else (cfg.query if cfg else None)
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
    cwd = effective_sources if effective_sources is not None and effective_sources.is_dir() else Path.cwd()
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
