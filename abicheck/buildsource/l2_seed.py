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

from .._compiler_options import split_gcc_options
from ..compile_context import CompileContext
from . import pack_io
from .inline import (
    BuildConfig,
    _run_cleanups,
    collect_inline_pack,
    discover_build_config,
    is_pack_dir,
    load_build_config,
)

logger = logging.getLogger(__name__)


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

    ``build_targets`` (P0.2, Codex review): folded in the same way, so an
    explicit ``--build-target`` scopes the L2 include/compile-context seed's
    own ``collect_inline_pack`` call identically to ``embed_build_source``'s
    L3/L4/L5 collection -- without this, a multi-target Bazel workspace could
    seed L2 from an unrelated target's include dirs/dialect flags even though
    the caller explicitly scoped L3 evidence to one root, producing a snapshot
    parsed under the wrong compile context despite target-scoped L3 evidence.

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

    Scans exactly the *units* it is handed, and it is the caller's job to hand
    it the narrowest honest set. :func:`seed_includes_and_fold_compile_context`
    passes ``HeaderCompileContextResolution.matched_units`` whenever the fold
    matched any (plan PR 3B / PR D), closing the gap where an unrelated TU's
    own generated-header directory could ride along in this seed and shadow
    the matched TU's own colliding header — on a run that then stamped
    ``parsed_with_build_context`` from the (separate) successful fold, so it
    read as *more* authoritative than the seed actually was. It falls back to
    every compile unit only when nothing matched, which is the case this seed
    was built for in the first place (a public header the compile DB does not
    cover, reaching into a dependency SDK) and where there is no narrower set
    to prefer.

    :func:`derive_l2_include_dirs` still passes every unit, and correctly so:
    it takes no ``headers`` argument at all, so it has nothing to match
    against.
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
    build_targets: tuple[str, ...] = (),
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
            build_config,
            sources,
            build_info,
            build_query,
            build_compile_db,
            build_targets,
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
    build_targets: tuple[str, ...] = (),
    allow_inferred_build_query: bool = True,
    explicit: CompileContext | None = None,
    lang: str | None = None,
    lang_explicit: bool = False,
) -> tuple[CompileContext | None, list[Callable[[], None]]]:
    """Best-effort L2 :class:`CompileContext` derived from the build's L3
    ``CompileUnit`` facts (P0.3).

    *explicit* is the caller's own already-supplied L2 context (typically
    ``evidence.compile``) — forwarded to :func:`~abicheck.buildsource.
    header_compile_context.resolve_header_compile_context` unchanged, so a
    field it already pins (e.g. an explicit ``-std=c++20``) excuses a
    same-field-only disagreement across the matched compile units instead of
    failing closed on it (Finding 3; see that function's own docstring).

    *lang*/*lang_explicit* (``discussion_r3787398644``, Codex review):
    forwarded unchanged to :func:`~abicheck.buildsource.
    header_compile_context.resolve_header_compile_context` — the same
    additive, default-``None``/``False`` threading pattern as *explicit*
    above, so a caller's explicitly-forced parse language (e.g.
    ``DumpRequest(lang="c++", lang_explicit=True)``) suppresses a matched
    compile unit's own derived ``-std=`` when its language family conflicts,
    rather than forwarding a C-family standard into a forced-C++ parse (see
    that function's own docstring for the confirmed repro).

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
            build_config,
            sources,
            build_info,
            build_query,
            build_compile_db,
            build_targets,
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
            build_evidence,
            list(headers),
            explicit=explicit,
            lang=lang,
            lang_explicit=lang_explicit,
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


def _merge_l3_compile_context(
    explicit: CompileContext | None, derived: CompileContext | None
) -> CompileContext | None:
    """Fold *derived* (L3-derived, P0.3) ahead of *explicit* (user-supplied).

    Mirrors ``-p``/``--compile-db``'s existing precedence for ``dump``
    (``cli_helpers_compare._merge_gcc_options``): the build-derived flags lead
    and the caller's own explicit representation is appended after — so an
    explicit, later token still wins any literal redefinition (e.g. a
    caller's own ``-DFOO=2`` after a derived ``-DFOO=1`` — the compiler uses
    the last ``-D`` for a given macro) without this function needing to know
    which tokens actually conflict. ``derived`` with no tokens at all (a
    matched compile unit with nothing ABI-relevant to forward — still real
    evidence, see ``header_compile_context``'s own docstring) is a no-op here;
    the caller still stamps ``parsed_with_build_context`` in that case since
    context genuinely *was* resolved and applied (as the empty flag list).

    Finding 2: "derived leads, explicit wins" only holds if *every*
    representation of the explicit value actually lands after every derived
    token in the rendered command — not just ``gcc_option_tokens`` entries.
    Both header command builders (``dumper_ast_config._build_castxml_command``/
    ``_build_clang_header_command``) render the structured ``sysroot`` field
    and the free-form ``gcc_options`` string *before* ``gcc_option_tokens``,
    so merely prepending ``derived.gcc_option_tokens`` to
    ``explicit.gcc_option_tokens`` (as before) left ``explicit.sysroot``/
    ``explicit.gcc_options`` — rendered earlier in the command — silently
    overridden by a later, conflicting derived token instead of winning.
    Folding both structured representations into trailing tokens (and
    clearing the structured fields, so the command builders no longer also
    emit them in their old, too-early position) puts every explicit
    representation strictly after every derived one, regardless of which of
    the three explicit channels (``sysroot``, ``gcc_options``,
    ``gcc_option_tokens``) it came through.

    Moved here from ``service_input_resolution.py`` (P0.3 dump-path fold):
    that module already imports :func:`derive_l2_compile_context` from this
    one, so this function living there too would have closed a
    ``l2_seed -> service_input_resolution -> cli_dump_helpers -> l2_seed``
    import cycle once ``cli_dump_helpers`` needed it directly (AGENTS.md
    "What NOT to do" -- prefer a leaf module both sides can depend on over
    extending ``IMPORT_CYCLE_ALLOWLIST``). ``service_input_resolution``'s
    own ``_seeded_includes_and_compile_context`` (PR C, typed dump/scan
    convergence) reaches it indirectly through
    :func:`seed_includes_and_fold_compile_context` below instead.

    "Derived leads, explicit wins" is *not* the right rule for an include
    search path (Codex review): unlike a macro/std/sysroot switch, which a
    real compiler resolves last-flag-wins, ``-I``/``-isystem`` entries are
    first-match-wins, so putting a derived ``CompileUnit.include_paths``/
    ``system_include_paths`` entry (:func:`header_compile_context.
    _context_flags` renders both) ahead of an explicit one silently prefers
    the build's own header over a caller's explicit override for a
    colliding basename. :func:`_split_include_tokens` carves derived's own
    include-search entries out of the leading (last-flag-wins) group and
    appends them *after* explicit instead, so explicit's own ``-I``/
    ``-isystem`` (wherever it came from) always searches first, while every
    other derived token keeps its original leading, overridable position.

    Finding 3 (P1 review, ``discussion_r3787772668``, P0.3 follow-up round
    2): ``derived.gcc_path`` (``header_compile_context._derived_gcc_path`` —
    the matched compile unit's own compiler, set when it is genuinely
    MSVC/clang-cl-dialect) was never read here at all, only ``derived``'s
    option-token fields — so even once ``resolve_header_compile_context``
    started returning a real ``gcc_path``, this merge silently discarded it,
    and the L2 header parse still defaulted to a plain ``clang++`` that
    cannot parse the retained ``/std:`` survivor. Unlike ``sysroot``/
    ``gcc_options`` (foldable, "derived leads, explicit wins" via
    trailing-token order), ``gcc_path``/``gcc_prefix`` each select a single
    compiler and cannot be folded the same way — the natural per-field rule
    mirrors this module's other explicit-pin precedents instead:
    ``explicit.gcc_path`` (or ``gcc_prefix``) wins outright when the caller
    already set one, and ``derived``'s value is used only when the caller
    left it unset (the default). A caller's own explicit ``--gcc-path`` was
    always going to be the correct choice regardless of what the matched
    compile unit used, so this can only ever fill in a gap, never override
    an explicit choice.

    **``gcc_path``/``gcc_prefix`` are one logical compiler-selector, not two
    independent fields (P2 review, ``discussion_r3788073754``, fresh
    evidence).** ``dumper_clang._resolve_clang_bin`` always checks
    ``gcc_path`` before ``gcc_prefix`` (a resolvable ``gcc_path`` wins
    outright; ``gcc_prefix`` is only ever consulted when ``gcc_path`` is
    absent or not clang-family) — so treating the two fields as
    independently "derived fills an unset explicit field" broke a caller who
    explicitly set *only* ``gcc_prefix`` (meaning "use this prefix, no
    literal-path override"): a *different* ``derived.gcc_path`` from the
    matched compile unit would still get merged in for the unset
    ``explicit.gcc_path`` slot, and since ``_resolve_clang_bin`` checks
    ``gcc_path`` first, the caller's actual intent (the explicit prefix) was
    silently overridden by the derived path instead of winning. Fixed by
    resolving both fields together as a single unit: if the caller
    explicitly set *either* one, neither is inherited from ``derived`` (even
    when the caller's own other field is unset) — only when the caller set
    *neither* is ``derived``'s own ``(gcc_path, gcc_prefix)`` pair adopted,
    together, from the same source.
    """
    if derived is None:
        return explicit
    if explicit is None:
        return derived
    explicit_tail: list[str] = []
    if explicit.sysroot is not None:
        explicit_tail.append(f"--sysroot={explicit.sysroot.as_posix()}")
    if explicit.gcc_options:
        try:
            explicit_tail.extend(split_gcc_options(explicit.gcc_options))
        except ValueError:
            # Malformed --gcc-options must not abort the merge (mirrors
            # _compiler_options.explicit_language_standard's own handling of
            # the identical failure mode) -- fall back to forwarding it
            # verbatim as one token so it is at least still present, rather
            # than silently dropped.
            explicit_tail.append(explicit.gcc_options)
    derived_rest, derived_includes = _split_include_tokens(derived.gcc_option_tokens)
    # Drop a derived include-search pair whose directory is already covered
    # by *explicit*'s own include-search entries -- both the structured
    # ``--sysroot=``/``gcc_options`` string just folded into `explicit_tail`
    # and `explicit.gcc_option_tokens` itself can independently carry an
    # ``-I``/``-isystem`` for the same directory `derived` resolves too (the
    # legacy ``-p``/``--compile-db`` matcher and this P0.3 fold both derive
    # from the *same* compile database when a caller's ``--build-info``
    # doubles as both). Left undeduped, the merged `gcc_option_tokens`
    # carries the identical include-search flag twice -- confirmed via a
    # minimal, castxml-free repro (AGENTS.md's L3->L2-fold "nineteenth
    # finding") -- and `_context_flags`'s own separate include-path
    # rendering plus this legacy string can jointly produce an
    # `include_sequence` a `scan --against` candidate resolution (whose
    # single-pass fold never double-derives the same directory) never
    # reproduces for the identical project. "Explicit wins, searches
    # first" is unaffected: only the later, redundant `derived` copy is
    # dropped, never the earlier explicit one.
    from ..header_utils import drop_include_tokens_duplicating_paths

    derived_includes = tuple(
        drop_include_tokens_duplicating_paths(
            derived_includes, explicit_tail + list(explicit.gcc_option_tokens)
        )
    )
    explicit_selector_set = (
        explicit.gcc_path is not None or explicit.gcc_prefix is not None
    )
    if explicit_selector_set:
        gcc_path = explicit.gcc_path
        gcc_prefix = explicit.gcc_prefix
    else:
        gcc_path = derived.gcc_path
        gcc_prefix = derived.gcc_prefix
    return dataclasses.replace(
        explicit,
        sysroot=None,
        gcc_options=None,
        gcc_option_tokens=(
            *derived_rest,
            *explicit_tail,
            *explicit.gcc_option_tokens,
            *derived_includes,
        ),
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
    )


def _split_include_tokens(
    tokens: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split *tokens* into ``(non_include, include)``, preserving order.

    An include-search flag given as a separate operand (``-I dir`` /
    ``/imsvc dir`` — the token equals the *bare* prefix exactly) consumes the
    next token as its directory operand, so both travel together into
    *include*; an attached form (``-Idir``) is self-contained. Mirrors
    ``header_utils._flag_tokens``'s identical spaced-vs-attached distinction.

    Known gap (Codex review, not fixed): this preserves the relative order
    *within* the derived include tokens, but does not distinguish GCC/Clang's
    distinct include-search *buckets* (``-iquote`` > ``-I`` > ``-isystem`` >
    ``-idirafter``, each a separate search class regardless of argv
    position) from a plain flat list. An explicit ``-isystem`` therefore
    still searches ahead of a derived ``-iquote``/``-I`` after this split,
    even though a real compiler would consult the quote/regular buckets
    first regardless of flag order. Closing this needs the merge to track
    bucket membership, not just include-vs-non-include -- a real, if narrow,
    redesign of this function's output shape, not a follow-up to the
    explicit-vs-derived ordering fix this function exists for.
    """
    from ..header_utils import _INCLUDE_FLAG_PREFIXES

    non_include: list[str] = []
    include: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if t in _INCLUDE_FLAG_PREFIXES and i + 1 < n:
            include.append(t)
            include.append(tokens[i + 1])
            i += 2
        elif any(t.startswith(p) for p in _INCLUDE_FLAG_PREFIXES):
            include.append(t)
            i += 1
        else:
            non_include.append(t)
            i += 1
    return tuple(non_include), tuple(include)


def _include_operand_dirs(tokens: tuple[str, ...]) -> tuple[Path, ...]:
    """Every directory in *tokens* an AST cache key must cover for staleness.

    Thin alias for :func:`~abicheck.header_utils.cache_relevant_operand_paths`
    -- the extraction moved to ``header_utils`` (a leaf module ``service.py``
    already imports from too) once ``service._attach_header_graph``'s own
    independent second header parse needed the identical extraction for its
    own ``gcc_option_tokens`` (Codex review), and widened from include-search
    dirs alone to their union with the dirs holding forced pre-included
    headers once the L3->L2 fold started deriving those too (plan PR 3B /
    PR D). Kept here under its original private name so this module's own
    callers/tests don't need updating.
    """
    from ..header_utils import cache_relevant_operand_paths

    return cache_relevant_operand_paths(tokens)


def seed_includes_and_fold_compile_context(
    *,
    headers: list[Path] | tuple[Path, ...],
    includes: list[Path] | tuple[Path, ...],
    sources: Path | None,
    build_info: Path | None,
    build_config: Path | None,
    build_query: str | None,
    build_compile_db: str | None,
    build_targets: tuple[str, ...] = (),
    collect_mode: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    sysroot: Path | None,
    nostdinc: bool,
    frontend: str,
    frontend_context: str,
    lang: str,
    lang_explicit: bool,
    pending_cleanups: list[Callable[[], None]],
    source_filter: str | None = None,
) -> tuple[list[Path], bool, CompileContext, tuple[Path, ...]]:
    """The L2 include-dir seed and the P0.3 L3->L2 compile-context fold,
    combined into one L3 collection (Codex review, PR #782).

    The two were originally two independent calls -- an inline
    ``seed_l2_includes()`` call followed immediately by a since-removed
    ``fold_l3_compile_context()`` helper -- each running its own
    :func:`~abicheck.buildsource.inline.collect_inline_pack` -- harmless when
    at most one can trigger the zero-config *inferred* build-system query
    (cmake/make/bazel), but a caller passing the same ``--sources`` tree
    through both, with an inferred query genuinely needed (no existing
    compile database), hit a real deadlock-shaped bug: the first call's
    claim on the deterministic build-dir lock (``build_query.
    _claim_inferred_build_dir``) is a real ``flock`` held until its own
    *cleanup* runs -- which is deliberately deferred until after the L2
    header parse has consumed the seeded dirs, i.e. long after this
    function returns -- so the second call's own inferred-query attempt
    would contend on the identical lock and wait up to the 600s timeout
    before falling back to a throwaway sibling dir. This function collects
    the L3 evidence exactly once and derives both results from it, so only
    one inferred query -- if any -- ever runs. All three ``dump``/``scan``
    call sites that previously chained the two separate helpers now call
    this one instead, which is why the older ``fold_l3_compile_context``
    wrapper (once the shared primitive behind all three) was removed
    entirely rather than left as dead code alongside it.

    Returns ``(includes, l3_context_applied, l3_effective_context,
    derived_include_dirs)`` -- ``includes`` is *includes* augmented with any
    build-derived seed dirs (mirrors ``seed_l2_includes``'s own return);
    ``l3_context_applied`` is ``True`` only when a real L3 context was found
    and folded in (the caller's signal for stamping
    ``AbiSnapshot.parsed_with_build_context``); ``l3_effective_context`` is
    always real -- the explicit one unchanged when nothing was found, or the
    merged result otherwise; ``derived_include_dirs`` is every directory a
    derived ``-I``/``-isystem``/etc. token names (empty when nothing was
    found), for a caller to fold into its own AST cache key's
    ``extra_hash_dirs`` -- these dirs reach the header parse only as opaque
    ``gcc_option_tokens`` strings, not as ``extra_includes``, so the cache
    key's directory-mtime hashing would otherwise never cover them (Codex
    review): editing a header under a derived include dir would otherwise
    reuse a stale cached AST. Any temp-build-dir cleanups are appended to
    *pending_cleanups* in place, to be drained only after the L2 parse has
    consumed both the seeded include dirs and the derived compile context's
    own directories.

    *source_filter* is ``dump --compile-db-filter``'s glob, narrowing the
    compile units both halves consider -- the fold's own matching *and* the
    include-dir seed's fallback set, so a filtered run cannot fold one
    translation unit's context while seeding another's include dirs. See
    :func:`~abicheck.buildsource.header_compile_context.filter_units_by_source`
    for the matching rules and the deliberate "a filter matching nothing keeps
    every unit" fallback. ``None`` (every caller but the ELF ``dump`` CLI) is
    a no-op.

    May raise :class:`~abicheck.errors.HeaderCompileContextAmbiguousError`
    when the matched compile units genuinely disagree on an unpinned
    ABI-relevant dimension -- callers should run this inside the same
    ``try`` block that converts the main header-AST parse's own errors to a
    ``click.ClickException``. A *source_filter* that selects a single unit is
    one of the documented ways to resolve exactly that -- and, before it was
    threaded here, the one the error message named without it working.
    """
    from ..errors import HeaderCompileContextAmbiguousError
    from ..header_utils import _context_tokens, _has_include_build_context
    from .header_compile_context import (
        filter_units_by_source,
        resolve_header_compile_context,
    )

    explicit_ctx = CompileContext(
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot,
        nostdinc=nostdinc,
        frontend=frontend,
        frontend_context=frontend_context,
    )
    incs = list(includes)
    # Mirrors seed_l2_includes' own gating exactly: an explicit -I list OR
    # include dirs supplied through --compiler-option both suppress the
    # include-dir fallback so the user's own search precedence is kept.
    user_gave_includes = bool(incs) or _has_include_build_context(
        _context_tokens(gcc_options, gcc_option_tokens)
    )
    want_seed = bool(headers) and not user_gave_includes
    # The second clause reduces to `not headers`: want_seed is False whenever
    # headers is empty (bool(headers) is its first operand), so "not want_seed
    # and not headers" can only ever equal "not headers" -- confirmed via
    # CodeRabbit review, which also flagged the now-removed `if not headers:`
    # guard a few lines below as unreachable for the identical reason.
    if (sources is None and build_info is None) or not headers:
        return incs, False, explicit_ctx, ()

    cleanups: list[Callable[[], None]] = []
    try:
        # Pack resolution stays inside this protected section, mirroring
        # derive_l2_include_dirs's/derive_l2_compile_context's own identical
        # comment: a corrupt/unreadable pack must degrade best-effort, not
        # raise (Codex review) -- an earlier revision of this function had it
        # outside the try, which reintroduced exactly that regression.
        args = _resolve_l2_seed_pack_args(
            build_config,
            sources,
            build_info,
            build_query,
            build_compile_db,
            build_targets,
        )
        if args is None:
            return incs, False, explicit_ctx, ()
        pack = collect_inline_pack(
            sources=args.sources,
            build_info=args.build_info,
            build_config=args.build_config,
            build_config_trusted_for_query=args.build_config_trusted_for_query,
            compile_db_explicit=args.compile_db_explicit,
            allow_inferred_build_query=collect_mode != "off",
            base_build=args.base_build,
            layers=("L3",),
            defer_cleanup=cleanups,
        )
        build_evidence = pack.build_evidence if pack is not None else None
        units = filter_units_by_source(
            build_evidence.compile_units if build_evidence is not None else [],
            source_filter,
        )

        # Resolve *before* seeding, so the seed can be restricted to the
        # compile unit(s) that actually compile these headers (plan PR 3B /
        # PR D). Ordering is otherwise unobservable: the one path that does
        # not fall through to the seed below is the fail-closed ambiguity
        # raise, which aborts the whole call either way.
        resolution = resolve_header_compile_context(
            build_evidence,
            list(headers),
            explicit=explicit_ctx,
            lang=lang,
            lang_explicit=lang_explicit,
            source_filter=source_filter,
        )

        if want_seed:
            seed_units = list(resolution.matched_units) or units
            seeded_dirs = _existing_include_dirs(seed_units)
            if seeded_dirs:
                logger.info(
                    "L2 header parse: seeded %d include dir(s) from %s (no -I given).",
                    len(seeded_dirs),
                    (
                        f"the {len(resolution.matched_units)} compile unit(s) "
                        "matching these headers"
                        if resolution.matched_units
                        else "the build's compile database"
                    ),
                )
                incs = incs + [Path(d) for d in seeded_dirs]

        if resolution.context is None:
            if cleanups:
                pending_cleanups.extend(cleanups)
            return incs, False, explicit_ctx, ()
        # Hand *cleanups* over to the caller's own list only after every
        # remaining fallible step has succeeded -- extending earlier (an
        # earlier revision did it right after resolve_header_compile_context)
        # left the same thunks in both pending_cleanups and this function's
        # own `cleanups`, so a _merge_l3_compile_context/_include_operand_dirs
        # failure below would have the except branch's _run_cleanups(cleanups)
        # remove them a second time -- not fatal (_run_cleanups logs rather
        # than raises on an already-closed handle), but still a real, visible
        # double-removal a caller's own debug log would show (Codex/
        # CodeRabbit review).
        merged = _merge_l3_compile_context(explicit_ctx, resolution.context)
        assert merged is not None  # both args non-None -> always merges
        dirs = _include_operand_dirs(resolution.context.gcc_option_tokens)
        if cleanups:
            pending_cleanups.extend(cleanups)
        return incs, True, merged, dirs
    except HeaderCompileContextAmbiguousError:
        # P0.3's fail-closed case: release any temp build dir this attempt
        # created, then propagate -- never resolved by silently guessing.
        _run_cleanups(cleanups)
        raise
    except Exception:  # noqa: BLE001 -- best-effort, mirrors derive_l2_include_dirs
        _run_cleanups(cleanups)
        return list(includes), False, explicit_ctx, ()


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
    build_targets: tuple[str, ...] = (),
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
    (``--compiler-option``). Include dirs given through them (e.g.
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
    # An explicit -I list OR include dirs supplied through --compiler-option
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
        build_targets=build_targets,
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
