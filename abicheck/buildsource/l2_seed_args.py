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

"""Config and pack-argument resolution for L2 include/compile-context seeding.

Split out of :mod:`abicheck.buildsource.l2_seed` (which keeps the seeding
itself): this module answers *which* build config, trust flags and pack inputs
a seed call runs with, so that decision has one owner the ``derive_l2_*``
entry points all share.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from . import pack_io
from .inline import BuildConfig, discover_build_config, is_pack_dir, load_build_config


def _l2_seed_config(
    build_config: Path | None,
    sources: Path | None,
    build_query: str | None,
    build_compile_db: str | None,
    build_targets: tuple[str, ...] = (),
) -> BuildConfig | None:
    """The effective ``BuildConfig`` for L2 seeding, or ``None`` to degrade.

    Mirrors ``embed_build_source``'s config handling so a trusted ``--config``
    ``build.compile_db``/``build.query`` is honored here too (and only an
    explicit ``--config`` file is trusted for query execution; an
    auto-discovered ``.abicheck.yml`` is loaded for its non-executable settings
    but never run), then folds the CLI build-DB overrides in exactly as embed
    does, so L2 seeding resolves the *same* DB L3 will.

    ``build_targets`` (P0.2, Codex review): folded in the same way, so a
    ``build.targets``/``InputSpec.build_targets`` scope -- ``--build-target``
    itself was removed as a CLI flag; only the config field and the typed-API
    parameter remain -- scopes the L2 include/compile-context seed's own
    ``collect_inline_pack`` call identically to ``embed_build_source``'s
    L3/L4/L5 collection, so a multi-target Bazel workspace can't seed L2
    from an unrelated target's include dirs/dialect flags.

    A malformed/invalid config surfaces loudly elsewhere (``embed_build_source``,
    the compile-context resolver); this is a best-effort include-dir hint, so it
    degrades to "no seeded dirs" rather than raising through.
    """
    cfg_path = build_config or discover_build_config(sources)
    try:
        cfg = load_build_config(cfg_path) if cfg_path is not None else BuildConfig()
    except ValueError:
        return None
    if build_query is None and build_compile_db is None and not build_targets:
        return cfg
    return dataclasses.replace(
        cfg,
        query=build_query if build_query is not None else cfg.query,
        compile_db=(
            build_compile_db if build_compile_db is not None else cfg.compile_db
        ),
        targets=list(build_targets) if build_targets else cfg.targets,
    )


def _is_inputs_pack_dir(path: Path | None) -> bool:
    """Compatibility alias for ``inputs_pack.is_inputs_pack_dir``.

    Owned there since ADR-061 Phase 3; this was one of three copies of the
    same guard, each kept local because the original lived in the CLI layer.
    The import stays function-local, as the copy's own note required: this
    module is reached from ``inline``, and ``inputs_pack`` imports ``inline``.
    """
    from .inputs_pack import is_inputs_pack_dir

    return is_inputs_pack_dir(path)


def _l2_seed_pack_build_evidence(path: Path) -> Any:
    """The ``BuildEvidence`` a pack directory at *path* folds into L2 seeding.

    Mirrors whichever of the two real loaders ``embed_build_source``'s own
    pack recognition would use for *path*: a classic ``BuildSourcePack``
    (``is_pack_dir``) loads via ``BuildSourcePack.load``; a Flow-2
    ``abicheck_inputs/`` pack (ADR-035 D5) loads via the lighter,
    comparable-cost ``load_inputs_manifest`` + ``_load_build_evidence`` pair
    -- parsing only the pack's own compile DB, not the full
    ``ingest_inputs_pack`` (which additionally reads and parses every
    ``source_facts/*.jsonl`` file: real extra I/O this L3-only seed does not
    need, and it would also require an ``exported_symbols`` list this
    caller, deep inside L2 seeding, has no access to). Raises the same way
    the real loaders do for a structurally malformed pack
    (``FileNotFoundError``/``ValueError``) -- both callers of
    :func:`_l2_seed_pack_inputs` already treat this whole resolution as
    best-effort and degrade to "no seeded dirs" on any exception, so this
    function does not need its own catch.
    """
    if is_pack_dir(path):
        return pack_io.load(path).build_evidence
    from .inputs_pack import _load_build_evidence, load_inputs_manifest

    manifest = load_inputs_manifest(path)
    return _load_build_evidence(path, manifest, [])


def _l2_seed_pack_inputs(
    build_info: Path | None, sources: Path | None
) -> tuple[Any, Path | None, Path | None]:
    """``(base_build, raw_build_info, raw_sources)`` with any pack pre-loaded.

    A ``--sources`` pack carries its own L3 ``build_evidence``, which
    ``embed_build_source``/``_combine_packs`` use for L3 when no ``--build-info``
    does; mirror that so the pack's compile-unit include dirs seed L2 too
    (Codex). Any explicit ``--build-info`` wins L3, so seed from the source pack
    only when *no* ``--build-info`` was given (not merely no build-info *pack*):
    a raw ``--build-info`` must still be resolved by ``collect_inline_pack``,
    not skipped by folding the pack into ``base_build`` (Codex review).

    Also recognizes a Flow-2 ``abicheck_inputs/`` pack (ADR-035 D5,
    ``_is_inputs_pack_dir``) the identical way it recognizes a classic
    ``BuildSourcePack`` -- ``embed_build_source`` already folds both shapes
    in uniformly (``bi_is_pack or bi_is_inputs`` / ``src_is_pack or
    src_is_inputs``), but this function, the L2-seed path's own pack
    recognizer, previously checked only ``is_pack_dir``. Confirmed by
    reading both real call sites: neither ``collect_inline_pack`` nor
    anything it calls has its own Flow-2 recognition, so an un-normalized
    Flow-2 pack directory reaching it was silently treated as a literal
    source tree -- its own compile-unit include dirs never reached L2
    seeding at all, and (the sharper failure mode) a trusted, explicit
    ``build.query``/``--config`` could genuinely be re-executed against the
    pack directory as if it were a real, queryable project checkout, even
    though the pack already carries its own resolved L3 evidence to fold in
    directly instead.
    """
    base_build = None
    raw_build_info = build_info
    if build_info is not None and (
        is_pack_dir(build_info) or _is_inputs_pack_dir(build_info)
    ):
        base_build = _l2_seed_pack_build_evidence(build_info)
        raw_build_info = None
    raw_sources = sources
    if sources is not None and (is_pack_dir(sources) or _is_inputs_pack_dir(sources)):
        if build_info is None:
            base_build = _l2_seed_pack_build_evidence(sources)
        raw_sources = None
    return base_build, raw_build_info, raw_sources


@dataclasses.dataclass(frozen=True)
class _L2SeedPackArgs:
    """Everything :func:`derive_l2_include_dirs` and :func:`derive_l2_compile_context`
    need to make their own, independent ``collect_inline_pack(..., layers=("L3",))``
    call, resolved identically for both.

    Config resolution (:func:`_l2_seed_config`), the trust flags derived from
    it, and the pack/build-info precedence (:func:`_l2_seed_pack_inputs`) were
    previously duplicated verbatim between the two ``derive_l2_*`` functions;
    this bundles that shared argument-*building* step into one helper both
    consume. Deliberately does **not** call ``collect_inline_pack`` itself —
    each ``derive_l2_*`` function keeps its own independent call (see
    :func:`derive_l2_compile_context`'s own docstring for why: an accepted,
    documented double-collection cost, not a duplication to also fold away
    here), so this only removes the genuinely-identical setup work ahead of
    that call.
    """

    sources: Path | None
    build_info: Path | None
    build_config: BuildConfig
    build_config_trusted_for_query: bool
    compile_db_explicit: bool
    base_build: Any


def _resolve_l2_seed_pack_args(
    build_config: Path | None,
    sources: Path | None,
    build_info: Path | None,
    build_query: str | None,
    build_compile_db: str | None,
    build_targets: tuple[str, ...] = (),
    build_config_explicit: bool = True,
) -> _L2SeedPackArgs | None:
    """Resolve *build_config*/*sources*/*build_info* into ``collect_inline_pack``
    call arguments, or ``None`` when there is no config to seed from (the
    caller's existing "nothing to apply" degrade).
    """
    cfg = _l2_seed_config(
        build_config, sources, build_query, build_compile_db, build_targets
    )
    if cfg is None:
        return None
    base_build, raw_build_info, raw_sources = _l2_seed_pack_inputs(build_info, sources)
    # A front-end-discovered config (build_config_explicit=False) is read for
    # its passive settings but never trusted to run build.query.
    operator_config = build_config is not None and build_config_explicit
    return _L2SeedPackArgs(
        sources=raw_sources,
        build_info=raw_build_info,
        build_config=cfg,
        build_config_trusted_for_query=operator_config or build_query is not None,
        compile_db_explicit=build_compile_db is not None or operator_config,
        base_build=base_build,
    )
