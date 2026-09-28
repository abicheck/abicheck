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

"""``dump --dry-run``'s ``build.query`` config resolution.

Owns the half of the ``Build query (trust)`` preview that decides *which*
build config the real call sites would read, and what loading it does:
operand normalization (a pack-directory ``--sources``/``--build-info`` is
nulled the way the real resolvers null it), config discovery, and the
malformed-auto-discovered-config outcomes. The reachability/trust/argv half
and the section's assembly stay in ``abicheck/cli_dump_dry_run_build_query.py``,
whose module docstring records the real call sites every rule here mirrors.
Resolution only -- nothing here ever executes ``build.query``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...dry_run import DryRunResult
    from ...workflows.extraction import BuildConfig

BUILD_QUERY_SECTION = "Build query (trust)"


def _is_inputs_pack_dir(path: Path | None) -> bool:
    """Compatibility alias for ``buildsource.inputs_pack.is_inputs_pack_dir``.

    Owned there since ADR-061 Phase 3; this was the third of three copies of
    the same guard, each local because the original lived in the CLI layer.
    """
    from ...workflows.extraction import is_inputs_pack_dir

    return is_inputs_pack_dir(path)


def is_pack_dir_any(path: Path | None) -> bool:
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
    from ...workflows.extraction import is_pack_dir

    return is_pack_dir(path) or _is_inputs_pack_dir(path)


def load_query_config(
    result: DryRunResult,
    *,
    cfg_path: Path | None,
    config_readable: bool,
    raise_on_bad_config: bool,
    collect_active: bool,
    collect_mode: str,
    effective_sources: Path | None,
    raw_operand_present: bool,
    build_info: Path | None,
) -> tuple[bool, BuildConfig | None, str | None]:
    """Load the build config the real call sites would read; ``(stop, cfg, compile_db)``."""
    from ...workflows.extraction import load_build_config

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
            result.add(
                BUILD_QUERY_SECTION, f"build.query: could not load {cfg_path}: {exc}"
            )
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
                    BUILD_QUERY_SECTION,
                    "build.query: will NOT run -- the auto-discovered config "
                    "failed to load for the L2 seed path (which silently "
                    "degrades on a load failure, rather than raising), and "
                    f"embed_build_source is unreachable anyway -- collect "
                    f"mode {collect_mode!r} means only the best-effort L2 "
                    "seed path could ever run this query",
                )
                return True, None, None
            if effective_sources is not None or not raw_operand_present:
                result.add(
                    BUILD_QUERY_SECTION,
                    "build.query: will NOT run -- the auto-discovered config "
                    "failed to load, and only the best-effort L2 seed path "
                    "(which silently degrades on a load failure, rather than "
                    "raising) could otherwise reach it",
                )
                return True, None, None
            result.add(
                BUILD_QUERY_SECTION,
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
    return False, cfg, cfg_compile_db


def resolve_config_inputs(
    *,
    sources: Path | None,
    build_info: Path | None,
    build_config: Path | None,
    l2_seed_reachable: bool,
    collect_active: bool,
) -> tuple[Path | None, bool, bool, bool, Path | None]:
    """Normalize operands and pick the config path the real call sites read.

    Returns ``(effective_sources, raw_operand_present, config_readable,
    raise_on_bad_config, cfg_path)``.
    """
    from ...workflows.extraction import discover_build_config

    # `_l2_seed_pack_inputs` nulls `raw_sources` whenever --sources is itself
    # a pack directory, unconditionally (independent of --build-info) -- both
    # config auto-discovery and the query's own cwd must use that same
    # normalized value, not the pack directory itself (Codex review, fresh
    # evidence).
    effective_sources = (
        None if (sources is not None and is_pack_dir_any(sources)) else sources
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
        build_info is not None and not is_pack_dir_any(build_info)
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
    return (
        effective_sources,
        raw_operand_present,
        config_readable,
        raise_on_bad_config,
        cfg_path,
    )
