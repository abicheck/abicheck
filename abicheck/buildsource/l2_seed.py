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

"""L2 header-parse include-dir seeding (shared by ``scan`` and ``dump``).

Split out of ``inline.py`` (which owns the L3/L4/L5 collection engine) to keep
that file under the size cap. These helpers are a thin *reader* over
:func:`inline.collect_inline_pack`: they resolve the build's compile-DB include
dirs so a ``-H`` header parse with no ``-I`` can still find the dependency
headers the build already knows about (the EPICS pvxs → EPICS Base case).

``inline`` re-exports ``derive_l2_include_dirs``/``seed_l2_includes`` via a lazy
module ``__getattr__`` so the historical ``from .inline import …`` paths (and the
CLI callers) keep working without a static ``inline`` → ``l2_seed`` import edge
that would re-introduce an import cycle.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from ..compile_context import CompileContext
from .inline import (
    BuildConfig,
    _run_cleanups,
    collect_inline_pack,
    discover_build_config,
    is_pack_dir,
    load_build_config,
)
from .pack import BuildSourcePack

logger = logging.getLogger(__name__)


def _l2_seed_config(
    build_config: Path | None,
    sources: Path | None,
    build_query: str | None,
    build_compile_db: str | None,
) -> BuildConfig | None:
    """The effective ``BuildConfig`` for L2 seeding, or ``None`` to degrade.

    Mirrors ``embed_build_source``'s config handling so a trusted ``--config``
    ``build.compile_db``/``build.query`` is honored here too (and only an
    explicit ``--config`` file is trusted for query execution; an
    auto-discovered ``.abicheck.yml`` is loaded for its non-executable settings
    but never run), then folds the CLI build-DB overrides in exactly as embed
    does, so L2 seeding resolves the *same* DB L3 will.

    A malformed/invalid config surfaces loudly elsewhere (``embed_build_source``,
    the compile-context resolver); this is a best-effort include-dir hint, so it
    degrades to "no seeded dirs" rather than raising through.
    """
    cfg_path = build_config or discover_build_config(sources)
    try:
        cfg = load_build_config(cfg_path) if cfg_path is not None else BuildConfig()
    except ValueError:
        return None
    if build_query is None and build_compile_db is None:
        return cfg
    return dataclasses.replace(
        cfg,
        query=build_query if build_query is not None else cfg.query,
        compile_db=(
            build_compile_db if build_compile_db is not None else cfg.compile_db
        ),
    )


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
    """
    base_build = None
    raw_build_info = build_info
    if build_info is not None and is_pack_dir(build_info):
        base_build = BuildSourcePack.load(build_info).build_evidence
        raw_build_info = None
    raw_sources = sources
    if sources is not None and is_pack_dir(sources):
        if build_info is None:
            base_build = BuildSourcePack.load(sources).build_evidence
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
) -> _L2SeedPackArgs | None:
    """Resolve *build_config*/*sources*/*build_info* into ``collect_inline_pack``
    call arguments, or ``None`` when there is no config to seed from (the
    caller's existing "nothing to apply" degrade).
    """
    cfg = _l2_seed_config(build_config, sources, build_query, build_compile_db)
    if cfg is None:
        return None
    base_build, raw_build_info, raw_sources = _l2_seed_pack_inputs(build_info, sources)
    return _L2SeedPackArgs(
        sources=raw_sources,
        build_info=raw_build_info,
        build_config=cfg,
        build_config_trusted_for_query=(
            build_config is not None or build_query is not None
        ),
        compile_db_explicit=build_compile_db is not None or build_config is not None,
        base_build=base_build,
    )


def _unit_include_dirs(cu: Any) -> list[str]:
    """One compile unit's normal-priority include dirs, structured + argv.

    The compile-DB adapter folds only ``-I``/``-isystem`` into the structured
    ``include_paths``/``system_include_paths``; normal-priority include dirs
    given via ``-iquote`` (GNU) or ``/I`` (MSVC) stay only in argv. The L4
    replay honours those, so L2 must see them too or a build resolving
    dependency headers via ``-iquote deps/include`` fails its header parse with
    no manual ``-I`` (Codex review). Restricted to normal-priority buckets: the
    callers re-emit every seeded dir as plain ``-I``, so promoting an
    after-system dir (``-idirafter``) or a system dir (``-isystem``/``-imsvc``)
    would shadow a system header the build would actually use (Codex review) —
    ``-isystem`` dirs are already carried structurally anyway. Relative operands
    resolve against the unit's ``directory`` (the compile command's cwd) and the
    home-relative ``~`` the adapter stored is expanded.
    """
    from ..header_utils import _build_context_include_dirs

    argv_dirs = (
        _build_context_include_dirs(
            list(cu.argv),
            base_dir=cu.directory or None,
            expand_user=True,
            prefixes=("-I", "-iquote", "/I"),
        )
        if cu.argv
        else set()
    )
    return [*cu.include_paths, *cu.system_include_paths, *sorted(argv_dirs)]


def _existing_include_dirs(units: Iterable[Any]) -> list[str]:
    """De-duplicated, existing include dirs across every compile unit.

    ``CompileDbAdapter`` stores paths through ``DEFAULT_REDACTION``, which
    rewrites the home prefix to a literal ``~`` (e.g. a CI runner's
    ``/home/runner/work`` -> ``~/work``). This derivation is ephemeral and runs
    on the same host as the build, so ``~`` is expanded back before the
    existence check — otherwise every home-rooted include dir (the common CI
    case this fallback targets) would be silently dropped.
    """
    seen: set[str] = set()
    out: list[str] = []
    for cu in units:
        for inc in _unit_include_dirs(cu):
            if not inc:
                continue
            real = os.path.expanduser(inc)
            if real not in seen and Path(real).is_dir():
                seen.add(real)
                out.append(real)
    return out


def derive_l2_include_dirs(
    build_info: Path | None,
    sources: Path | None,
    build_config: Path | None = None,
    *,
    build_query: str | None = None,
    build_compile_db: str | None = None,
    allow_inferred_build_query: bool = True,
) -> tuple[list[str], list[Callable[[], None]]]:
    """Best-effort ``-I``/``-isystem`` dirs from the build's compile DB, + cleanups.

    The L2 public-header parse (castxml/clang over ``-H`` headers) only searches
    the user's ``-I`` inputs and the inferred public-header roots — it does *not*
    see the include directories the build already knows about. When a project's
    public headers ``#include`` a dependency's headers (e.g. EPICS pvxs headers
    including ``<epicsTime.h>``), a ``scan``/``dump`` with just ``--sources`` (no
    explicit ``-I``) then fails to parse them. This resolves the same compile DB
    the L4 replay uses (explicit ``--build-info`` / a trusted ``--config``
    ``build.compile_db``/``build.query`` / auto-discovered ``compile_commands.json``
    / the inferred build-system query) and returns the de-duplicated, existing
    include dirs so the caller can feed them to L2 as a **fallback** (only when the
    user gave no ``-I``).

    Returns ``(include_dirs, cleanups)``. The *cleanups* are the temp-build-dir
    thunks an inferred CMake query appends — an inferred CMake build dir can hold
    generated headers that the returned include dirs point into, so the caller
    **must** run these only *after* the L2 parse has consumed the dirs (thread them
    onto the scan's ``defer_cleanup``); this function never runs them on the success
    path. Purely best-effort: any failure drains the cleanups and returns
    ``([], [])`` so a scan that works today never regresses.
    """
    if sources is None and build_info is None:
        return [], []
    cleanups: list[Callable[[], None]] = []
    try:
        # Pack resolution (config load + any --sources/--build-info pack load)
        # happens inside this same protected section: a corrupt/unreadable
        # pack (bad manifest.json/build_evidence.json) must degrade to "no
        # seeded dirs" like every other failure mode here, not raise through
        # (Codex review — this call used to live inside this try before the
        # shared-helper extraction, and moved ahead of it by mistake).
        args = _resolve_l2_seed_pack_args(
            build_config, sources, build_info, build_query, build_compile_db
        )
        if args is None:
            return [], []
        # Reuse the same L3-collection path embed_build_source drives, restricted
        # to build context only (no L4/L5), so every supported build-info form —
        # a collected pack, a Bazel aquery/cquery, an explicit/auto-discovered/
        # config-located compile DB, or the inferred build-system query — yields
        # the same CompileUnit include dirs the L4 replay would use. Re-deriving
        # this by hand kept missing input forms (packs, bazel); collect_inline_pack
        # owns them, plus the temp-build-dir cleanup lifecycle via defer_cleanup.
        pack = collect_inline_pack(
            sources=args.sources,
            build_info=args.build_info,
            build_config=args.build_config,
            build_config_trusted_for_query=args.build_config_trusted_for_query,
            compile_db_explicit=args.compile_db_explicit,
            allow_inferred_build_query=allow_inferred_build_query,
            base_build=args.base_build,
            layers=("L3",),
            defer_cleanup=cleanups,
        )
        units = (
            pack.build_evidence.compile_units
            if pack is not None and pack.build_evidence is not None
            else []
        )
        out = _existing_include_dirs(units)
        if not out:
            # Nothing to preserve — release any temp build dir now.
            _run_cleanups(cleanups)
            return [], []
        return out, cleanups
    except Exception:  # noqa: BLE001 — best-effort include hint, never fatal
        _run_cleanups(cleanups)
        return [], []


def derive_l2_compile_context(
    headers: list[Path] | tuple[Path, ...],
    build_info: Path | None,
    sources: Path | None,
    build_config: Path | None = None,
    *,
    build_query: str | None = None,
    build_compile_db: str | None = None,
    allow_inferred_build_query: bool = True,
    explicit: CompileContext | None = None,
) -> tuple[CompileContext | None, list[Callable[[], None]]]:
    """Best-effort L2 :class:`CompileContext` derived from the build's L3
    ``CompileUnit`` facts (P0.3).

    *explicit* is the caller's own already-supplied L2 context (typically
    ``evidence.compile``) — forwarded to :func:`~abicheck.buildsource.
    header_compile_context.resolve_header_compile_context` unchanged, so a
    field it already pins (e.g. an explicit ``-std=c++20``) excuses a
    same-field-only disagreement across the matched compile units instead of
    failing closed on it (Finding 3; see that function's own docstring).

    Sibling of :func:`derive_l2_include_dirs`, sharing its exact pack-
    resolution precedence (explicit ``--build-info``/``--sources`` pack ->
    trusted ``--config``/``--build-query`` -> ``build.compile_db`` ->
    auto-discovered ``compile_commands.json`` -> the inferred build-system
    query) via the same :func:`abicheck.buildsource.inline.collect_inline_pack`
    call — kept as an independent call (rather than folded into
    :func:`derive_l2_include_dirs`'s own single call) so this function's
    return shape stays additive and every existing
    ``derive_l2_include_dirs``/``seed_l2_includes`` caller and test is
    unaffected; ``derive_l2_include_dirs`` already runs the identical
    collection once per side for its own include-dir seeding, so a caller
    using both pays for the L3 collection twice per side — an accepted,
    documented cost (the collection itself is already re-run a third time by
    ``embed_build_source`` later in the same pipeline for L3-L5 embedding, so
    this is not a new class of repeated work).

    Returns ``(context, cleanups)`` — ``context`` is ``None`` when there is
    nothing to apply (mirrors :func:`derive_l2_include_dirs`'s ``[]``
    degrade); the *cleanups* are the temp-build-dir thunks an inferred build
    query may have appended, to be run only after the L2 parse has consumed
    the derived context (same contract as ``derive_l2_include_dirs``).

    Propagates :class:`~abicheck.errors.HeaderCompileContextAmbiguousError`
    (P0.3's fail-closed multi-context case) rather than swallowing it — unlike
    every other failure mode here (missing/malformed compile DB, no build
    system, ...), which stays best-effort and degrades silently, a genuine
    ABI-relevant disagreement across compile units must never be resolved by
    silently guessing. This function drains any accumulated *cleanups* itself
    before re-raising on that path (mirroring every other failure branch
    here) — a function that both re-raises and returns a value has no channel
    to hand the exception a value along with it, so the caller receives none
    and has nothing left to run.
    """
    from ..errors import HeaderCompileContextAmbiguousError
    from .header_compile_context import resolve_header_compile_context

    if (sources is None and build_info is None) or not headers:
        return None, []
    cleanups: list[Callable[[], None]] = []
    try:
        # Pack resolution stays inside this protected section for the same
        # reason as derive_l2_include_dirs's own copy of this comment: a
        # corrupt/unreadable pack must degrade best-effort, not raise
        # (Codex review).
        args = _resolve_l2_seed_pack_args(
            build_config, sources, build_info, build_query, build_compile_db
        )
        if args is None:
            return None, []
        pack = collect_inline_pack(
            sources=args.sources,
            build_info=args.build_info,
            build_config=args.build_config,
            build_config_trusted_for_query=args.build_config_trusted_for_query,
            compile_db_explicit=args.compile_db_explicit,
            allow_inferred_build_query=allow_inferred_build_query,
            base_build=args.base_build,
            layers=("L3",),
            defer_cleanup=cleanups,
        )
        build_evidence = pack.build_evidence if pack is not None else None
        resolution = resolve_header_compile_context(
            build_evidence, list(headers), explicit=explicit
        )
        if resolution.context is None:
            _run_cleanups(cleanups)
            return None, []
        return resolution.context, cleanups
    except HeaderCompileContextAmbiguousError:
        # P0.3's fail-closed case: release any temp build dir this attempt
        # created, then propagate — never resolved by silently guessing.
        _run_cleanups(cleanups)
        raise
    except Exception:  # noqa: BLE001 -- best-effort, mirrors derive_l2_include_dirs
        _run_cleanups(cleanups)
        return None, []


def seed_l2_includes(
    *,
    headers: list[Path] | tuple[Path, ...],
    includes: list[Path] | tuple[Path, ...],
    sources: Path | None,
    build_info: Path | None,
    build_config: Path | None,
    defer_cleanup: list[Callable[[], None]] | None,
    build_query: str | None = None,
    build_compile_db: str | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: Sequence[str] = (),
    allow_inferred_build_query: bool = True,
) -> tuple[list[Path], list[Callable[[], None]]]:
    """Augment *includes* with build-derived L2 include dirs (shared by scan+dump).

    When ``-H`` headers are given but the user passed no explicit ``-I``, the L2
    aggregate public-header parse cannot see the include dirs the build already
    knows (pvxs public headers include EPICS Base's ``<epicsTime.h>``). This seeds
    them from :func:`derive_l2_include_dirs` so ``scan``/``dump --sources`` parse
    those headers without a manual ``-I``.

    ``gcc_options``/``gcc_option_tokens`` are the pass-through compile flags
    (``--gcc-options``/``--gcc-option``). Include dirs given through them (e.g.
    ``--gcc-options '-I /sdk/include'``) are as explicit as ``-I``, so the fallback
    treats them the same and stays a no-op — seeding compile-DB dirs as
    ``extra_includes`` on top would front-run the user's SDK in the dumper's search
    order (Codex review).

    ``allow_inferred_build_query`` gates the zero-config inferred build-system query
    (cmake/make/bazel). L2-only callers (``--depth headers`` / ``collect_mode`` "off")
    pass ``False`` so a no-compile-DB tree does not trigger a build just to hint
    includes — passive discovery still applies; only the executing fallback is
    suppressed (Codex review).

    Returns ``(includes, pending_cleanups)``. Temp-build-dir cleanups (an inferred
    CMake dir may hold generated headers the seeded dirs point into) are pushed
    onto *defer_cleanup* when the caller provides one (drained at command end);
    otherwise they are returned as *pending_cleanups* for the caller to run only
    **after** the L2 parse has consumed the dirs. A no-op (returns *includes*
    unchanged, no cleanups) when the seeding conditions do not hold.
    """
    from ..header_utils import _context_tokens, _has_include_build_context

    incs = list(includes)
    # An explicit -I list OR include dirs supplied through --gcc-options/--gcc-option
    # both count as "the user gave includes" — either suppresses the fallback so the
    # user's search precedence is preserved.
    user_gave_includes = bool(incs) or _has_include_build_context(
        _context_tokens(gcc_options, gcc_option_tokens)
    )
    if not (
        headers
        and not user_gave_includes
        and (sources is not None or build_info is not None)
    ):
        return incs, []
    derived, cleanups = derive_l2_include_dirs(
        build_info,
        sources,
        build_config,
        build_query=build_query,
        build_compile_db=build_compile_db,
        allow_inferred_build_query=allow_inferred_build_query,
    )
    if not derived:
        return incs, []
    logger.info(
        "L2 header parse: seeded %d include dir(s) from the build's compile "
        "database (no -I given).",
        len(derived),
    )
    seeded = [Path(d) for d in derived]
    if defer_cleanup is not None:
        defer_cleanup.extend(cleanups)
        return seeded, []
    return seeded, cleanups
