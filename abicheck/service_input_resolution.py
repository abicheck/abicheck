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

from .artifact_plan import ResolvedArtifactPlan
from .errors import SnapshotError, ValidationError
from .header_conditionals import attach_build_context_for_parsed_headers

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from .api_types import InputSpec
    from .compile_context import CompileContext
    from .model import AbiSnapshot
    from .service_compare_evidence import SideEvidence

__all__ = [
    "BaselineReuseContext",
    "SideResolution",
    "embed_side_build_source",
    "enforce_requested_depth",
    "is_raw_source_tree",
    "reject_hybrid_source_frontend",
    "resolve_baseline_compile_context",
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
    #: PR 3A blocker 6: the context the *other* (baseline) side's parse should
    #: use, when the caller supplied a ``baseline_reuse_hint``. Identical to
    #: ``effective_compile_context`` when no hint was given (no second side to
    #: decide about) or when the two sides' resolved scopes match, and the
    #: caller's own unfolded context when they genuinely diverge -- see
    #: :class:`BaselineReuseContext` for why that is the correct fallback.
    #: Defaulted, so a caller that ignores it is unaffected.
    baseline_compile_context: CompileContext | None = None


@dataclass(frozen=True, slots=True)
class BaselineReuseContext:
    """The *other* side's resolved header/include scope, for a paired resolve.

    PR 3A blocker 6 (CLI cleanup phase two). ``scan --against`` resolves one
    side — the candidate — through the per-input machinery, then has to answer
    a question that is inherently about *two* snapshots: may the candidate's
    own P0.3 L3→L2 folded :class:`CompileContext` also be used to parse the
    baseline, or must the baseline fall back to the caller's plain, unfolded
    one?

    That decision was hand-rolled inline in ``scan_engine.run_scan_core`` as a
    four-clause boolean expression, and it took three separate review rounds to
    get right (the twelfth, thirteenth and fifteenth findings on the root
    ``AGENTS.md``'s L3→L2-fold entry: gating on ``baseline_headers``
    truthiness rather than content, then on headers alone while
    ``-I old=``/``-I new=`` routed the two sides through different include
    trees). It is exactly the kind of rule that must exist once, not once per
    caller.

    Deliberately **not** a widening of :func:`resolve_side_snapshot`'s general
    single-input contract: this is an optional, opt-in hint, and a caller that
    does not pass one is bit-for-bit unaffected — every field of
    :class:`SideResolution` it does not touch keeps its previous meaning. The
    per-input primitives stay per-input; this is the one pair-shaped fact a
    ``scan`` caller can hand *in*, mirroring how
    ``service_compare_pipeline``'s own docstring keeps pair-shaped decisions
    out of the per-input layer rather than pretending they don't exist.
    """

    #: The old side's resolved header list (``cli_scan``'s
    #: ``header_both + header_old``). Empty means "no old-side header scope of
    #: its own", which reuses the candidate's.
    baseline_headers: tuple[Path, ...] = ()
    #: The old side's resolved include list (``include_both + include_old``),
    #: built by ``cli_scan`` completely independently of the header list —
    #: which is why both have to be checked, not just one.
    baseline_includes: tuple[Path, ...] = ()

    def folded_context_is_reusable(
        self,
        *,
        headers: Sequence[Path],
        effective_includes: Sequence[Path],
    ) -> bool:
        """May the candidate's folded context also parse the baseline?

        Only when the baseline's own resolved scope is either absent or
        *identical in content* to the candidate's, on **both** axes.

        Content, not truthiness, on the header axis: a bare, shared ``-H
        api.h`` (no ``old=`` scoping — the ordinary, most common
        ``scan --against`` usage) already makes ``baseline_headers`` truthy
        and equal to the candidate's, since ``cli_scan`` builds it as
        ``header_both + header_old``. Gating on mere truthiness treats every
        scan with any headers at all as old-side-scoped and drops the fold for
        the common case, which is the whole ``NOT_COMPARABLE`` bug this fold
        exists to prevent.

        And both axes, not just headers: ``-H api.h -I old=old-build -I
        new=new-build`` shares one header list while routing each side through
        a genuinely different include tree. Forwarding the new side's folded
        ``-D``/``-std``/sysroot flags there would parse the old binary under
        the new build's configuration.

        There is no ``--build-info-old``/``--sources-old``, so no old-side
        fold can be derived for the diverging case — the caller's plain,
        unfolded context is the correct fallback, not a second guess.
        """
        if self.baseline_headers and list(self.baseline_headers) != list(headers):
            return False
        return not (
            self.baseline_includes
            and list(self.baseline_includes) != list(effective_includes)
        )


def resolve_baseline_compile_context(
    hint: BaselineReuseContext | None,
    *,
    folded: CompileContext | None,
    unfolded: CompileContext | None,
    headers: Sequence[Path],
    effective_includes: Sequence[Path],
) -> CompileContext | None:
    """The :class:`CompileContext` the *baseline* side's parse should use.

    The one implementation of :meth:`BaselineReuseContext.
    folded_context_is_reusable`'s consequence, shared by
    ``scan_engine.run_scan_core`` (which calls it directly today) and by
    :func:`_resolve_side_snapshot_impl`'s ``baseline_reuse_hint`` parameter
    (which reports the same answer on :class:`SideResolution` for whichever
    slice finally routes ``scan``'s candidate resolution through the shared
    primitive). Two callers, one rule — which is the point.

    *hint* of ``None`` means the caller has no second side, so there is
    nothing to decide: the folded context is simply this side's own.
    """
    if hint is None:
        return folded
    if hint.folded_context_is_reusable(
        headers=headers, effective_includes=effective_includes
    ):
        return folded
    return unfolded


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


def _gated_build_query_inputs(
    build_config: Path | None,
    build_query: str | None,
    *,
    allow_build_query: bool,
    build_config_locally_trusted: bool = False,
) -> tuple[Path | None, str | None]:
    """The real trust gate on *build_config*/*build_query* -- shared by both
    the L2 seed and the L3-L5 embed step so a caller's permission decision is
    computed once and applied identically everywhere (Codex review, fresh
    evidence, two rounds).

    ``build_query`` is a trusted **executable command** (``build.query`` in
    ``.abicheck.yml``, or ``--build-query`` on the CLI); ``build_config`` is a
    path to a ``.abicheck.yml`` that may itself carry a ``build.query`` key,
    so it carries the identical execution risk *by proxy of that one key* --
    it is forced to ``None`` unless *allow_build_query* is exactly ``True``,
    regardless of what the caller passed, UNLESS *build_config_locally_
    trusted* says otherwise (see below). ``build_query`` itself is always
    gated the same way regardless of that flag -- it is a bare, always-
    executable string with no downstream consumer that separately checks its
    provenance. **``build_compile_db`` is deliberately not gated by this
    function** (see its own call sites) -- it is a bare path/glob naming an
    *existing* ``compile_commands.json``, a pure data read with "no such
    restriction" (matching this repo's own established ``dump
    --build-compile-db`` vs. ``--build-query`` distinction, and
    ``embed_build_source``'s own pre-existing behavior). Gating it the same
    way as the executable inputs would silently degrade a caller's real
    include paths/defines/dialect for supplying data that was never a
    permission question in the first place.

    Relying on ``seed_includes_and_fold_compile_context``'s/
    ``collect_inline_pack``'s own identically-named ``allow_build_query``
    parameter would be wrong here -- it is a documented, deprecated no-op
    (``buildsource/inline.py``'s ``collect_inline_pack`` docstring). This
    function is the one place that decision is actually enforced.

    *build_config_locally_trusted* (PR 3A, scan resolver convergence; Codex
    review -- a real regression, not a hypothetical): ``build_config``'s own
    *query* field is independently, correctly enforced downstream, at the
    actual point of execution -- ``collect_inline_pack``'s
    ``build_config_trusted_for_query`` parameter, computed presence-based
    (``build_config is not None or build_query is not None``) by both of
    this function's callers (``l2_seed._resolve_l2_seed_pack_args``,
    ``cli_buildsource.embed_build_source``) before this gate here was ever
    introduced. That downstream check is what actually decides whether
    ``build.query`` may run; blanket-nulling ``build_config`` *here* as well
    is a second, blunter gate keyed on a different signal
    (*allow_build_query*) that also silently drops every *passive*,
    non-executable setting the config carries (``build.compile_db``,
    ``build.internal_namespaces``, ...) whenever that signal is not exactly
    ``True`` -- which, for ``scan``, is the common case: ``cli_scan_helpers.
    resolve_effective_allow_query`` (ADR-037 D4 "level-implies-query") only
    ever answers ``True`` when the config *itself* declares a ``build.query``
    key AND an explicitly-pinned deep evidence level, so an ordinary
    ``scan --config <path>`` whose config only sets ``build.compile_db``
    lost that config entirely once this function started gating
    ``build_config``'s bare presence for ``scan`` too. Passing this flag
    restores ``scan``'s pre-migration behavior (``build_config`` always
    forwarded ungated to both the seed and the embed step, trusting exactly
    the downstream, presence-based gate) without weakening the default this
    function already gives ``dump``/``compare``'s typed-API callers, which
    have no equivalent CLI-side consent gate of their own and stay fully
    gated (default ``False``, unchanged).
    """
    gated_query = build_query if allow_build_query is True else None
    if allow_build_query is True or build_config_locally_trusted:
        gated_config = build_config
    else:
        gated_config = None
    return gated_config, gated_query


def _seeded_includes_and_compile_context(
    side: InputSpec,
    evidence: SideEvidence,
    *,
    lang: str = "c++",
    lang_explicit: bool = False,
    build_config: Path | None = None,
    build_query: str | None = None,
    build_compile_db: str | None = None,
    allow_build_query: bool = False,
    build_config_locally_trusted: bool = False,
    collect_mode: str | None = None,
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

    ``allow_inferred_build_query=False`` (``collect_mode="off"``) by default,
    unlike the CLI's ``collect_mode != "off"``: passive discovery of an
    existing compile database still applies, but a Tier-2 API call must never
    *execute* a build system (cmake/make/bazel) as a side effect of resolving
    an input. That is a surprise a library caller cannot see coming, and the
    CLI only permits it because the user typed a command that says so — which
    is exactly what the optional *collect_mode* parameter is for: ``scan``'s
    candidate resolution, migrated onto this primitive by PR 3A, passes its
    own real collect mode so it keeps the inferred-query seeding it has always
    had. ``None`` (every other caller) keeps the pin.

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

    **Enforced here, not merely forwarded (Codex review, fresh evidence, two
    rounds)**: *allow_build_query* gates whether *build_config*/*build_query*
    — the two potentially-*executable* inputs — are forwarded at all, via the
    shared :func:`_gated_build_query_inputs` (also used by
    :func:`_resolve_side_snapshot_impl`'s embed step, so a caller's
    permission decision is computed once and applied identically to both the
    seed and the L3-L5 embed). *build_compile_db* is deliberately **not**
    gated — see that helper's own docstring for why a bare data path/glob
    naming an existing compile database is not the same risk as a trusted
    command. This function's own real trust decision does not rest on
    ``collect_inline_pack``'s identically-named parameter, which is a
    documented, deprecated no-op (``buildsource/inline.py``'s own
    ``collect_inline_pack`` docstring: "``allow_build_query`` is accepted
    only for backward compatibility and is ignored"). Threading
    *build_config*/*build_query* through without this local gate would let
    any caller of this Tier-2 primitive execute an operator-supplied
    ``build.query`` command merely by supplying a path, with no separate
    consent step — exactly the "surprise a library caller cannot see coming"
    this function's own docstring already warns against for the *inferred*
    query below; explicit ``build.query`` needs the identical discipline.
    The CLI's own gating (``dump_cmd`` only resolves a non-``None``
    ``build_config``/``build_query`` when ``--config``/``--build-query`` was
    genuinely typed) is a different, CLI-side act of consent that this
    parameter existing lets that one call site assert explicitly, rather than
    this function inferring consent from mere presence.

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

    # See _gated_build_query_inputs's own docstring: build_compile_db is a
    # data path, not gated -- only the two potentially-executable inputs are.
    build_config, build_query = _gated_build_query_inputs(
        build_config,
        build_query,
        allow_build_query=allow_build_query,
        build_config_locally_trusted=build_config_locally_trusted,
    )

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
            collect_mode=collect_mode if collect_mode is not None else "off",
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
            source_filter=side.compile_db_filter,
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
    symbols_only: bool = False,
    debug_presence_only: bool = False,
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

    ``symbols_only``/``debug_presence_only`` (PR 3A, dump/scan resolver
    convergence): forwarded to :func:`abicheck.service.resolve_input`
    unchanged. Both default ``False``, matching that function's own
    defaults, so every pre-existing caller (``compare``, ``dump``'s typed
    pipeline) is unaffected — only ``scan``'s candidate-side resolution,
    which supports a binary-depth/debug-presence-only scan, needs to pass a
    non-default value. Before this, only ``scan_engine._build_new_snapshot``
    (which calls :func:`abicheck.service.resolve_input` directly, bypassing
    this shared primitive entirely) could express either flag — see
    ``AGENTS.md``'s PR C entry for the gap this closes.

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
        symbols_only=symbols_only,
        debug_presence_only=debug_presence_only,
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
    symbols_only: bool = False,
    debug_presence_only: bool = False,
    include_labels: dict[Path, str] | None = None,
    notify: Callable[[str], None] | None = None,
    build_config: Path | None = None,
    build_query: str | None = None,
    build_compile_db: str | None = None,
    changed_paths: tuple[str, ...] = (),
    allow_build_query: bool | None = None,
    build_config_locally_trusted: bool = False,
    baseline_reuse_hint: BaselineReuseContext | None = None,
    seed_collect_mode: str | None = None,
    seed_lang_explicit: bool | None = None,
    defer_cleanup: list[Callable[[], None]] | None = None,
    source_extractor: str | None = None,
    expand_public_header_roots: bool = False,
    source_frontend_from_folded_context: bool = False,
    l4_public_headers: list[Path] | None = None,
    l4_public_header_dirs: list[Path] | None = None,
) -> SideResolution:
    """The real implementation behind :func:`resolve_side_snapshot`.

    PR 3A (dump/scan resolver convergence, CLI cleanup phase two): everything
    :func:`resolve_side_snapshot` already did, plus returning the fold's own
    effective ``includes``/:class:`CompileContext` (see :class:`SideResolution`)
    and three extra optional pass-throughs (*changed_paths*,
    *allow_build_query*, and the *symbols_only*/*debug_presence_only* pair)
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

    Eight further parameters exist so ``scan``'s candidate resolution could be
    migrated onto this one primitive *without changing any of its own
    behaviour* (PR 3A). Each defaults to what this function did before, so
    ``compare``/``dump``'s typed pipelines are bit-for-bit unaffected; six are
    forwarded straight to :func:`embed_side_build_source`, whose docstring
    explains each (including *l4_public_headers*/*l4_public_header_dirs* --
    the L4-only public-root override that closes the scan-vs-dump L4
    root-set asymmetry that same docstring documents). The two that are this
    function's own:

    * *seed_collect_mode* — the collect mode handed to the L2 include/compile
      seed. ``None`` keeps the pinned ``"off"``: a Tier-2 API call must never
      *execute* a build system as a side effect of resolving an input (see
      :func:`_seeded_includes_and_compile_context`). ``scan`` passes its real
      collect mode, because the user typed a command that says so, and
      dropping that would silently remove its zero-config inferred-build-query
      include seeding for a source tree with no compile database.
    * *seed_lang_explicit* — the seed's own ``lang_explicit``, when it differs
      from the one the *parse* gets. ``None`` (the default) uses
      *lang_explicit* for both, which is what every request-shaped caller
      wants. ``scan`` has no ``lang_explicit`` on its CLI at all, but ``--lang
      c`` is never its Click default and so is always a genuine request — it
      therefore guards the seed with ``lang == "c"`` while leaving the parse's
      own auto-detection alone, exactly as it did before this migration.

    *build_config_locally_trusted* -- ``False`` keeps ``build_config``'s
    presence fully gated by *allow_build_query* (unchanged for ``dump``/
    ``compare``'s typed pipelines). ``scan`` passes ``True``: its own CLI-side
    consent gate (``cli_scan_helpers.resolve_effective_allow_query``, ADR-037
    D4) only ever authorizes a config's *executable* ``build.query`` field,
    never its bare presence, and blanket-nulling ``build_config`` here for
    every other case would drop an ordinary ``--config`` file's *passive*
    settings too -- see :func:`_gated_build_query_inputs`'s own docstring for
    the full reasoning and the pre-migration behavior this restores.
    """
    from . import service
    from .api_types import required_path

    # `InputSpec.path` is `Path | None` since PR 3A blocker 5 (so a source-only
    # dump is expressible), but this function resolves a *native artifact* --
    # every request type that reaches here has already rejected a `None` path
    # in `validate()`, and a binary-less dump is `cli_buildsource.
    # dump_source_only`'s pipeline, not this one. Narrowed once, up front.
    side_path = required_path(side, "input")

    # PR C (typed dump/scan convergence): the include-dir seed and the P0.3
    # L3->L2 compile-context fold are resolved together, in one L3
    # collection, by _seeded_includes_and_compile_context -- see that
    # function's own docstring for why two independent collections here was
    # a latent self-deadlock risk, not just duplicated work.
    # The `_artifact_plan` constructed below (rather than left unbound) makes
    # the `finally` below safe even if _seeded_includes_and_compile_context
    # itself raises before returning -- it drains its own cleanups internally
    # on HeaderCompileContextAmbiguousError (see its docstring), so an empty
    # `pending_cleanups` here is correct, not a leak.
    # Computed once, shared by the seed below and the embed step further
    # down, so both apply the identical permission decision (Codex review,
    # fresh evidence) rather than each independently gating build_config/
    # build_query -- see _gated_build_query_inputs's own docstring.
    # build_compile_db is deliberately not part of this gate (a data path,
    # not an executable command) and is forwarded to both call sites as-is.
    _gated_build_config, _gated_build_query = _gated_build_query_inputs(
        build_config,
        build_query,
        allow_build_query=bool(allow_build_query),
        build_config_locally_trusted=build_config_locally_trusted,
    )
    # Phase 1 (dedup-and-convergence plan) Milestone A follow-up: the third
    # and (per the plan doc's own Phase 1 item list) last known hand-rolled
    # `pending_cleanups: list[...] = []` + manual drain-in-finally pattern,
    # after `perform_elf_dump` and `handle_non_elf_dump`. Same primitive,
    # same behavior: `_seeded_includes_and_compile_context` still *returns*
    # its own cleanups list (its return contract is unchanged, and shared
    # with other reasoning in its docstring) -- only what the caller does
    # with that list changes, from a bare local to
    # `_artifact_plan.pending_cleanups`, drained once via `run_cleanups()`
    # at the identical point the old code called `_run_cleanups()`.
    _artifact_plan = ResolvedArtifactPlan()
    try:
        includes, compile_ctx, context_applied, _seed_cleanups = (
            _seeded_includes_and_compile_context(
                side,
                evidence,
                lang=lang,
                lang_explicit=(
                    lang_explicit if seed_lang_explicit is None else seed_lang_explicit
                ),
                build_config=_gated_build_config,
                build_query=_gated_build_query,
                build_compile_db=build_compile_db,
                allow_build_query=bool(allow_build_query),
                build_config_locally_trusted=build_config_locally_trusted,
                collect_mode=seed_collect_mode,
            )
        )
        _artifact_plan.pending_cleanups.extend(_seed_cleanups)
        # Drained as soon as the L2 parse below has consumed the seeded dirs --
        # *before* the embed step further down, not after it (PR 3A, dump/scan
        # resolver convergence; the ordering `scan_engine._build_new_snapshot`
        # already proved). An inferred build query holds its deterministic
        # per-source-tree build dir under an exclusive `flock` until its own
        # cleanup runs, and `embed_side_build_source` below runs its *own*
        # inferred query in the same call -- so draining only at the end of
        # this function makes that second query contend, in the same process,
        # on a lock this one still holds, blocking for up to
        # `INFERRED_QUERY_TIMEOUT_S` (600s) before falling back to a throwaway
        # dir. That is the identical self-contention shape recorded as the
        # fifth finding on the root `AGENTS.md`'s L3->L2-fold entry.
        #
        # Latent rather than live for every caller that exists today, which is
        # why the old ordering never showed up: `_seeded_includes_and_compile_
        # context` pins the seed's own `collect_mode="off"`, so no caller can
        # currently run an inferred query in the seed at all and
        # `_artifact_plan.pending_cleanups` is always empty here. It stops
        # being latent the moment that pin is relaxed for the CLI resolvers
        # PR 3A still has to migrate -- fixing it now costs nothing and
        # removes a trap from that migration's path.
        #
        # Draining here is safe for everything downstream: an inferred build
        # dir can hold the *generated headers* the seeded include dirs point
        # at, which is why the drain must not happen before the parse -- but
        # neither the ADR-039 collector (it scans the caller's own header list)
        # nor `embed_side_build_source` (it collects its own evidence from
        # scratch) reads through those seeded dirs. `run_cleanups()` is
        # idempotent (drains only what's new since the last drain), so this
        # early call and the outer `finally`'s backstop call below never
        # double-run the same cleanup.
        try:
            snap = service.resolve_input(
                side_path,
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
                symbols_only=symbols_only,
                debug_presence_only=debug_presence_only,
                include_labels=include_labels,
                notify=notify,
                # Provenance widening gets ONLY this side's own explicit -I
                # list (`side.includes`, pre-seeding), never the fold's
                # already-widened `includes` local -- same regression class
                # the ELF/PE/Mach-O CLI resolvers already avoid (Codex
                # review, round 13): an auto-derived seed directory can hold
                # a genuinely private sibling header.
                public_include_search_dirs=list(side.includes),
            )
        finally:
            _artifact_plan.run_cleanups()
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
        # PR C / PR 3A (typed dump/scan convergence): the ADR-039
        # build-context collector, previously reachable only from the ELF
        # `dump` CLI's own `perform_elf_dump` (one call site) -- now available
        # to every caller of this shared primitive (compare's implicit-dump
        # operand, dump's typed `run_dump_request` API), and, since this
        # helper was extracted, to `scan_engine._build_new_snapshot` too. Every
        # gate the three call sites used to hand-write now lives in one place;
        # see `attach_build_context_for_parsed_headers`' own docstring for what
        # each of them is and why.
        #
        # `side.compile` is this side's *pre-fold*, caller-supplied
        # `CompileContext` (the ADR-055 D1 per-side override resolved before
        # `_seeded_includes_and_compile_context`'s L3->L2 fold ran above) --
        # never `compile_ctx`, the folded/derived result, which would union a
        # per-header-matched compile unit's own -D snapshot-wide (see
        # `user_define_flags`' own docstring, and the ninth finding in the root
        # AGENTS.md's L3->L2-fold entry).
        #
        # `side.compile_db_filter` (PR 3A investigation, 2026-08-21): the same
        # filter narrowed `_seeded_includes_and_compile_context`'s fold above,
        # forwarded here too so the ADR-039 collector scans the identical
        # translation-unit subset the header parse used -- exactly the
        # "the fold, the legacy match and the ADR-039 collector cannot select
        # different translation units for the same filter" invariant
        # `build_context.source_matches_filter` establishes for the three
        # CLI-side layers (root AGENTS.md's PR C entry). `resolve_dump_
        # request` is what refuses the combination this can't safely narrow
        # (a filter with a resolved collect mode that also embeds *this*
        # side's L3 evidence via `embed_side_build_source` below, which has no
        # filter concept of its own) before this function is ever reached.
        attach_build_context_for_parsed_headers(
            snap,
            evidence.headers,
            build_info=side.build_info,
            live_elf_parse=(
                snap.elf is not None and service.sniff_text_format(side_path) != "json"
            ),
            user_gcc_option_tokens=(
                side.compile.gcc_option_tokens if side.compile else ()
            ),
            user_gcc_options=side.compile.gcc_options if side.compile else None,
            source_filter=side.compile_db_filter,
        )
        if side.sources or side.build_info:
            # Known, accepted limitation (Codex review, fresh evidence, not
            # fixed here): when a trusted build_query was authorized and
            # headers were present, the seed above (_seeded_includes_and_
            # compile_context) has already run it once via
            # seed_includes_and_fold_compile_context. Forwarding the same
            # build_query here means embed_build_source's own, independent
            # collect_inline_pack call can run it a *second* time -- an
            # already-authorized command re-executed, not a new privilege,
            # but real, avoidable duplicated work for an expensive/stateful
            # query, and a theoretical risk of the two collections observing
            # a build that changed between the two runs. This mirrors an
            # already-accepted characteristic of this same pipeline for the
            # build_config-driven *inferred* query case (see this module's
            # `_seeded_includes_and_compile_context` docstring and
            # `_l2_local_cleanups`' own comment below: "embed_build_source()
            # below runs its own inferred query in the same function").
            # Closing this for real needs the seed and this embed call to
            # share one collect_inline_pack result across their two
            # different layer scopes (L3-only vs. L3+L4+L5) -- a genuine,
            # separate refactor, not a same-session patch; see
            # docs/contribute/plans/cli-cleanup-phase-two.md's PR 3A section.
            embed_side_build_source(
                snap,
                side,
                evidence,
                header_backend,
                public_headers,
                public_header_dirs,
                changed_paths=changed_paths,
                allow_build_query=bool(allow_build_query),
                build_config=_gated_build_config,
                build_query=_gated_build_query,
                build_compile_db=build_compile_db,
                defer_cleanup=defer_cleanup,
                source_extractor=source_extractor,
                source_frontend_compile=(
                    compile_ctx if source_frontend_from_folded_context else None
                ),
                expand_public_header_roots=expand_public_header_roots,
                l4_public_headers=l4_public_headers,
                l4_public_header_dirs=l4_public_header_dirs,
            )
    finally:
        # Backstop only. The real drain happens in the nested `finally` around
        # `service.resolve_input` above, as soon as the L2 parse has consumed
        # the seeded dirs and before the embed step can contend on the same
        # inferred-build-dir lock -- see that comment for why. `run_cleanups()`
        # is idempotent (drains only what's new since the last drain), so this
        # call is a no-op on every path the nested drain already ran, and
        # covers only the narrow window where the seed itself raised after
        # handing back cleanups but before the nested `try` was reached.
        _artifact_plan.run_cleanups()
    return SideResolution(
        snapshot=snap,
        effective_includes=tuple(includes),
        effective_compile_context=compile_ctx,
        # PR 3A blocker 6: the pair-shaped answer, computed only when the
        # caller handed in the second side's scope. Without a hint this is
        # just `compile_ctx` -- there is no other side to decide about -- so
        # every pre-existing caller reads exactly what it read before.
        baseline_compile_context=resolve_baseline_compile_context(
            baseline_reuse_hint,
            folded=compile_ctx,
            unfolded=evidence.compile,
            headers=evidence.headers,
            effective_includes=includes,
        ),
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
    build_config: Path | None = None,
    build_query: str | None = None,
    build_compile_db: str | None = None,
    defer_cleanup: list[Callable[[], None]] | None = None,
    source_extractor: str | None = None,
    source_frontend_compile: CompileContext | None = None,
    expand_public_header_roots: bool = False,
    l4_public_headers: list[Path] | None = None,
    l4_public_header_dirs: list[Path] | None = None,
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

    *build_config*/*build_query*/*build_compile_db* (Codex review, fresh
    evidence): the identical build inputs the caller already resolved for
    the L2 seed (:func:`_resolve_side_snapshot_impl` forwards its own
    already-gated values here — see :func:`_gated_build_query_inputs`, the
    single place *build_config*/*build_query* are actually authorized).
    Without this, the L2 header-AST parse and the L3-L5 embed could resolve
    *different* build configurations for the same input — the L2 seed using
    the caller's explicit config while this embed step fell back to
    auto-discovery — so the snapshot's own evidence layers could silently
    describe two different builds, and an explicitly requested depth could
    fail to be satisfied despite the caller having supplied exactly what it
    needed.

    The last four parameters exist so ``scan``'s candidate resolution can route
    through this one primitive without any of its own long-standing behaviour
    changing underneath it (CLI cleanup phase two, PR 3A). Each defaults to
    exactly what this function did before, so every pre-existing caller
    (``compare``, ``dump``'s typed pipeline) is bit-for-bit unaffected:

    * *defer_cleanup* — ``scan`` owns the command-lifetime cleanup list its
      collection's temp build dirs are drained from; ``compare``/``dump`` let
      ``embed_build_source`` drain its own.
    * *source_extractor* — the L4 replay frontend. ``None`` keeps this
      function's own :func:`service_compare_evidence.effective_frontend`
      resolution. ``scan`` passes ``"auto"``, which
      ``buildsource.inline._make_source_extractor`` reads as clang, because
      that is what ``scan`` has always done — making it match the other
      resolvers would newly *require* castxml for a ``scan --depth source``
      that works with clang today, a real behaviour change for real users
      that cannot be verified without a castxml-capable lane (recorded as an
      open divergence in the plan's PR 3A section, deliberately not closed by
      the migration that added this parameter).
    * *source_frontend_compile* — the context whose ``gcc_path``/
      ``gcc_prefix`` selects the L4 replay compiler, when it is not
      ``evidence.compile``. ``scan`` passes the *folded* (post-P0.3) context,
      i.e. the one its L2 header AST was actually pointed at, which is what
      this function's own ``clang_bin`` comment below says the intent is; the
      two differ only when the caller set neither selector and the matched
      compile unit named an MSVC/clang-cl driver.
    * *expand_public_header_roots* — expand a public-header *directory* into
      its individual files before handing the list to ``embed_build_source``.
      ``scan`` does; the raw pass-through this function otherwise uses loses
      ``clang_public_roots._equivalent_public_roots_for_unit``'s
      single-sample mirror promotion for a directory root (a change switching
      ``scan`` to the raw shape was landed and reverted for exactly that
      regression — see the plan's 2026-08-20 note).
    * *l4_public_headers*/*l4_public_header_dirs* — an override root set for
      *this call's* ``embed_build_source`` invocation only, when the caller's
      L2-facing *public_headers*/*public_header_dirs* need to stay narrower
      than what L4 replay should classify against. ``scan`` needs exactly
      this split: its L2/crosscheck-origin provenance
      (``cli_scan_baseline._public_provenance_set``) deliberately does not
      activate for a lone ``-H`` *file* with no accompanying directory (a
      single header cannot establish a public directory boundary — see that
      function's own docstring, and its pinned
      ``test_lone_file_does_not_activate``), so changing that default to fix
      L4 would also silently flip the origin/crosscheck-skip behavior every
      other scan already relies on. But ``dump``'s write-time embed and
      ``compare``'s implicit-dump operand both derive their public-header
      roots via the more permissive ``split_public_header_inputs`` (every
      ``-H`` file/dir is a root, no directory required) — so a `dump`
      baseline for a lone-``-H``-file project correctly links its L4
      declarations to binary symbols while a `scan --against` candidate for
      the identical project silently degrades to zero matches, producing a
      spurious ``source_decl_binary_symbol_mismatch``/
      ``source_to_binary_mapping_changed`` RISK finding on an unchanged
      library purely from this L2-vs-L4 root-set asymmetry (reproduced
      end-to-end; PR 3A review). Defaults to ``None`` (use
      *public_headers*/*public_header_dirs* unchanged, exactly as before this
      parameter existed) for every pre-existing caller.
    """
    import click

    from . import service_compare_evidence as _sce
    from .cli_buildsource import embed_build_source
    from .dumper_clang import resolve_source_frontend_clang_bin
    from .dumper_scoping import dump_manifest_public_roots
    from .service_scan import expand_public_header_inputs

    ctx = evidence.compile
    frontend_ctx = (
        source_frontend_compile if source_frontend_compile is not None else ctx
    )
    manifest_roots = dump_manifest_public_roots(evidence.dump_manifest)
    embed_headers = (
        l4_public_headers if l4_public_headers is not None else public_headers
    )
    embed_header_dirs = (
        l4_public_header_dirs
        if l4_public_header_dirs is not None
        else public_header_dirs
    )
    if expand_public_header_roots:
        embedded_public_headers: tuple[str, ...] = tuple(
            expand_public_header_inputs(
                [*embed_headers, *embed_header_dirs, *manifest_roots]
            )
        )
    else:
        embedded_public_headers = tuple(str(p) for p in embed_headers)
    try:
        embed_build_source(
            snap,
            build_info=side.build_info,
            sources=side.sources,
            build_config=build_config,
            build_query=build_query,
            build_compile_db=build_compile_db,
            build_targets=side.build_targets,
            collect_mode=evidence.collect_mode,
            changed_paths=changed_paths,
            allow_build_query=allow_build_query,
            extractor=(
                source_extractor
                if source_extractor is not None
                else _sce.effective_frontend(evidence.compile, header_backend)
            ),
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
                frontend_ctx.gcc_path if frontend_ctx else None,
                frontend_ctx.gcc_prefix if frontend_ctx else None,
                exclude_cl_style=False,
            ),
            public_headers=embedded_public_headers,
            public_header_dirs=tuple(str(p) for p in embed_header_dirs)
            + tuple(str(p) for p in manifest_roots),
            defer_cleanup=defer_cleanup,
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
