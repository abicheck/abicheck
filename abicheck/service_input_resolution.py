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

"""One input side, resolved — the primitives ``compare`` and ``dump`` share.

G33 Phase 5 (ADR-055 D1's shape, applied to ``dump``). ``compare`` already had
exactly one resolution implementation for a *pair* of inputs
(:mod:`abicheck.service_compare_pipeline`); ``dump`` had none — the MCP
``abi_dump`` tool called :func:`abicheck.service.resolve_input` with a fixed
five-argument subset and could not express ``--depth``/``--sources``/
``--build-info``/``--dump-manifest``/a :class:`CompileContext` at all. Giving
``dump`` a typed request meant either a second copy of the per-side work
``compare`` does, or lifting that work out of the pair. This module is the
second option: everything here was ``service_compare_pipeline``'s, moved
verbatim and re-expressed for *one* side, so a change to how an input resolves
lands on both commands at once.

The pair-shaped decisions deliberately stayed behind in
``service_compare_pipeline``: the pair-wide C++20 dialect override exists
precisely because two sides must agree on a standard, and the concurrency rule
is about two extractions running at once. Neither means anything for a single
dump.

Same mechanical note as ``service_compare_pipeline``: everything this module
needs from ``service`` is looked up **through the module object at call time**
(``from . import service`` inside the function), never bound at import time, so
``monkeypatch.setattr(service, "resolve_input", ...)`` keeps working. The
function-local import also keeps this module out of ``service``'s import cycle
(AGENTS.md "What NOT to do").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .errors import SnapshotError, ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from .api_types import InputSpec
    from .compile_context import CompileContext
    from .model import AbiSnapshot
    from .service_compare_evidence import SideEvidence

__all__ = [
    "SideResolution",
    "embed_side_build_source",
    "enforce_requested_depth",
    "is_raw_source_tree",
    "reject_hybrid_source_frontend",
    "resolve_side_snapshot",
]


@dataclass(frozen=True, slots=True)
class SideResolution:
    """One resolved input side, plus the L2/P0.3 fold's own effective values.

    PR 3A (dump/scan resolver convergence, CLI cleanup phase two). Everything
    :func:`resolve_side_snapshot` already computed internally -- the seeded
    ``includes`` and the folded :class:`CompileContext`, both discarded after
    use -- but callers with a post-resolution hook that must agree with the
    primary parse (``perform_elf_dump``'s ADR-039 build-context collector and
    header-graph second pass; ``scan_engine``'s pair-aware baseline-context
    reuse decision) need these values themselves, not just the snapshot. See
    :func:`_resolve_side_snapshot_impl`.

    **Lifetime caveat (Codex review, fresh evidence)**: when the fold ran a
    trusted, zero-config *inferred* build-system query (no existing compile
    database), the temporary build directory ``effective_includes``/
    ``effective_compile_context`` were seeded from is already deleted by the
    time this object is returned -- ``_resolve_side_snapshot_impl``'s own
    ``finally`` drains that cleanup right after the primary parse has
    consumed it, deliberately, to release its exclusive lock before a
    sibling collection (e.g. ``embed_build_source``'s own inferred query)
    can run. Safe for *identity/comparison* (e.g. ``scan_engine``'s own
    pair-aware baseline-context-reuse decision, which only compares these
    values against another side's resolved header/include sets, never reads
    a file under them) -- **not** safe for a caller intending to re-read a
    file under one of these paths after this call returns. Closing that for
    real needs the pair-aware/lifetime redesign PR 3A's "Known gaps" entry
    already scopes as a dedicated follow-up, not merely exposing the values.
    """

    snapshot: AbiSnapshot
    effective_includes: tuple[Path, ...]
    effective_compile_context: CompileContext | None


def is_raw_source_tree(path: Path | None) -> bool:
    """True for a source tree needing real extraction — not a prebuilt pack."""
    from .buildsource.inline import is_pack_dir
    from .cli_buildsource_helpers import _is_inputs_pack_dir

    return path is not None and not (is_pack_dir(path) or _is_inputs_pack_dir(path))


def reject_hybrid_source_frontend(
    depth: str | None,
    sides: Sequence[tuple[InputSpec, SideEvidence]],
    header_backend: str,
) -> None:
    """Reject ``depth='source'`` under the ``hybrid`` AST frontend.

    ``hybrid`` has no real ``embed_build_source`` extractor, so a raw source
    tree needing real extraction under it is a usage error rather than a
    silently weaker result. A prebuilt pack or a bare ``build_info`` never
    feeds L4, so neither is rejected. Mirrors ``cli.py``'s own ``--depth
    source`` + ``--ast-frontend hybrid`` ``UsageError``.
    """
    from . import service_compare_evidence as _sce

    if depth is None or depth.lower() != "source":
        return
    for side, evidence in sides:
        if (
            is_raw_source_tree(side.sources)
            and _sce.effective_frontend(evidence.compile, header_backend) == "hybrid"
        ):
            raise ValidationError(
                "depth='source' is incompatible with the 'hybrid' AST "
                "frontend: L4 source-ABI replay has no dual-backend hybrid "
                "extractor. Use 'castxml' or 'clang' for a depth='source' "
                "request."
            )


def _seeded_includes_and_compile_context(
    side: InputSpec,
    evidence: SideEvidence,
    *,
    lang: str = "c++",
    lang_explicit: bool = False,
    build_config: Path | None = None,
    build_query: str | None = None,
    build_compile_db: str | None = None,
) -> tuple[list[Path], CompileContext | None, bool, list[Callable[[], None]]]:
    """This input's L2 include-dir seed *and* its P0.3 L3->L2 compile-context
    fold, resolved together in one L3 collection (PR C, typed dump/scan
    convergence -- see the root ``AGENTS.md`` "Known gaps" entry on
    ``service_dump_pipeline.py``).

    This used to be two independent calls here -- ``seed_l2_includes`` (the
    include-dir fallback: when headers are given with ``sources``/
    ``build_info`` but no explicit ``includes``, the L2 public-header parse
    cannot see the include dirs the build knows -- the pvxs/EPICS case, a
    public header reaching into a dependency SDK) and
    ``derive_l2_compile_context`` (folding the build's real compile context
    -- standard, defines/undefines, include search paths, sysroot, target
    triple -- onto the L2 header-AST invocation, P0.3) -- each independently
    capable of running :func:`~abicheck.buildsource.inline.collect_inline_pack`.
    That is the exact self-deadlock shape already found and fixed for
    ``dump``/``scan``'s three CLI-side resolvers (see
    :func:`~abicheck.buildsource.l2_seed.seed_includes_and_fold_compile_context`'s
    own docstring, "Known gaps" fifth finding): a caller whose ``sources``/
    ``build_info`` genuinely needs the zero-config *inferred* build-system
    query (no existing compile database) would have the include-dir seed's
    own inferred query hold the deterministic build-dir lock until its
    cleanup runs -- deliberately deferred until after the L2 parse consumes
    the seeded dirs -- so the compile-context fold's own, separate
    inferred-query attempt would contend on the identical lock and wait up to
    the 600s timeout before falling back to a throwaway sibling dir. This
    path never actually hit that timeout, because ``allow_inferred_build_
    query`` was always ``False`` here (see below, unchanged) -- but running
    :func:`~abicheck.buildsource.inline.collect_inline_pack` twice per side
    was still real, avoidable duplicated work even with the query itself
    suppressed, and diverging from the one already-fixed shared primitive
    the other three call sites converged on is exactly the kind of drift PR
    C exists to close. This is the one piece of that convergence safely
    landable on its own, without restructuring ``perform_elf_dump``/
    ``scan_engine._build_new_snapshot`` themselves to route through
    :func:`resolve_side_snapshot` -- their own pipelines have hooks (a
    second header-graph/clang-layout-tool pass, a side-aware ``-H
    old=PATH`` baseline) this function's shared primitive does not yet
    model; see the ``AGENTS.md`` entry for the full accounting of what
    remains open.

    ``allow_inferred_build_query=False`` (``collect_mode="off"``), unlike the
    CLI's ``collect_mode != "off"``: passive discovery of an existing compile
    database still applies, but a Tier-2 API call must never *execute* a
    build system (cmake/make/bazel) as a side effect of resolving an input.
    That is a surprise a library caller cannot see coming, and the CLI only
    permits it because the user typed a command that says so.

    A no-op (``(list(side.includes), evidence.compile, False, [])``) when
    there is no L3 evidence or no headers to match — the exact same behavior
    as the two functions this replaces, so a caller with no build evidence
    for this side sees no change (backward compatible).

    *lang*/*lang_explicit* (``discussion_r3787398644``, Codex review):
    this side's own requested parse language, forwarded unchanged so a
    matched compile unit's derived ``-std=`` whose language family
    conflicts with an explicitly forced language is omitted rather than
    forwarded into a parse that would reject it (e.g. a matched C compile
    unit's ``-std=c17`` forwarded into an explicitly-forced C++ parse).

    *build_config*/*build_query*/*build_compile_db* (PR 3A, dump/scan
    resolver convergence): optional pass-throughs to
    :func:`~abicheck.buildsource.l2_seed.seed_includes_and_fold_compile_context`,
    defaulted to ``None`` so every existing caller (``compare``'s typed
    pipeline, ``dump``'s ``execute_dump_request``) is unaffected. These exist
    only so a caller resolving a side that still carries the CLI's live
    ``--build-query``/``--build-compile-db``/``--config`` flags (``dump``'s
    ELF path, until PR 3C removes them) can route through this one shared
    primitive instead of a second, independent call to the same underlying
    function.

    Returns ``(includes, context, applied, cleanups)`` — ``includes`` is
    *side.includes* augmented with any build-derived seed dirs; ``applied``
    is True only when a real L3 context was found and folded in, which is
    what the caller uses to decide whether to stamp
    ``AbiSnapshot.parsed_with_build_context``; *cleanups* is what the caller
    must run **after** the parse consumes the seeded/derived dirs — an
    inferred build dir may hold the generated headers they point at.

    May raise :class:`~abicheck.errors.HeaderCompileContextAmbiguousError` —
    the same fail-closed-on-ambiguity contract ``derive_l2_compile_context``
    already had (a genuine ABI-relevant disagreement across compile units is
    never silently resolved by picking one).
    """
    if not (side.sources or side.build_info) or not evidence.headers:
        return list(side.includes), evidence.compile, False, []
    from .buildsource.l2_seed import seed_includes_and_fold_compile_context

    ctx = evidence.compile
    cleanups: list[Callable[[], None]] = []
    effective_ctx: CompileContext | None
    includes, applied, effective_ctx, _derived_dirs = (
        seed_includes_and_fold_compile_context(
            headers=evidence.headers,
            includes=side.includes,
            sources=side.sources,
            build_info=side.build_info,
            build_config=build_config,
            build_query=build_query,
            build_compile_db=build_compile_db,
            build_targets=side.build_targets,
            collect_mode="off",
            gcc_path=ctx.gcc_path if ctx is not None else None,
            gcc_prefix=ctx.gcc_prefix if ctx is not None else None,
            gcc_options=ctx.gcc_options if ctx is not None else None,
            gcc_option_tokens=ctx.gcc_option_tokens if ctx is not None else (),
            sysroot=ctx.sysroot if ctx is not None else None,
            nostdinc=ctx.nostdinc if ctx is not None else False,
            frontend=ctx.frontend if ctx is not None else "auto",
            frontend_context=ctx.frontend_context if ctx is not None else "host",
            lang=lang,
            lang_explicit=lang_explicit,
            pending_cleanups=cleanups,
        )
    )
    # seed_includes_and_fold_compile_context() always returns a real
    # CompileContext for `effective_ctx` -- it's built fresh from the
    # individual kwargs above, never literally `ctx` -- so a no-op fold
    # (`applied=False`) with no caller-supplied context would otherwise
    # silently turn a `None` into a default-valued CompileContext() here.
    # That distinction is load-bearing: service_dump_cache._dump_is_cacheable
    # only permits caching when `compile is None` (Codex review, fresh
    # evidence) -- preserve `None` in exactly that case so an otherwise
    # cacheable typed dump/compare operand doesn't lose caching merely
    # because unrelated build evidence was supplied and matched nothing.
    if not applied and ctx is None:
        effective_ctx = None
    return includes, effective_ctx, applied, cleanups


def resolve_side_snapshot(
    side: InputSpec,
    evidence: SideEvidence,
    *,
    lang: str,
    lang_explicit: bool = False,
    header_backend: str,
    fmt: str | None,
    public_headers: list[Path],
    public_header_dirs: list[Path],
    enable_debuginfod: bool = False,
    debuginfod_url: str | None = None,
    dwarf_only: bool = False,
    debug_format: str | None = None,
    include_labels: dict[Path, str] | None = None,
    notify: Callable[[str], None] | None = None,
) -> AbiSnapshot:
    """Resolve one :class:`InputSpec` into an :class:`AbiSnapshot`.

    Runs :func:`abicheck.service.resolve_input` with this side's already-resolved
    :class:`~abicheck.service_compare_evidence.SideEvidence` (headers, compile
    context, dump manifest), then embeds the side's inline L3-L5 build/source
    evidence when it declares any.

    ``lang_explicit`` (G31 Phase C follow-up): whether *lang* reflects a
    genuinely explicit request rather than a request-level default — see
    :attr:`abicheck.api_types.CompareRequest.lang_explicit` /
    :attr:`abicheck.api_types.DumpRequest.lang_explicit`. Forwarded to
    :func:`abicheck.service.resolve_input` unchanged.

    A thin wrapper over :func:`_resolve_side_snapshot_impl` (PR 3A, dump/scan
    resolver convergence) — identical signature, identical behavior, for every
    existing caller. Use the impl function directly when the caller also
    needs the fold's effective ``includes``/``CompileContext`` back.
    """
    return _resolve_side_snapshot_impl(
        side,
        evidence,
        lang=lang,
        lang_explicit=lang_explicit,
        header_backend=header_backend,
        fmt=fmt,
        public_headers=public_headers,
        public_header_dirs=public_header_dirs,
        enable_debuginfod=enable_debuginfod,
        debuginfod_url=debuginfod_url,
        dwarf_only=dwarf_only,
        debug_format=debug_format,
        include_labels=include_labels,
        notify=notify,
    ).snapshot


def _resolve_side_snapshot_impl(
    side: InputSpec,
    evidence: SideEvidence,
    *,
    lang: str,
    lang_explicit: bool = False,
    header_backend: str,
    fmt: str | None,
    public_headers: list[Path],
    public_header_dirs: list[Path],
    enable_debuginfod: bool = False,
    debuginfod_url: str | None = None,
    dwarf_only: bool = False,
    debug_format: str | None = None,
    include_labels: dict[Path, str] | None = None,
    notify: Callable[[str], None] | None = None,
    build_config: Path | None = None,
    build_query: str | None = None,
    build_compile_db: str | None = None,
    changed_paths: tuple[str, ...] = (),
    allow_build_query: bool | None = None,
) -> SideResolution:
    """The real implementation behind :func:`resolve_side_snapshot`.

    PR 3A (dump/scan resolver convergence, CLI cleanup phase two): everything
    :func:`resolve_side_snapshot` already did, plus returning the fold's own
    effective ``includes``/:class:`CompileContext` (see :class:`SideResolution`)
    and two extra optional pass-throughs (*changed_paths*, *allow_build_query*)
    that only ``scan``'s candidate-side resolution needs — every other caller
    leaves them at their no-op defaults, so this is a strict superset of the
    prior behavior, not a new decision point. *build_config*/*build_query*/
    *build_compile_db* are the equivalent pass-through for ``dump``'s ELF
    path, which still has live ``--build-query``/``--build-compile-db``/
    ``--config`` CLI flags until PR 3C removes them.

    ``allow_build_query=None`` keeps this Tier-2 primitive's existing
    "never execute a build system as a side effect" default (``False``,
    matching every pre-existing caller) — only a caller that explicitly
    passes ``True`` (the CLI, once its own trust gate has already decided
    the query is authorized) opts into running one.
    """
    from . import service

    # PR C (typed dump/scan convergence): the include-dir seed and the P0.3
    # L3->L2 compile-context fold are resolved together, in one L3
    # collection, by _seeded_includes_and_compile_context -- see that
    # function's own docstring for why two independent collections here was
    # a latent self-deadlock risk, not just duplicated work.
    # Pre-seeded (rather than left unbound) so the `finally` below is safe
    # even if _seeded_includes_and_compile_context itself raises before
    # returning -- it drains its own cleanups internally on
    # HeaderCompileContextAmbiguousError (see its docstring), so an empty
    # list here is correct, not a leak.
    cleanups: list[Callable[[], None]] = []
    try:
        includes, compile_ctx, context_applied, cleanups = (
            _seeded_includes_and_compile_context(
                side,
                evidence,
                lang=lang,
                lang_explicit=lang_explicit,
                build_config=build_config,
                build_query=build_query,
                build_compile_db=build_compile_db,
            )
        )
        snap = service.resolve_input(
            side.path,
            evidence.headers,
            includes,
            side.version,
            lang,
            lang_explicit=lang_explicit,
            is_elf=True if fmt == "elf" else None,
            pdb_path=side.pdb,
            debug_roots=list(side.debug_roots) or None,
            enable_debuginfod=enable_debuginfod,
            debuginfod_url=debuginfod_url,
            header_backend=header_backend,
            compile=compile_ctx,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            include_dependencies=side.include_dependencies,
            dump_manifest=evidence.dump_manifest,
            follow_linker_scripts=side.follow_linker_scripts,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
            include_labels=include_labels,
            notify=notify,
        )
        # P0.3: a genuine L3 CompileUnit context was resolved and folded into
        # this side's L2 header-AST invocation above -- record that so the
        # existing header_parse_context_drift/header_build_context_mismatch
        # advisory findings correctly stop firing for this snapshot (they key
        # off this exact flag). Gated on snap.from_headers the same way every
        # other parsed_with_build_context stamp site is (cli_dump_helpers.py):
        # a snapshot that never actually parsed the headers (e.g. --dwarf-only
        # ignored them) must not claim their parse used real build context.
        if context_applied and snap.from_headers:
            snap.parsed_with_build_context = True
        if side.sources or side.build_info:
            embed_side_build_source(
                snap,
                side,
                evidence,
                header_backend,
                public_headers,
                public_header_dirs,
                changed_paths=changed_paths,
                allow_build_query=bool(allow_build_query),
            )
    finally:
        # Only after the L2 parse (and any embed) has consumed the seeded dirs:
        # an inferred CMake build dir can hold the generated headers they point
        # into, so draining earlier would delete them mid-parse.
        if cleanups:
            from .buildsource.inline import _run_cleanups

            _run_cleanups(cleanups)
    return SideResolution(
        snapshot=snap,
        effective_includes=tuple(includes),
        effective_compile_context=compile_ctx,
    )


def embed_side_build_source(
    snap: AbiSnapshot,
    side: InputSpec,
    evidence: SideEvidence,
    header_backend: str,
    public_headers: list[Path],
    public_header_dirs: list[Path],
    *,
    changed_paths: tuple[str, ...] = (),
    allow_build_query: bool = False,
) -> None:
    """Embed one side's inline L3-L5 build/source evidence into *snap*.

    Same public roots as ``resolve_input``, plus a ``dump_manifest``'s
    *declared-public* roots only (a manifest's project-owned TU includes are
    private, hence ``dump_manifest_public_roots`` rather than
    ``dump_manifest_header_roots``).

    A malformed pack raises ``click.ClickException`` deep inside
    ``embed_build_source`` — no place in this Tier-2 API's
    ``ValidationError``/``SnapshotError`` contract, so it is translated here
    (Codex review).

    *changed_paths*/*allow_build_query* (PR 3A, dump/scan resolver
    convergence): optional pass-throughs to ``embed_build_source``, defaulted
    to their existing no-op values so every pre-existing caller (``compare``,
    ``dump``'s typed pipeline) is unaffected — only ``scan``'s candidate-side
    resolution passes non-default values, for its POI-focused L4 replay
    scoping and its CLI-resolved trusted-build-query permission.
    """
    import click

    from . import service_compare_evidence as _sce
    from .cli_buildsource import embed_build_source
    from .dumper_clang import resolve_source_frontend_clang_bin
    from .dumper_scoping import dump_manifest_public_roots

    ctx = evidence.compile
    try:
        embed_build_source(
            snap,
            build_info=side.build_info,
            sources=side.sources,
            build_targets=side.build_targets,
            collect_mode=evidence.collect_mode,
            changed_paths=changed_paths,
            allow_build_query=allow_build_query,
            extractor=_sce.effective_frontend(evidence.compile, header_backend),
            # L4 source-ABI replay must invoke the compiler this input's own L2
            # header AST was pointed at (`gcc_path`/`gcc_prefix`), not
            # `embed_build_source`'s bare "clang" default -- the same fix
            # `scan_engine` and the `dump` CLI already carry. Without it a
            # typed request naming a non-default toolchain (e.g. icpx) replayed
            # L4 through a plain "clang" that may not understand the real
            # build's flags, so an omitted `depth` silently returned a weaker
            # snapshot and an explicit `depth="source"` failed (Codex review).
            # `exclude_cl_style=False` because L4 re-drives a CL compile unit
            # with `--driver-mode=cl` itself; only the S2 pre-scan needs the
            # exclusion.
            clang_bin=resolve_source_frontend_clang_bin(
                ctx.gcc_path if ctx else None,
                ctx.gcc_prefix if ctx else None,
                exclude_cl_style=False,
            ),
            public_headers=tuple(str(p) for p in public_headers),
            public_header_dirs=tuple(str(p) for p in public_header_dirs)
            + tuple(str(p) for p in dump_manifest_public_roots(evidence.dump_manifest)),
            quiet=True,
        )
    except click.ClickException as exc:
        raise SnapshotError(str(exc)) from exc


def enforce_requested_depth(
    depth: str | None, sides: Sequence[tuple[str, AbiSnapshot]]
) -> None:
    """Fail when an explicit ``depth`` was requested but not actually reached.

    Mirrors ``dump``'s own ``check_requested_depth_satisfied`` hard-fail, but
    raises ``ValidationError`` (a Tier-2 API has no ``ClickException``
    concept). Without it, a raw input that could not reach the requested rung
    — no usable compile database, extractor, or linkable declarations —
    silently produced whatever weaker evidence ``embed_build_source`` managed.

    *sides* is ``(label, snapshot)`` pairs so the message names the side that
    fell short: ``compare`` passes both of its own, ``dump`` its single input.

    Known, accepted limitation (Codex review, not fixed here): this is a
    floor, not a ceiling. An input that is an already-serialized JSON snapshot
    with richer embedded evidence than ``depth`` requested still carries all of
    it — ``resolve_input``'s ``fmt == "json"`` branch returns
    ``load_snapshot(path)`` verbatim, matching the CLI's own long-documented
    default, which ``--depth`` has never projected down for a pre-built
    snapshot either.
    """
    if depth is None:
        return
    from .cli_dump_helpers import _DEPTH_RANK, _gated_source_label

    # validate() already restricts depth to USER_DEPTHS.
    requested_rank = _DEPTH_RANK.get(depth.lower(), 0)
    for side_label, snap in sides:
        effective = _gated_source_label(snap.build_source, snap)
        if _DEPTH_RANK.get(effective, 0) < requested_rank:
            raise ValidationError(
                f"depth={depth!r} was requested for the {side_label} "
                f"side but the resolved snapshot only reached {effective!r} "
                "evidence depth. Supply the evidence this rung needs (headers, "
                "a build/compile database, or --sources with linkable "
                "declarations) or lower depth to match what is actually "
                "available."
            )
