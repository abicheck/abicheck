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
from .build_evidence import BuildEvidence
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


class _Uncollected:
    """Sentinel default for ``evidence=`` (P0.3), distinguishing "no shared
    evidence was given -- collect your own" from "a caller already collected
    and is sharing an explicit ``None`` (found nothing)" -- a bare ``None``
    default could not tell these apart, and treating a shared "found
    nothing" as "not given" would silently trigger the exact redundant
    second collection this parameter exists to avoid. Never instantiated
    more than once; see :data:`_UNCOLLECTED`.
    """


_UNCOLLECTED = _Uncollected()


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


def _collect_l2_seed_build_evidence(
    build_info: Path | None,
    sources: Path | None,
    build_config: Path | None,
    build_query: str | None,
    build_compile_db: str | None,
    allow_inferred_build_query: bool,
) -> tuple[BuildEvidence | None, list[Callable[[], None]]]:
    """The L3-only collection :func:`derive_l2_include_dirs` and
    :func:`derive_l2_compile_context` each used to run independently (P0.3) —
    factored out so a caller needing both (``resolve_side_snapshot``) can pay
    for it once via :func:`collect_l2_seed_evidence` instead of twice.

    Best-effort: any failure drains the cleanups already accumulated and
    returns ``(None, [])``, same as each sibling function's own former
    inline copy of this logic.
    """
    if sources is None and build_info is None:
        return None, []
    cfg = _l2_seed_config(build_config, sources, build_query, build_compile_db)
    if cfg is None:
        return None, []
    cfg_trusted_for_query = build_config is not None or build_query is not None
    compile_db_explicit = build_compile_db is not None or build_config is not None
    cleanups: list[Callable[[], None]] = []
    try:
        base_build, raw_build_info, raw_sources = _l2_seed_pack_inputs(
            build_info, sources
        )
        # Reuse the same L3-collection path embed_build_source drives, restricted
        # to build context only (no L4/L5), so every supported build-info form —
        # a collected pack, a Bazel aquery/cquery, an explicit/auto-discovered/
        # config-located compile DB, or the inferred build-system query — yields
        # the same CompileUnit include dirs the L4 replay would use. Re-deriving
        # this by hand kept missing input forms (packs, bazel); collect_inline_pack
        # owns them, plus the temp-build-dir cleanup lifecycle via defer_cleanup.
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
        return (pack.build_evidence if pack is not None else None), cleanups
    except Exception:  # noqa: BLE001 — best-effort, never fatal
        _run_cleanups(cleanups)
        return None, []


def collect_l2_seed_evidence(
    build_info: Path | None,
    sources: Path | None,
    build_config: Path | None = None,
    *,
    build_query: str | None = None,
    build_compile_db: str | None = None,
    allow_inferred_build_query: bool = False,
) -> tuple[BuildEvidence | None, list[Callable[[], None]]]:
    """Collect once the L3 evidence :func:`derive_l2_include_dirs` and
    :func:`derive_l2_compile_context` would otherwise each independently
    re-derive for the same side (P0.3) — call this once per side and pass
    the result to both via their ``evidence=`` parameter.

    ``allow_inferred_build_query`` defaults to ``False`` here, unlike the two
    functions above's own ``True`` default: this is the entry point a Tier-2
    API caller (``service_input_resolution.resolve_side_snapshot``) uses, and
    per :func:`seed_l2_includes`'s own docstring, "a Tier-2 API call must
    never execute a build system (cmake/make/bazel) as a side effect of
    resolving an input." Pass ``True`` explicitly only once it's established
    that running an inferred query for this side is appropriate.

    Returns ``(evidence, cleanups)`` — the *cleanups* are the temp-build-dir
    thunks an inferred build query may have appended, to be run only after
    every consumer of *evidence* (the L2 parse, and anything else fed this
    result) has finished with it.
    """
    return _collect_l2_seed_build_evidence(
        build_info,
        sources,
        build_config,
        build_query,
        build_compile_db,
        allow_inferred_build_query,
    )


def derive_l2_include_dirs(
    build_info: Path | None,
    sources: Path | None,
    build_config: Path | None = None,
    *,
    build_query: str | None = None,
    build_compile_db: str | None = None,
    allow_inferred_build_query: bool = True,
    evidence: BuildEvidence | None | _Uncollected = _UNCOLLECTED,
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

    *evidence* (P0.3): a caller that already collected L3 evidence for this
    exact ``(sources, build_info, ...)`` request via
    :func:`collect_l2_seed_evidence` passes it here to skip a second,
    redundant collection — including an explicit ``None`` (collection ran and
    found nothing), which must still short-circuit rather than silently
    triggering a repeat attempt; the default sentinel :data:`_UNCOLLECTED`
    means "no shared evidence given," preserving today's behaviour for every
    other caller (``cli_dump_helpers.py``, ``scan_engine.py``).

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
    if not isinstance(evidence, _Uncollected):
        units = evidence.compile_units if evidence is not None else []
        return _existing_include_dirs(units), []
    build_evidence, cleanups = _collect_l2_seed_build_evidence(
        build_info,
        sources,
        build_config,
        build_query,
        build_compile_db,
        allow_inferred_build_query,
    )
    units = build_evidence.compile_units if build_evidence is not None else []
    out = _existing_include_dirs(units)
    if not out:
        # Nothing to preserve — release any temp build dir now.
        _run_cleanups(cleanups)
        return [], []
    return out, cleanups


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
    evidence: BuildEvidence | None | _Uncollected = _UNCOLLECTED,
) -> tuple[CompileContext | None, list[Callable[[], None]]]:
    """Best-effort L2 :class:`CompileContext` derived from the build's L3
    ``CompileUnit`` facts (P0.3).

    *explicit* is the caller's own already-supplied L2 context (typically
    ``evidence.compile`` on the caller's own ``SideEvidence`` — a different
    object from this function's own *evidence* parameter below, despite the
    name collision) — forwarded to :func:`~abicheck.buildsource.
    header_compile_context.resolve_header_compile_context` unchanged, so a
    field it already pins (e.g. an explicit ``-std=c++20``) excuses a
    same-field-only disagreement across the matched compile units instead of
    failing closed on it (Finding 3; see that function's own docstring).

    *evidence*: a caller that already collected L3 evidence for this exact
    ``(sources, build_info, ...)`` request via :func:`collect_l2_seed_evidence`
    passes it here to skip :func:`derive_l2_include_dirs`'s sibling collection
    — see that function's own *evidence* docstring for the sentinel/``None``
    distinction, identical here.

    Sibling of :func:`derive_l2_include_dirs`, sharing its exact pack-
    resolution precedence (explicit ``--build-info``/``--sources`` pack ->
    trusted ``--config``/``--build-query`` -> ``build.compile_db`` ->
    auto-discovered ``compile_commands.json`` -> the inferred build-system
    query) via the same :func:`abicheck.buildsource.inline.collect_inline_pack`
    call. A caller that needs both this function's and
    :func:`derive_l2_include_dirs`'s result for the same side should collect
    once via :func:`collect_l2_seed_evidence` and pass it to both — see
    ``service_input_resolution.resolve_side_snapshot``, the one caller doing
    so today. The embed step later in the same pipeline
    (``embed_build_source``) still runs its own, separate collection: unlike
    these two opportunistic L2-parse improvements it is the caller's explicit
    request for L3+ evidence and (unlike them) always permits the inferred
    build query, so merging it into this shared result would either narrow
    its own permission or widen these two functions' — deliberately left
    unmerged (see docs/contribute/performance.md "Coverage gaps this
    workflow does not close").

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
    silently guessing. This holds regardless of whether *evidence* was freshly
    collected here or shared in — the ambiguity is a property of the evidence
    itself, not of how it was obtained. The caller is responsible for running
    any accumulated *cleanups* before letting the exception propagate
    further, since this function's own ``except`` cannot both re-raise and
    return them.
    """
    from ..errors import HeaderCompileContextAmbiguousError
    from .header_compile_context import resolve_header_compile_context

    if (sources is None and build_info is None) or not headers:
        return None, []
    build_evidence: BuildEvidence | None
    cleanups: list[Callable[[], None]]
    if not isinstance(evidence, _Uncollected):
        build_evidence, cleanups = evidence, []
    else:
        build_evidence, cleanups = _collect_l2_seed_build_evidence(
            build_info,
            sources,
            build_config,
            build_query,
            build_compile_db,
            allow_inferred_build_query,
        )
    try:
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
    evidence: BuildEvidence | None | _Uncollected = _UNCOLLECTED,
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

    *evidence* (P0.3): forwarded unchanged to :func:`derive_l2_include_dirs` —
    see that function's own *evidence* docstring. Left as the default
    sentinel by every existing caller except ``service_input_resolution``'s
    ``_seeded_includes``, which shares one :func:`collect_l2_seed_evidence`
    result between this function and its sibling
    :func:`derive_l2_compile_context` call instead of each collecting L3
    evidence independently.

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
        evidence=evidence,
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
