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

import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path

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
    # Mirror embed_build_source's config handling so a trusted --config
    # build.compile_db / build.query is honored here too (and only an explicit
    # --config file is trusted for query execution; an auto-discovered
    # .abicheck.yml is loaded for its non-executable settings but never run).
    cfg_path = build_config or discover_build_config(sources)
    try:
        cfg = load_build_config(cfg_path) if cfg_path is not None else BuildConfig()
    except ValueError:
        # A malformed/invalid config surfaces loudly elsewhere (embed_build_source,
        # the compile-context resolver); this is a best-effort L2 include-dir hint,
        # so degrade to "no seeded dirs" rather than raising through it.
        return [], []
    # Fold the CLI build-DB overrides into cfg exactly as embed_build_source does,
    # so the L2 seeding resolves the *same* DB L3 will (an explicit --build-compile-db
    # / --build-query wins over an auto-discovered one). compile_db_explicit mirrors
    # embed too: when the DB is explicitly configured, a missing glob must *stop*
    # here rather than silently seed from an unrelated auto-discovered/inferred DB.
    if build_query is not None or build_compile_db is not None:
        import dataclasses

        cfg = dataclasses.replace(
            cfg,
            query=build_query if build_query is not None else cfg.query,
            compile_db=build_compile_db
            if build_compile_db is not None
            else cfg.compile_db,
        )
    cfg_trusted_for_query = build_config is not None or build_query is not None
    compile_db_explicit = build_compile_db is not None or build_config is not None
    cleanups: list[Callable[[], None]] = []
    try:
        # Reuse the same L3-collection path embed_build_source drives, restricted
        # to build context only (no L4/L5), so every supported build-info form —
        # a collected pack, a Bazel aquery/cquery, an explicit/auto-discovered/
        # config-located compile DB, or the inferred build-system query — yields
        # the same CompileUnit include dirs the L4 replay would use. Re-deriving
        # this by hand kept missing input forms (packs, bazel); collect_inline_pack
        # owns them, plus the temp-build-dir cleanup lifecycle via defer_cleanup.
        base_build = None
        raw_build_info = build_info
        if build_info is not None and is_pack_dir(build_info):
            base_build = BuildSourcePack.load(build_info).build_evidence
            raw_build_info = None
        raw_sources = sources
        if sources is not None and is_pack_dir(sources):
            # A --sources pack carries its own L3 build_evidence, which
            # embed_build_source/_combine_packs use for L3 when no --build-info does;
            # mirror that so the pack's compile-unit include dirs seed L2 too (Codex).
            # Any explicit --build-info wins L3, so seed from the source pack only
            # when *no* --build-info was given (not merely no build-info *pack*): a
            # raw --build-info must still be resolved by collect_inline_pack below,
            # not skipped by folding the pack into base_build (Codex review).
            if build_info is None:
                base_build = BuildSourcePack.load(sources).build_evidence
            raw_sources = None
        pack = collect_inline_pack(
            sources=raw_sources,
            build_info=raw_build_info,
            build_config=cfg,
            build_config_trusted_for_query=cfg_trusted_for_query,
            compile_db_explicit=compile_db_explicit,
            allow_inferred_build_query=allow_inferred_build_query,
            base_build=base_build,
            layers=("L3",),
            defer_cleanup=cleanups,
        )
        units = (
            pack.build_evidence.compile_units
            if pack is not None and pack.build_evidence is not None
            else []
        )
        from ..header_utils import _build_context_include_dirs

        seen: set[str] = set()
        out: list[str] = []
        for cu in units:
            # The compile-DB adapter folds only -I/-isystem into the structured
            # include_paths/system_include_paths; normal-priority include dirs given
            # via -iquote (GNU) or /I (MSVC) stay only in argv. The L4 replay honours
            # those, so L2 must see them too or a build resolving dependency headers
            # via `-iquote deps/include` fails its header parse with no manual -I
            # (Codex review). Restrict to normal-priority buckets: the callers re-emit
            # every seeded dir as plain -I, so promoting an *after-system* dir
            # (-idirafter) or a system dir (-isystem/-imsvc) would shadow a system
            # header the build would actually use (Codex review) — -isystem dirs are
            # already carried structurally anyway. Resolve relative operands against
            # the unit's `directory` (the compile command's cwd) and un-redact the
            # home-relative `~` the adapter stored. Union, deduped.
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
            for inc in (*cu.include_paths, *cu.system_include_paths, *sorted(argv_dirs)):
                if not inc:
                    continue
                # CompileDbAdapter stores paths through DEFAULT_REDACTION, which
                # rewrites the home prefix to a literal ``~`` (e.g. a CI runner's
                # /home/runner/work -> ~/work). This derivation is ephemeral and
                # runs on the same host as the build, so expand ~ back before the
                # existence check — otherwise every home-rooted include dir (the
                # common CI case this fallback targets) would be silently dropped.
                real = os.path.expanduser(inc)
                if real not in seen and Path(real).is_dir():
                    seen.add(real)
                    out.append(real)
        if not out:
            # Nothing to preserve — release any temp build dir now.
            _run_cleanups(cleanups)
            return [], []
        return out, cleanups
    except Exception:  # noqa: BLE001 — best-effort include hint, never fatal
        _run_cleanups(cleanups)
        return [], []


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
        build_info, sources, build_config,
        build_query=build_query, build_compile_db=build_compile_db,
        allow_inferred_build_query=allow_inferred_build_query,
    )
    if not derived:
        return incs, []
    logger.info(
        "L2 header parse: seeded %d include dir(s) from the build's compile "
        "database (no -I given).", len(derived),
    )
    seeded = [Path(d) for d in derived]
    if defer_cleanup is not None:
        defer_cleanup.extend(cleanups)
        return seeded, []
    return seeded, cleanups
