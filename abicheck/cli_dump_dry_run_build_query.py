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
an explicit ``--config`` authorizes executing ``build.query``; an
auto-discovered ``.abicheck.yml`` never does) were already implemented
(ADR-032 D5) -- this module only adds the missing dry-run *visibility* into
that already-enforced trust decision, mirroring it read-only rather than
re-deciding it. It never invokes the query -- resolution only, matching
every other ``dump --dry-run`` section's contract.

The trust rule mirrored here is ``cli_buildsource.embed_build_source``'s own
(``cfg_trusted_for_query = build_config is not None`` -- a single term since
PR 3C removed ``--build-query``/``--build-compile-db``, so an explicit
``--config`` is the only authorizer) and the command/cwd construction is
``buildsource.inline.
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
``_resolve_compile_db``. There are two independent paths in (both reached
today from ``service_input_resolution._resolve_side_snapshot_impl``, and
from the retired ``perform_elf_dump``/``handle_non_elf_dump`` before it):
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
one of them -- so a configured query with neither given can never
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
patches" convention -- this module has now been through twenty-two review
rounds):

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
- The ``--build-info``-is-a-Bazel-jsonproto precedence check reuses
  ``sniff_build_info_format`` unmodified, whose own docstring documents a
  deliberate cost tradeoff: a JSON *array* is classified from a bounded
  head read, but a JSON *object* is fully ``json.load()``-ed, since "the
  discriminating key can sit far past the sniff window in a large aquery
  dump." For a very large pre-captured Bazel capture, this means a
  ``--dry-run`` invocation can spend real time/memory fully parsing that
  file just to decide query precedence (Codex review, fresh evidence) --
  in tension with this module's own "no I/O beyond stat()/PATH lookups"
  aspiration elsewhere. Not narrowed to a cheaper, bounded classifier
  here: doing so would mean a *second*, necessarily approximate
  implementation of the same classification the real (non-dry) run
  performs with this exact function, which is precisely the "re-deriving
  the same resolution a second, potentially-diverging way" this module's
  own docstring already rejects as a design principle for every other
  precedence check in this file (see the ``_compile_db_at`` reuse note
  above). Accepted as-is: correctness (matching the real run's actual
  classification) is kept, at the cost of dry-run's cheapness guarantee
  for this one, large-Bazel-capture input shape.

- **The underlying production double-execution this module now *reports*
  (see ``add_build_query_dry_run_section``'s own "RUNS AT LEAST ONCE..."
  notes) is itself not fixed here, deliberately.** Whether the query
  genuinely runs twice is subject to two independent, compounding sources
  of uncertainty this preview cannot resolve without actually running the
  real command: (1) whether the intervening dump even reaches build-source
  embedding at all -- the L2 seed's own invocation runs first, but
  ``embed_build_source`` (the second invocation) is only reached from
  ``_write_snapshot_output``, well after the primary header-AST parse, so
  a real `dump` with no `castxml`/AST frontend on `PATH` (or any other
  intervening failure) runs the query exactly once, via the L2 seed, before
  the parse fails and aborts the command -- verified empirically; and (2)
  whether a raw ``--build-info`` already short-circuits the second
  invocation, once reached -- ``_resolve_compile_db``'s own ``cfg.query``
  branch has no existing-file check before invoking it (unlike its sibling
  ``_compile_db_at``/glob branches), so with no raw ``--build-info`` given
  at all nothing can prevent the second invocation's query from running
  once reached, but with a raw ``--build-info`` given, whether the second
  invocation also runs the query is genuinely conditional on whether the
  first invocation's own query happened to write a compile DB at
  ``--build-info``'s exact path -- verified empirically both ways with two
  real compiled-library runs of the identical marker-appending query (one
  marker line when the query also wrote to ``--build-info``'s path, two
  when it did not, and one marker line again when no AST frontend was
  available regardless of ``--build-info``). This module reports "RUNS AT
  LEAST ONCE, AND AGAIN IF THE DUMP REACHES BUILD-SOURCE EMBEDDING" (no
  raw ``--build-info``) or "RUNS AT LEAST ONCE, POSSIBLY TWICE" (a raw
  ``--build-info`` given) rather than a false certainty in any direction;
  it cannot resolve either ambiguity without actually running the dump and
  the query, which it deliberately never does. This is a real
  correctness/idempotency issue in
  ``dump``'s own production execution, not a reporting gap in this
  read-only preview module -- fixing it means sharing or caching one
  resolved collection result across two currently-independent call sites
  in ``cli_dump_helpers.py``/``cli_buildsource.py``, each with its own
  scope/config-resolution nuances (the L2 seed call happens *before* the
  primary header-AST parse even runs; ``embed_build_source`` happens
  *after*, from ``_write_snapshot_output``) -- a real, cross-cutting change
  to `dump`'s own execution shape, not something this module can or should
  attempt on its own. This module's contribution is limited to what a
  read-only preview can honestly do: tell the operator the query is
  non-idempotent-unsafe for this input shape *before* they run it for
  real.

This closes the full set of ``BuildSourcePack``-shaped *and* Flow-2
``abicheck_inputs/``-shaped paths into ``collect_inline_pack``/the L2 seed
this module is aware of (the header-directory-validation gap above
excepted); a new bypass mechanism added to either path in the future would
need a matching addition here, the same way each of these did.

**Update: the Flow-2 gap above is closed.** It was originally two gaps, not
one: this module's own lack of Flow-2 recognition, *and* a real production
gap this investigation found in the L2-seed path itself --
``buildsource.l2_seed._l2_seed_pack_inputs`` (the pack-precedence resolver
``seed_includes_and_fold_compile_context`` uses) only ever recognized a
classic ``BuildSourcePack`` (``is_pack_dir``), never a Flow-2 pack, even
though ``embed_build_source`` already recognized both uniformly. A Flow-2
pack given as ``--sources``/``--build-info`` alongside ``-H`` headers was
therefore silently treated by the L2 seed as a literal, un-normalized
source tree -- its own compile-unit include dirs never reached L2 seeding,
and a trusted, explicit ``build.query``/``--config`` could genuinely be
re-executed against the pack directory itself. Fixed at the root
(``_l2_seed_pack_inputs`` now also checks ``_is_inputs_pack_dir``, folding
in a Flow-2 pack's ``BuildEvidence`` via the same lighter
``load_inputs_manifest``/``_load_build_evidence`` pair this module uses
below, rather than the full ``ingest_inputs_pack``), which is what makes
this module's own uniform ``_is_pack_dir_any``/``_pack_dir_build_evidence``
treatment of both call sites correct rather than merely convenient --
before that fix, mirroring ``embed_build_source``'s recognition here alone
would have made this preview *wrong* for the L2-seed-reachable branches
(reporting "will NOT run" for an input the L2 seed's own un-fixed
resolution would still have genuinely reached ``cfg.query`` through).
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .buildsource.build_evidence import BuildEvidence
    from .dry_run import DryRunResult

_SECTION = "Build query (trust)"


def _is_inputs_pack_dir(path: Path | None) -> bool:
    """Compatibility alias for ``buildsource.inputs_pack.is_inputs_pack_dir``.

    Owned there since ADR-061 Phase 3; this was the third of three copies of
    the same guard, each local because the original lived in the CLI layer.
    """
    from .workflows.extraction import is_inputs_pack_dir

    return is_inputs_pack_dir(path)


def _is_pack_dir_any(path: Path | None) -> bool:
    """True when *path* is either pack-directory shape the real resolvers
    fold in and null the corresponding ``raw_build_info``/``raw_sources``
    operand for -- a classic :class:`BuildSourcePack` (``is_pack_dir``) or a
    Flow-2 ``abicheck_inputs/`` pack (ADR-035 D5, ``_is_inputs_pack_dir``).

    Both ``embed_build_source`` and (since the fix this module's own
    docstring records) ``buildsource.l2_seed._l2_seed_pack_inputs`` treat
    both shapes identically for this purpose (``bi_is_pack or bi_is_inputs``
    / ``src_is_pack or src_is_inputs``, both unconditionally nulling the raw
    operand regardless of whether the pack carries any L3 evidence) -- so
    every reachability branch in this module that keys off "is this operand
    itself a pack" can safely recognize both the same way too.
    """
    from .workflows.extraction import is_pack_dir

    return is_pack_dir(path) or _is_inputs_pack_dir(path)


def _pack_dir_build_evidence(path: Path) -> BuildEvidence | None:
    """The ``BuildEvidence`` a pack directory at *path* would fold in.

    Mirrors whichever of the two real loaders the production pack-precedence
    resolvers use for *path*: a classic ``pack_io.load(path).
    build_evidence`` for the ``is_pack_dir`` shape, or -- for a Flow-2
    ``abicheck_inputs/`` pack -- ``validate_inputs_pack``'s hard validation
    followed by the lighter ``load_inputs_manifest`` + ``_load_build_
    evidence`` pair for the ``BuildEvidence`` itself, **not** the full
    ``ingest_inputs_pack`` (which additionally links a full L4
    ``SourceAbiSurface`` and folds the L5 graph -- real extra work this
    reachability question, "does this pack already carry L3 compile units",
    does not need). Raises the same way the real loaders do for a
    structurally malformed pack (``FileNotFoundError``/``ValueError``, or a
    broader shape-mismatch exception a malformed manifest can raise -- see
    the two call sites' own ``except Exception`` handling, which does not
    key on the exception's type), so both call sites can share one catch-all
    shape regardless of which pack kind was actually loaded.

    The Flow-2 branch's own hard validation (Codex review, fresh evidence)
    mirrors ``embed_build_source``'s own real ``_load_inputs_pack_or_raise``
    -> ``validate_inputs_pack`` -> raise-on-``report.errors`` order exactly:
    without it, a pack that is structurally readable (``is_inputs_pack``/
    ``load_inputs_manifest`` both succeed) but whose source facts fail
    validation (duplicate ``tu_id``s, target-id/fact-set-recipe/fact-set-
    identity errors) would have this function report a real ``BuildEvidence``
    and let the surrounding precedence chain conclude "will NOT run --
    already carries L3 compile units," even though the real
    ``embed_build_source`` call this pack reaches would raise
    ``click.ClickException`` (exit 1) before ever getting that far -- a dry
    run claiming a broken invocation is valid, which is exactly what this
    module elsewhere blocks for. This does mean the Flow-2 branch is no
    longer "no I/O beyond stat()/PATH lookups" (``validate_inputs_pack``
    itself reads every ``source_facts/*.jsonl`` file to check for duplicate
    ``tu_id``s and per-TU fact-set issues) -- an accepted correctness-over-
    cheapness tradeoff, the same one this module's own docstring already
    makes for a large pre-captured Bazel jsonproto. Deliberately **not**
    applied to the classic-``BuildSourcePack`` branch above: that shape has
    no equivalent separate "validate" step in production
    (``_load_pack_or_raise`` is just ``pack_io.load`` wrapped in a
    narrow except), so this function's existing load call already matches
    it exactly.
    """
    from .workflows.extraction import is_pack_dir, load_pack_or_raise

    if is_pack_dir(path):
        return load_pack_or_raise(path).build_evidence
    from .workflows.extraction import validate_inputs_pack

    report = validate_inputs_pack(path)
    if report.errors:
        raise ValueError(
            f"{len(report.errors)} validation error(s): " + "; ".join(report.errors)
        )
    from .workflows.extraction import _load_build_evidence, load_inputs_manifest

    manifest = load_inputs_manifest(path)
    return _load_build_evidence(path, manifest, [])


def _resolve_compile_db_hint_line(
    compile_db_hint: str, effective_sources: Path | None
) -> str:
    """The ``resulting compile-DB path:`` line for a configured hint.

    `_run_build_query`'s own resolution isn't a literal-string label --
    `cfg.compile_db`, glob-metacharacter-bearing or not, is resolved via
    `sorted(sources.glob(cfg.compile_db))` AFTER the query has run (first
    existing file wins), expecting the query to have (re)written it.
    `Path.glob()` treats a metacharacter-free pattern as an exact
    relative-path existence check, so a plain `build/compile_commands.json`
    hint resolves the identical way a real `build/*/compile_commands.json`
    glob does -- it is not printed verbatim as if it were already a path
    relative to *this process's* cwd; it is joined onto `sources` and
    checked for existence. An earlier revision special-cased "no glob
    metacharacters" as "unambiguous, print as-is," but that was wrong for
    the same reason a real glob is: whether the file already exists still
    needs checking, and the printed value must be the resolved path (or an
    explicit "not yet" note), never the bare configured string (Codex
    review, fresh evidence -- the common, glob-free
    `build.compile_db: build/compile_commands.json` case previously printed
    the literal string even when `--sources` was some other, unrelated
    directory).
    """
    try:
        existing_match = (
            next(
                (
                    m
                    for m in sorted(effective_sources.glob(compile_db_hint))
                    if m.is_file()
                ),
                None,
            )
            if effective_sources is not None
            else None
        )
    except (OSError, ValueError):
        existing_match = None
    except NotImplementedError:
        # `Path.glob()` rejects a non-relative (absolute) pattern outright
        # -- `_run_build_query`'s own identical `sources.glob(cfg.
        # compile_db)` call has the same, uncaught gap, so a real run
        # configuring an absolute `build.compile_db` would itself raise
        # this same unhandled exception once the query actually executes
        # (Codex review, fresh evidence). This module's own contract is
        # never to crash on a read-only preview, so this is reported as a
        # diagnostic instead of propagating -- but the note is honest
        # about what the real run would do, rather than silently
        # pretending the pattern degrades to "no match yet" like a
        # relative one would.
        return (
            f"resulting compile-DB path: (configured as {compile_db_hint!r} "
            "-- an absolute path; build.compile_db is documented as "
            "relative to --sources, and the real run's own identical glob "
            "resolution would raise NotImplementedError if this query "
            "ever executed)"
        )
    if existing_match is not None:
        # This is a PROVISIONAL match, not a resolved answer: `_run_build_
        # query` glob-resolves `cfg.compile_db` AFTER the query has run,
        # via `sorted(sources.glob(cfg.compile_db))` -- first *lexically
        # sorted* existing match wins, not "the same file this preview
        # found." A query that creates a lexicographically-earlier match
        # (or removes this one) makes the real run resolve to a genuinely
        # DIFFERENT path than this pre-query snapshot shows (Codex review,
        # fresh evidence) -- "recreate/refresh this file" undersold that:
        # it isn't only refreshed, it can be entirely superseded.
        return (
            f"resulting compile-DB path (provisional, pre-query snapshot): "
            f"{existing_match} (configured as {compile_db_hint!r}; the "
            "query runs BEFORE this glob is actually resolved for real, so "
            "it may create a lexicographically-earlier match, remove this "
            "one, or leave it unchanged -- the real run always re-resolves "
            "the configured glob fresh after the query exits, and can "
            "select a different file than this one)"
        )
    return (
        f"resulting compile-DB path: (configured as {compile_db_hint!r}, "
        "but no file matches it yet -- the exact path can only be "
        "known after the query runs and (re)writes it)"
    )


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
) -> None:
    """Append the ``build.query`` trust/execution report to *result*."""
    from .workflows.extraction import (
        _compile_db_at,
        discover_build_config,
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
        from .workflows.extraction import detect_binary_format, normalize_binary_input

        try:
            _normalized_path, _binary_fmt = normalize_binary_input(so_path)
            if _binary_fmt is None:
                _binary_fmt = detect_binary_format(_normalized_path)
        except (OSError, ValueError):
            # CodeRabbit nitpick: `normalize_binary_input`/`detect_binary_
            # format` already swallow `OSError` internally and never raise
            # `ValueError` -- this catch is therefore defensive rather than
            # reachable today -- but narrowing it still keeps an unexpected
            # programming error visible instead of silently degrading to
            # "unknown format", matching this repo's general convention
            # against bare `except Exception`.
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

    # `l2_seed.seed_includes_and_fold_compile_context` is only ever called
    # from the artifact-bearing dispatch (`perform_elf_dump`/
    # `handle_non_elf_dump`) -- when `SO_PATH` is omitted, `dump_cmd`
    # dispatches to `dump_source_only()` instead (the parallel-baseline
    # flow), which never seeds L2 at all: "this path's snapshot starts with
    # no functions/variables... a source-only dump has no -H headers
    # either" (its own docstring). So the L2-seed path is reachable only
    # when BOTH headers are present AND a real artifact was given -- headers
    # alone, with no `SO_PATH`, give this module no route to `collect_
    # inline_pack` the real `dump --sources ... -H ...` (no binary) run
    # doesn't also lack (Codex review, fresh evidence).
    l2_seed_reachable = bool(headers) and so_path is not None

    # Neither `l2_seed.seed_includes_and_fold_compile_context` (needs headers
    # AND a real artifact, per `l2_seed_reachable` above) nor `embed_build_
    # source` (needs a non-"off" collect mode) would even call
    # `collect_inline_pack`/`_resolve_compile_db` -- build.query, trusted or
    # not, is never reached (Codex review, fresh evidence).
    if not l2_seed_reachable and collect_mode == "off":
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
    if sources is not None and _is_pack_dir_any(sources):
        try:
            src_pack_evidence = _pack_dir_build_evidence(sources)
        except Exception as exc:  # noqa: BLE001 -- best-effort preview load; see
            # the note above this try (Codex review, fresh evidence): a
            # structurally malformed manifest (e.g. a JSON `null` where a
            # dict is expected -- `BuildSourceManifest.from_dict`'s
            # `dict(d.get("source_root", ...))` raises `TypeError`, not
            # `OSError`/`ValueError`, for that shape) previously escaped
            # this narrower catch entirely, crashing the whole `--dry-run`
            # invocation with a raw traceback instead of a report -- for a
            # `--depth headers` (`collect_active=False`) invocation where
            # the real run's own L2 seed (`seed_includes_and_fold_compile_
            # context`) wraps the identical `BuildSourcePack.load` in a
            # bare `except Exception:  # noqa: BLE001 -- best-effort` and
            # completes successfully by degrading silently. Confirmed
            # empirically: a real gcc-compiled library, a real clang
            # frontend, and a manifest with `"source_root": null` crash
            # this function's own load at the identical line the real L2
            # seed's own (differently-shaped) call site degrades from.
            # Broadened to match the real call sites' own catch-all
            # discipline exactly, rather than enumerating every possible
            # malformed-shape exception `from_dict` could raise one at a
            # time -- the failure mode here is "this pack's on-disk shape
            # doesn't parse," which is not narrower than what a corrupt or
            # adversarially-edited pack can produce, and every consumer of
            # this exception already branches on `collect_active`/
            # `l2_seed_reachable`, never on the exception's own type.
            if build_info is not None:
                # `l2_seed._l2_seed_pack_inputs` only attempts to load
                # `sources` when `build_info is None` -- with a non-``None``
                # `build_info`, it is never even reached (nulling
                # `raw_sources` is unconditional, but the `BuildSourcePack.
                # load(sources)` call is gated behind that same check) --
                # so this malformed pack is genuinely irrelevant to whether
                # the L2 seed's own query resolution proceeds: it resolves
                # via `build_info`'s own path unaffected, and may still run
                # `cfg.query` once -- BUT ONLY when the L2 seed is actually
                # *reachable* at all (`l2_seed_reachable`, computed above:
                # headers present AND a real artifact). When it is not
                # (a source-only dump, or an artifact dump with no `-H`),
                # the L2 seed never runs regardless of this pack, and
                # `embed_build_source` is then the ONLY call site left --
                # but it also loads `sources` unconditionally (see the
                # module docstring's note) and will hit this identical
                # failure itself, raising before ever reaching
                # `collect_inline_pack`/`cfg.query`. Falling through to a
                # "will run" claim in that shape is doubly wrong: not just
                # inconsistent with the `result.block()` call just below,
                # but describing an invocation whose one remaining real call
                # site provably never reaches the query at all (Codex
                # review, fresh evidence -- confirmed empirically with a
                # real gcc-compiled library, no headers, a malformed
                # --sources pack, and a raw --build-info: the real run
                # fails outright with "Invalid evidence pack", the
                # marker-writing query's marker is never created). This
                # holds regardless of `collect_active` otherwise (Codex
                # review, fresh evidence -- an earlier revision returned
                # early on `collect_active` alone, before ever checking
                # `build_info`, hiding that the L2 seed's own invocation
                # genuinely runs before `embed_build_source`'s own
                # *unconditional*, later re-attempt at loading this same
                # malformed pack -- see the module docstring's note on that
                # unconditional load -- fails and aborts the overall
                # command).
                result.add(
                    _SECTION,
                    f"build.query: could not load --sources pack {sources}: {exc}",
                )
                if collect_active:
                    if l2_seed_reachable:
                        result.block(
                            f"--sources names an unloadable pack ({sources}): {exc} -- "
                            "build-source embedding re-attempts this same load later "
                            "and fails there, even though the L2 seed's own query "
                            "invocation below is unaffected by it"
                        )
                    else:
                        result.block(
                            f"--sources names an unloadable pack ({sources}): {exc} -- "
                            "the L2 seed's own query invocation is not reachable "
                            "either (no headers/no artifact), so build-source "
                            "embedding's own unconditional re-load of this same "
                            "pack is the only remaining real call site, and it "
                            "fails before ever reaching build.query"
                        )
                        return
                # Fall through with no L3 evidence from this pack, same as a
                # valid-but-empty one -- the rest of this function resolves
                # the L2 seed's own query via build_info.
                src_pack_evidence = None
            elif collect_active:
                result.add(
                    _SECTION,
                    f"build.query: could not load --sources pack {sources}: {exc}",
                )
                result.block(f"--sources names an unloadable pack ({sources}): {exc}")
                return
            else:
                # `build_info is None` and `collect_mode == "off"`:
                # `l2_seed`'s own pack loading attempts the SAME malformed
                # pack in this shape (it is only skipped when `build_info`
                # is given) and, per its own best-effort contract, degrades
                # the whole resolution silently rather than raising -- no
                # compile_inline_pack call succeeds, so no query attempt
                # happens from either real call site.
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

    if build_info is not None and _is_pack_dir_any(build_info):
        try:
            bi_pack_evidence = _pack_dir_build_evidence(build_info)
        except Exception as exc:  # noqa: BLE001 -- best-effort preview load,
            # broadened for the identical reason the sibling --sources pack
            # load above was (Codex review, fresh evidence): a structurally
            # malformed manifest can raise TypeError (or another shape-
            # mismatch exception `from_dict` doesn't guard), not just
            # OSError/ValueError, and every branch below already keys off
            # `collect_active`, never the exception's own type.
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
                result.block(
                    f"--build-info names an unloadable pack ({build_info}): {exc}"
                )
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
    effective_sources = (
        None if (sources is not None and _is_pack_dir_any(sources)) else sources
    )

    # Two independent real call sites can load `cfg_path`, with two different
    # reachability conditions and two different failure behaviors:
    #
    # 1. `embed_build_source`'s own `raw_build_info`/`raw_sources` -- non-None
    #    only for a *non-pack* operand -- gate whether IT loads/validates
    #    `cfg_path`, and only once `collect_active` (its own collect-mode
    #    gate) already let it get that far. A load failure there is a real
    #    `click.UsageError` (exit 64).
    # 2. `l2_seed._l2_seed_config` (reached via `seed_includes_and_fold_
    #    compile_context`, gated on `l2_seed_reachable` above -- headers
    #    non-empty AND a real artifact -- independent of `collect_active`/
    #    pack status) *also* loads `cfg_path` whenever it runs,
    #    unconditionally -- but its own load is
    #    best-effort: a `ValueError` degrades to "no seeded dirs, no fold"
    #    rather than raising (its own docstring: "surfaces loudly elsewhere
    #    ... this is a best-effort include-dir hint, so it degrades ...
    #    rather than raising through"). Missing this path (Codex review,
    #    fresh evidence) meant a valid explicit --config's own query/
    #    compile_db went unread whenever the only reachable path was an
    #    empty pack + headers (`raw_operand_present` False, `collect_active`
    #    irrelevant since embed_build_source never even gets called for a
    #    fully-pack-absorbed pair) -- reported as "(none configured)" even
    #    though the real run genuinely resolves and runs a trusted query
    #    through this exact path.
    #
    # So *reading* cfg_path is gated on either path being reachable
    # (`config_readable`); *raising* on a load failure is gated on
    # `raise_on_bad_config`, requiring embed_build_source's own stricter
    # path specifically -- config validation must happen here, ahead of
    # every "will NOT run because X takes precedence" branch below (which
    # answer a materially different question: whether `_resolve_compile_db`
    # would use `cfg.query` once collect_inline_pack does run), not after
    # them (Codex review, fresh evidence: an earlier revision validated
    # config only after those precedence checks had already returned, so a
    # malformed auto-discovered config combined with e.g. an already-
    # resolved --build-info compile database never got validated at all --
    # verified end-to-end that the real run still raises for that exact
    # combination).
    raw_operand_present = (
        build_info is not None and not _is_pack_dir_any(build_info)
    ) or (effective_sources is not None)
    config_readable = l2_seed_reachable or raw_operand_present
    # `embed_build_source`'s own auto-discovery is `discover_build_config
    # (raw_sources)` -- keyed on `effective_sources` alone, never
    # `build_info` -- so a raw (non-pack) `--build-info` can make
    # `raw_operand_present` True while `effective_sources` is still `None`
    # (--sources absent, or itself a pack): in that shape `embed_build_
    # source` never discovers *any* file (`discover_build_config(None)` is
    # always `None`), so it can never be the reason a load fails, no matter
    # how `collect_active`/`raw_operand_present` resolve (Codex review,
    # fresh evidence -- a malformed `.abicheck.yml` inside a `--sources`
    # pack, combined with a raw `--build-info`, previously raised here even
    # though `embed_build_source` never reads that file at all: only
    # `l2_seed`'s own pack-rooted discovery does, and that path always
    # degrades silently). When `effective_sources` *is* set, `sources ==
    # effective_sources` unconditionally (it is only ever nulled when
    # `--sources` is itself a pack), so `discover_from` above always agrees
    # with what `embed_build_source` would independently discover -- no
    # divergence to guard against in that case.
    raise_on_bad_config = (
        collect_active and raw_operand_present and effective_sources is not None
    )

    # Same source (source-tree-root-only, no upward walk) `embed_build_source`
    # itself resolves from for this purpose -- distinct from `discover_project_
    # config`'s upward walk, which the rest of this dry-run report already uses
    # for the generic ".abicheck.yml:" info line.
    #
    # But *which* value depends on which real call site is doing the
    # discovering, and the two disagree (Codex review, fresh evidence):
    # `embed_build_source` discovers from its own normalized `raw_sources`
    # (nulled whenever --sources is a pack, matching `effective_sources`
    # here), while `l2_seed._l2_seed_config` discovers from the *original,
    # unnormalized* `sources` it is handed
    # (`_resolve_l2_seed_pack_args`/`seed_includes_and_fold_compile_context`
    # pass the raw `sources` parameter straight through to it, never the
    # pack-nulled value) -- so an empty --sources pack carrying its own
    # .abicheck.yml is genuinely readable by the L2-seed path even though
    # `effective_sources` alone would report "(none configured)". When
    # --sources is not itself a pack, `sources` and `effective_sources` are
    # identical, so this only changes behavior for the pack case. `cwd`/the
    # compile-DB hint below still use `effective_sources`, matching
    # `embed_build_source`'s own real cwd/compile-DB resolution -- only
    # config *discovery* differs between the two real call sites.
    discover_from = sources if l2_seed_reachable else effective_sources
    cfg_path = build_config or discover_build_config(discover_from)
    # An explicit --config is now the *only* authorizer (CLI cleanup phase
    # two, PR 3C): `--build-query`/`--build-compile-db` are removed, so the
    # real gate in `cli_buildsource.embed_build_source` reduces to this same
    # single term. There is no longer a second way to mark a query trusted.
    trusted = build_config is not None

    # The real path (`cli_buildsource.py`) always loads *cfg_path* when one
    # is found, for `cfg.compile_db` as well as `cfg.query`.
    cfg = None
    cfg_compile_db: str | None = None
    if cfg_path is not None and config_readable:
        try:
            cfg = load_build_config(cfg_path)
        except ValueError as exc:
            # `build_config is None` here -- the explicit-config case
            # already raised, unconditionally, at the very top of this
            # function. This is therefore always an *auto-discovered*
            # config, which `embed_build_source` validates strictly (a
            # `click.UsageError`, exit 64) only past its own collect-mode
            # AND raw-operand gate -- `raise_on_bad_config` above -- while
            # `l2_seed`'s own headers-gated load degrades silently
            # regardless (CodeRabbit/Codex review, fresh evidence; verified
            # end-to-end: a malformed auto-discovered config exits 0,
            # warn-only, under `--depth headers`, but exits 64 under the
            # default collect mode). Raised directly rather than encoded via
            # `result.block()`, matching this module's documented exit-64
            # contract, same as the explicit-config case above.
            if raise_on_bad_config:
                import click

                raise click.UsageError(
                    f"cannot parse build config {cfg_path}: {exc}"
                ) from exc
            result.add(_SECTION, f"build.query: could not load {cfg_path}: {exc}")
            # `cfg_path` here was discovered from `discover_from` above, which
            # -- when `l2_seed_reachable` -- is the *unnormalized* `sources`,
            # not `effective_sources`. `embed_build_source`'s own discovery
            # always uses `effective_sources`, so whenever the two diverge
            # (`effective_sources is None`, e.g. because `--sources` is
            # itself a pack) `embed_build_source` never even attempts to
            # read *this* `cfg_path` -- it is purely an L2-seed-only
            # discovery, and this load failure says nothing about whether
            # `embed_build_source`'s own, independent config resolution
            # (which may still succeed from the explicit --config's own query
            # override with no file involved at all) would also fail --
            # BUT ONLY when `embed_build_source` is actually *reachable* at
            # all: its own dispatch guard is `raw_build_info is not None or
            # raw_sources is not None`, and `raw_sources` is nulled the exact
            # same way `effective_sources` is (both collapse to `None`
            # whenever `--sources` is itself a pack) -- so inside this
            # `effective_sources is None` branch, `raw_sources` is always
            # `None` too, and the guard reduces to whether a genuine, raw
            # (non-pack) `--build-info` was also given. `raw_operand_present`
            # (computed above) already answers exactly that question in this
            # branch. Getting this wrong is a real, confirmed regression, not
            # a hypothetical: an earlier revision of this fix fell through
            # unconditionally whenever `effective_sources is None`, which
            # made a `--sources`-only pack (no `--build-info` at all) with a
            # malformed config report "will run" -- but with `raw_build_info`
            # also `None` in that shape, `embed_build_source`'s own dispatch
            # guard is never satisfied at all, so it never reaches the
            # query-resolution step either;
            # the *only* real call site (the L2 seed) already failed to
            # load this exact config, so the real run does NOT execute the
            # query here (Codex review, fresh evidence -- verified by
            # reading `embed_build_source`'s own `if raw_build_info is not
            # None or raw_sources is not None:` guard directly). The
            # original finding this whole branch exists for (Codex review,
            # commit f9fd95d) specifically named a raw `--build-info` as
            # part of the scenario -- this fix was too broad in dropping
            # that qualifier. Fall through with `cfg = None` only when
            # `raw_operand_present` -- i.e. a raw `--build-info` genuinely
            # makes `embed_build_source` reachable -- rather than returning,
            # so the precedence chain below still answers correctly from
            # that operand alone in that case. When `effective_sources is
            # not None`, `discover_from` always agrees with what
            # `embed_build_source` would discover (see the
            # `raise_on_bad_config` comment above), so this load failure
            # really does mean both call sites are equally affected --
            # reporting "will NOT run" there remains correct, and this
            # branch is unreached in that case since `raise_on_bad_config`
            # (which requires `collect_active` too) would already have
            # raised whenever `embed_build_source` could actually be reached
            # with a failing config of its own. `raw_operand_present` alone
            # is not sufficient, though (Codex review, fresh evidence): it
            # says a raw --build-info exists to make embed_build_source's
            # *dispatch guard* satisfiable, but that guard is reached only
            # when `collect_active` (`collect_mode != "off"`) in the first
            # place -- `embed_build_source` is called from `cli_dump_
            # helpers.perform_elf_dump` behind exactly that check. With
            # `--depth headers` (collect_mode "off"), embed_build_source is
            # never invoked at all regardless of what operands were given,
            # so it can't be the fallback call site either -- verified
            # end-to-end against a real gcc-compiled library, a malformed
            # pack-local .abicheck.yml, a raw --build-info directory, an
            # explicit --config, and --depth headers: the real run
            # exits 0 with the marker never created, i.e. build.query never
            # runs, even though an earlier revision of this branch reported
            # "will run (trusted -- explicit --config)" here.
            if not collect_active and effective_sources is None and raw_operand_present:
                result.add(
                    _SECTION,
                    "build.query: will NOT run -- the auto-discovered config "
                    "failed to load for the L2 seed path (which silently "
                    "degrades on a load failure, rather than raising), and "
                    f"embed_build_source is unreachable anyway -- collect "
                    f"mode {collect_mode!r} means only the best-effort L2 "
                    "seed path could ever run this query",
                )
                return
            if effective_sources is not None or not raw_operand_present:
                result.add(
                    _SECTION,
                    "build.query: will NOT run -- the auto-discovered config "
                    "failed to load, and only the best-effort L2 seed path "
                    "(which silently degrades on a load failure, rather than "
                    "raising) could otherwise reach it",
                )
                return
            result.add(
                _SECTION,
                "build.query: the auto-discovered config failed to load, but "
                "only for the L2 seed path's own pack-rooted discovery "
                "(which silently degrades on a load failure, rather than "
                "raising) -- embed_build_source's own, independent config "
                "resolution never reads this same file (--sources is a pack, "
                "so its discovery is nulled), and it is reachable at all "
                f"only because a raw --build-info ({build_info}) was also "
                "given, so it is evaluated separately below from an "
                "auto-discovered config of its own, if any",
            )
        else:
            cfg_compile_db = cfg.compile_db or None

    # NOW the "does the query actually get reached" precedence chain --
    # unaffected by config validation above, since these branches answer
    # whether `collect_inline_pack`/`_resolve_compile_db` would even look at
    # `cfg.query` given the operands' own shapes.
    if build_info is not None and _is_pack_dir_any(build_info):
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
        if effective_sources is None and not l2_seed_reachable:
            # embed_build_source's own raw_build_info becomes None once
            # --build-info is a pack (regardless of collect mode), and
            # raw_sources is None whenever --sources is absent *or* is
            # itself a pack (not just absent -- an empty --sources pack
            # normalizes the same way, which a literal `sources is None`
            # check missed: Codex review, fresh evidence) -- its dispatch
            # condition (`raw_build_info is not None or raw_sources is not
            # None`) therefore fails unconditionally, leaving only the L2
            # seed path, which itself needs headers.
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
        from .workflows.extraction import sniff_build_info_format

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
    elif sources is not None and _is_pack_dir_any(sources):
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
        if not l2_seed_reachable:
            # build_info is None in this branch (elif chain), so
            # embed_build_source's raw_build_info is already None; raw_sources
            # becomes None too once --sources is a pack, unconditionally --
            # its dispatch condition fails regardless of collect mode, leaving
            # only the L2 seed path, which itself needs headers AND a real
            # artifact (Codex
            # review, fresh evidence).
            result.add(
                _SECTION,
                "build.query: will NOT run -- --sources is a pack with no L3 "
                "compile units and no headers give another path to "
                "collect_inline_pack",
            )
            return

    effective_query = cfg.query if cfg else None
    # `_run_build_query`'s own resolution of the compile-DB path it expects
    # the query to have (re)written is gated on `sources is not None`: with
    # no source tree (an absent --sources, or one that normalized to None
    # because it's itself a pack), it neither globs `cfg.compile_db` against
    # it nor auto-discovers a `compile_commands.json` -- `db` stays `None`
    # regardless of whether a compile-DB hint is configured (Codex review,
    # fresh evidence). This module must not promise a specific path the real
    # run can never resolve to.
    _configured_compile_db_hint = cfg_compile_db
    compile_db_hint = (
        _configured_compile_db_hint if effective_sources is not None else None
    )

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

    # Since PR 3C an explicit --config is the only authorizer, so reaching
    # here at all means `build_config is not None` (see `trusted` above).
    # Kept as a named constant rather than inlined so the rendered label
    # stays greppable next to the trust decision it reports.
    trust_source = "explicit --config"
    cwd = (
        effective_sources
        if effective_sources is not None and effective_sources.is_dir()
        else Path.cwd()
    )
    if compile_db_hint and Path(compile_db_hint).is_absolute():
        # `_run_build_query`'s own resolution -- reached here, since we are
        # already inside the "trusted, will run" branch -- calls
        # `sources.glob(cfg.compile_db)` unconditionally once the query
        # exits 0 (`inline.py`'s own `if cfg.compile_db and sources is not
        # None: for match in sorted(sources.glob(cfg.compile_db)):`), with
        # no guard against an absolute pattern. `Path.glob()` raises
        # `NotImplementedError` outright for one, uncaught anywhere in that
        # call chain -- a genuine unhandled crash in the real run, not a
        # hypothetical (Codex review, fresh evidence: confirmed by reading
        # `_run_build_query`'s own body directly, the same code path
        # `_resolve_compile_db_hint_line`'s own `NotImplementedError`
        # handler below already documents for the *pre-query* glob check,
        # just reached this time from the *post-query* one). A dry run
        # claiming this invocation is "valid" (exit 0) would be actively
        # wrong -- it is going to crash, not merely produce an unexpected
        # compile-DB answer -- so this blocks before any "will run" claim
        # is made, the same way every other genuinely-broken-invocation
        # case in this module does.
        result.block(
            f"build.compile_db is configured as an absolute path "
            f"({compile_db_hint!r}) -- build.compile_db is documented as "
            "relative to --sources, and the real run's own "
            "sources.glob(cfg.compile_db) call raises NotImplementedError "
            "outright for an absolute pattern, once the query itself exits "
            "0 -- this invocation will crash, not merely run"
        )
    if compile_db_hint:
        # `_run_build_query`'s own resolution isn't a literal-string label --
        # `cfg.compile_db`, glob-metacharacter-bearing or not, is resolved
        # via `sorted(sources.glob(cfg.compile_db))` AFTER the query has run
        # (first existing file wins), expecting the query to have (re)written
        # it. `Path.glob()` treats a metacharacter-free pattern as an exact
        # relative-path existence check, so a plain `build/compile_
        # commands.json` hint resolves the identical way a real
        # `build/*/compile_commands.json` glob does -- it is not printed
        # verbatim as if it were already a path relative to *this process's*
        # cwd; it is joined onto `sources` and checked for existence. An
        # earlier revision special-cased "no glob metacharacters" as
        # "unambiguous, print as-is," but that was wrong for the same reason
        # a real glob is: whether the file already exists still needs
        # checking, and the printed value must be the resolved path (or an
        # explicit "not yet" note), never the bare configured string (Codex
        # review, fresh evidence -- the common, glob-free `build.compile_db:
        # build/compile_commands.json` case previously printed the literal
        # string even when `--sources` was some other, unrelated directory).
        compile_db_line = _resolve_compile_db_hint_line(
            compile_db_hint, effective_sources
        )
    elif _configured_compile_db_hint and effective_sources is None:
        compile_db_line = (
            f"resulting compile-DB path: (build.compile_db is configured, "
            f"{_configured_compile_db_hint!r}, but there is no --sources tree "
            "to resolve it against -- the query's own default output location)"
        )
    elif effective_sources is not None:
        # No `build.compile_db` hint configured, but `_run_build_query`
        # still resolves *something*: `_autodiscover_compile_db(sources)`
        # (a pure, read-only stat/glob search over conventional build-dir
        # names, then any immediate subdirectory) -- the query's expected
        # default output location genuinely exists as a concrete path when
        # a conventional compile DB is already sitting under `--sources`
        # (Codex review, fresh evidence). Reusing this private helper
        # mirrors the existing `_compile_db_at` reuse pattern this module
        # already relies on elsewhere.
        from .workflows.extraction import _autodiscover_compile_db

        try:
            discovered_db = _autodiscover_compile_db(effective_sources)
        except (OSError, ValueError):
            discovered_db = None
        if discovered_db is not None:
            # PROVISIONAL, same as the configured-glob branch above:
            # `_run_build_query` runs the arbitrary query BEFORE
            # `_autodiscover_compile_db` is ever consulted for real, so the
            # query may delete this file, create a higher-precedence
            # candidate (a conventional dir name earlier in
            # `_autodiscover_compile_db`'s own search order), or leave it
            # unchanged -- the real run can select a different path, or
            # none at all (Codex review, fresh evidence).
            compile_db_line = (
                f"resulting compile-DB path (provisional, pre-query "
                f"snapshot): {discovered_db} (no build.compile_db "
                "configured; a conventional compile DB already exists "
                "here, but the query runs BEFORE this auto-discovery is "
                "actually performed for real, so it may delete this file, "
                "create a higher-precedence candidate, or leave it "
                "unchanged -- the real run always re-discovers fresh after "
                "the query exits, and can select a different path or none "
                "at all)"
            )
        else:
            compile_db_line = (
                "resulting compile-DB path: (build.compile_db not "
                "configured, and no conventional compile DB exists yet -- "
                "the query's own default output location)"
            )
    else:
        compile_db_line = (
            "resulting compile-DB path: (build.compile_db not configured -- "
            "the query's own default output location)"
        )
    # Two independent real call sites can each reach this identical `cfg.
    # query` resolution for the SAME operands: `l2_seed.seed_includes_and_
    # fold_compile_context` (gated on `l2_seed_reachable`) runs first inside
    # `perform_elf_dump`/`handle_non_elf_dump`, and `embed_build_source`
    # (gated on `collect_active`, i.e. `collect_mode != "off"`) runs again
    # afterward from `_write_snapshot_output` -- neither caches or shares its
    # result with the other. Whether the query genuinely runs twice is
    # subject to TWO independent, compounding sources of uncertainty this
    # preview cannot resolve without actually running the real command:
    #
    # 1. **Reaching the second call site at all.** The L2 seed's own
    #    invocation runs first, as an early step inside `perform_elf_dump`/
    #    `handle_non_elf_dump` -- but `embed_build_source` (the second
    #    invocation) is only reached from `_write_snapshot_output`, well
    #    after the primary header-AST parse. If the intervening dump fails
    #    or exits before that point -- e.g. `castxml`/the resolved AST
    #    frontend is missing, or the header parse itself errors -- the
    #    second invocation never runs at all, regardless of any other
    #    condition below (Codex review, fresh evidence: reproduced directly
    #    -- a real `dump` with no castxml on PATH ran the marker-appending
    #    query exactly once, via the L2 seed, before the primary parse
    #    failed and aborted the command).
    # 2. **Whether `build_info` already short-circuits it, once reached.**
    #    `_resolve_compile_db`'s `if build_info is not None:` branch checks
    #    `_compile_db_at(build_info)` *before* ever considering `cfg.query`
    #    -- with no raw `--build-info` given at all, neither invocation's
    #    own call ever takes this branch, so nothing here can prevent the
    #    second invocation's `cfg.query` from running once reached. With a
    #    raw `--build-info` given (not yet resolving to a compile DB at
    #    dry-run time -- every earlier precedence branch in this function
    #    already ruled out the case where it currently does), the FIRST
    #    invocation also finds nothing there and runs `cfg.query` -- but if
    #    that query's own side effect happens to (re)write a compile DB at
    #    exactly `build_info`'s path (a common real setup: `--build-info
    #    <dir>` pointing at the same directory `build.query` configures its
    #    build to output into), the SECOND invocation's own
    #    `_compile_db_at(build_info)` now finds it and returns early,
    #    skipping `cfg.query` -- running the query only once even though it
    #    was reached (Codex review, fresh evidence: verified empirically
    #    with two real compiled-library runs of the identical
    #    marker-appending query, one whose query also wrote a compile DB
    #    into `--build-info`'s exact path -- one marker line -- and one
    #    whose query did not -- two marker lines).
    #
    # Neither uncertainty is resolved -- let alone fixed at the production
    # call sites -- here: predicting condition 1 would mean simulating
    # whether the real header-AST parse succeeds, which is exactly the
    # expensive, side-effecting work this module's whole "no I/O beyond
    # stat()/PATH lookups" contract exists to avoid running; deduplicating
    # the two collections for condition 2 is a real behavior change to
    # `dump`'s own execution (sharing/caching a resolved `BuildEvidence`
    # across two currently-independent call sites, each with its own scope/
    # config-resolution nuances), not a change this read-only preview module
    # can make on its own; see this module's own "Known, deliberately
    # unclosed gaps" section.
    #
    # A third condition gates whether `embed_build_source` (the second call
    # site) can EVER dispatch into `collect_inline_pack` at all, independent
    # of the two above: its own `raw_build_info`/`raw_sources` both
    # collapse to `None` whenever the corresponding operand is itself a pack
    # (`bi_is_pack`/`src_is_pack`) or absent -- its dispatch guard
    # (`raw_build_info is not None or raw_sources is not None`) fails
    # unconditionally when BOTH normalize away, e.g. `--build-info
    # <emptypack>` given alone with no raw `--sources` at all (Codex review,
    # fresh evidence). When that happens, the second invocation is not
    # merely unreached-yet or short-circuited -- it never runs, regardless
    # of whether the intervening dump succeeds, so this must gate
    # `both_sites_reachable` itself rather than only the `build_info is
    # None` split above (which only distinguishes *why* a reachable second
    # invocation may or may not still run `cfg.query`).
    raw_build_info_for_embed = (
        None if (build_info is None or _is_pack_dir_any(build_info)) else build_info
    )
    embed_dispatch_possible = (
        raw_build_info_for_embed is not None or effective_sources is not None
    )
    both_sites_reachable = (
        l2_seed_reachable and collect_active and embed_dispatch_possible
    )
    count_suffix: str
    count_note: tuple[str, ...]
    if both_sites_reachable and raw_build_info_for_embed is None:
        # Includes BOTH `build_info is None` (no --build-info at all) and a
        # --build-info that IS given but normalizes away (it is itself a
        # pack, per `raw_build_info_for_embed`'s own definition above) --
        # `embed_build_source`'s own `_resolve_compile_db` call receives
        # `build_info=None` in that second case too (pack-normalized,
        # matching `_l2_seed_pack_inputs`'s identical nulling), so its `if
        # build_info is not None:` short-circuit branch can never fire for
        # the second invocation either way, regardless of what the ORIGINAL
        # --build-info operand was (Codex review, fresh evidence -- an
        # earlier revision checked the raw `build_info is None` here
        # instead, wrongly routing a pack --build-info into the
        # short-circuit-possible branch below even though a pack can never
        # actually provide that short-circuit for the second invocation).
        count_suffix = (
            " -- RUNS AT LEAST ONCE, AND AGAIN IF THE DUMP REACHES "
            "BUILD-SOURCE EMBEDDING -- see note below"
        )
        count_note = (
            "note: this input combination reaches build.query from two "
            "independent, non-deduplicated call sites in the real run: the "
            "L2 include-dir seed (runs first, unconditionally once headers "
            "+ a real artifact are given) and build-source embedding (runs "
            "afterward, from _write_snapshot_output, ONLY if the "
            "intervening header-AST parse and dump succeed that far -- e.g. "
            "it never runs if the AST frontend is missing or the parse "
            "errors). No raw --build-info directory/file reaches the second "
            "invocation's own compile-DB resolution (either none was given, "
            "or it was itself a pack, which normalizes away identically), "
            "so nothing can short-circuit that invocation's own query once "
            "reached -- a non-idempotent query (e.g. one that appends "
            "rather than overwrites) therefore executes twice if the dump "
            "reaches build-source embedding, or once if it does not",
        )
    elif both_sites_reachable:
        count_suffix = " -- RUNS AT LEAST ONCE, POSSIBLY TWICE -- see note below"
        count_note = (
            "note: this input combination reaches build.query from two "
            "independent, non-deduplicated call sites in the real run: the "
            "L2 include-dir seed (runs first, unconditionally once headers "
            "+ a real artifact are given) and build-source embedding (runs "
            "afterward, from _write_snapshot_output, ONLY if the "
            "intervening header-AST parse and dump succeed that far). Even "
            "if that point is reached, whether the second invocation also "
            "runs the query -- rather than short-circuiting because the "
            "first invocation's query happened to write a compile DB at "
            "--build-info's own path -- cannot be determined without "
            "actually running the query (which this preview never does); a "
            "non-idempotent query may therefore execute once (the dump "
            "fails before reaching embedding, or --build-info's path gets "
            "satisfied by the first run) or twice",
        )
    else:
        count_suffix = ""
        count_note = ()
    result.add(
        _SECTION,
        f"build.query: will run (trusted -- {trust_source})" + count_suffix,
        f"argv: {argv}",
        f"cwd: {cwd}",
        compile_db_line,
        *count_note,
    )
