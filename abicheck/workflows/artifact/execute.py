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

"""One artifact, executed — the ``ResolvedPlan -> ArtifactResult`` half.

ADR-061 Phase 3, the companion to :mod:`abicheck.workflows.artifact.resolve`.
This module runs a plan that module already decided: it produces the snapshot,
embeds the L3-L5 build-source evidence onto it, and reports what the run
*achieved* -- which is the distinction the phase's acceptance criteria turn on.

Three of those criteria are structural properties of this module rather than
claims about it:

* **Extraction occurs once per artifact.** :func:`resolve_side_snapshot` is the
  single entry point every front end reaches (``dump`` through
  ``service_dump_pipeline``, both ``compare`` sides through
  ``service_compare_pipeline``, ``scan``'s candidate through
  ``scan_engine._build_new_snapshot``), and it calls ``service.resolve_input``
  exactly once. A front end that wanted a second opinion would have to call
  this again, not fork the logic.
* **Resource lifetimes cover execution.** The L2 seed's cleanup thunks are
  drained through :class:`~abicheck.workflows.artifact.contracts.ResolvedArtifactPlan`
  *after* the header parse consumes the seeded directories and *before* the
  L3-L5 embed step runs its own collection -- an ordering fixed after an
  inferred build query's own ``flock`` was found able to self-contend with the
  embed's.
* **Achieved depth is a result fact.** :class:`SideResolution` carries the
  effective includes and the effective :class:`CompileContext` the parse
  actually used, and :func:`enforce_requested_depth` answers "was the depth the
  caller asked for actually reached" from the produced snapshot rather than
  from the flags that requested it. A frontend does not guess.

Same mechanical note as :mod:`~abicheck.workflows.artifact.resolve`: ``service``
is reached through the module object at call time so ``monkeypatch.setattr``
keeps working and this module stays out of ``service``'s import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...errors import SnapshotError, ValidationError
from ...header_conditionals import attach_build_context_for_parsed_headers
from ...header_utils import include_operand_dirs
from .contracts import ResolvedArtifactPlan
from .resolve import (
    BaselineReuseContext,
    _gated_build_query_inputs,
    _seeded_includes_and_compile_context,
    resolve_baseline_compile_context,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ...api_types import InputSpec
    from ...compile_context import CompileContext
    from ...model import AbiSnapshot
    from ...service_compare_evidence import SideEvidence

__all__ = [
    "SideResolution",
    "embed_side_build_source",
    "enforce_requested_depth",
    "resolve_side_snapshot",
    "side_effective_compile_context",
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
    legacy_compile_db_tokens: tuple[str, ...] = (),
    legacy_compile_db_matched: bool = False,
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
    path, which keeps a live ``--config`` flag plus these programmatic
    ``build_query``/``build_compile_db`` arguments -- PR 3C removed the CLI
    flags of those two names, not the parameters themselves, since a
    programmatic caller is the operator exactly as an explicit ``--config``
    is.

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

    *legacy_compile_db_tokens*/*legacy_compile_db_matched* (ADR-063 Phase 1):
    forwarded verbatim to
    :func:`~abicheck.workflows.artifact.resolve._seeded_includes_and_compile_context`
    -- see that function's own docstring for the precedence rule (the P0.3
    fold's own result wins whenever it applies), why *matched* is a signal
    independent of whether any tokens were derived (Codex review), and
    ``docs/contribute/known-gaps.md``'s "ADR-063 Phase 1" entry for the
    mechanism this closes. Both default to falsy, so every existing caller
    of this function is unaffected.

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
    import abicheck.service as service

    from ...api_types import required_path

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
                legacy_compile_db_tokens=legacy_compile_db_tokens,
                legacy_compile_db_matched=legacy_compile_db_matched,
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
                # a genuinely private sibling header. A `--compiler-option`/
                # `InputSpec.compile.gcc_option_tokens` include-search
                # operand is exactly as explicit as `side.includes` (Codex
                # review, fresh evidence) -- sourced from `side.compile`,
                # this side's pre-fold caller-supplied CompileContext (never
                # `compile_ctx`, the L3-folded result, same distinction the
                # ADR-039 collector call below already draws). Suppressed
                # entirely for a manifest dump: `side.compile`'s tokens are
                # global, applied to every TU regardless of the manifest,
                # and `dump()`'s own manifest mutual-exclusivity check
                # rejects any non-empty `public_include_search_dirs` -- see
                # the identical fix/reasoning in `cli_dump_helpers.
                # perform_elf_dump`.
                public_include_search_dirs=(
                    None
                    if evidence.dump_manifest is not None
                    else list(side.includes)
                    + list(
                        include_operand_dirs(
                            side.compile.gcc_option_tokens if side.compile else ()
                        )
                    )
                ),
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

    A malformed pack raises ``SnapshotError`` from
    ``buildsource.embed.embed_build_source``, which needs no translation; a
    malformed config raises ``ValidationError`` and is flattened onto
    ``SnapshotError`` here
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
    import abicheck.service_compare_evidence as _sce

    from ...buildsource.embed import embed_build_source
    from ...dumper_clang import resolve_source_frontend_clang_bin
    from ...dumper_scoping import dump_manifest_public_roots
    from ...service_scan import expand_public_header_inputs

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
        )
    except ValidationError as exc:
        # The engine keeps usage and operational errors distinct (the CLI needs
        # 64 vs 1); this surface has always flattened both onto SnapshotError,
        # so preserve that. SnapshotError itself propagates unchanged.
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

    This function is the *floor* half only — it never strips evidence a
    resolved snapshot carries beyond *depth*. The *ceiling* half is
    :func:`abicheck.policy.depth_projection.project_snapshot_to_depth`,
    applied by ``classify_compare_pair`` right after this function confirms
    the floor — see that function's own docstring and
    ``docs/contribute/known-gaps.md``'s "``--depth`` is a floor for live
    extraction, not a ceiling for a pre-built snapshot" entry for the full
    account, including why ``dump`` deliberately does not apply it.
    """
    if depth is None:
        return
    from ...evidence_depth import depth_rank, gated_source_label

    # validate() already restricts depth to USER_DEPTHS.
    requested_rank = depth_rank(depth.lower())
    for side_label, snap in sides:
        effective = gated_source_label(snap.build_source, snap)
        if depth_rank(effective) < requested_rank:
            raise ValidationError(
                f"depth={depth!r} was requested for the {side_label} "
                f"side but the resolved snapshot only reached {effective!r} "
                "evidence depth. Supply the evidence this rung needs (headers, "
                "a build/compile database, or --sources with linkable "
                "declarations) or lower depth to match what is actually "
                "available."
            )


def side_effective_compile_context(
    resolution: SideResolution,
    snapshot: AbiSnapshot,
    fmt: str | None,
    *,
    dump_manifest: object | None,
) -> CompileContext | None:
    """The `CompileContext` to record for one side's `ResolvedExecutionContext.
    compile_contexts`, or `None` when recording it would be wrong or absent.

    Shared by ``service_dump_pipeline.execute_dump_request`` and
    ``service_compare_pipeline.resolve_compare_request`` (Codex review,
    PR #1037; lifted out of the dump path's own inline conditional once the
    compare path needed the identical gate). Only when a header-AST parse
    actually ran *this invocation* (``snapshot.from_headers``), the binary
    format was successfully detected (``fmt is not None``), and the side
    isn't a manifest-driven dump -- whose own real header-AST parse runs
    under its own manifest-authoritative ``frontend_context`` (e.g.
    ``"device"``), not the request-derived context this fold resolved, so
    recording it here would risk stating a wrong (``"host"``) toolchain.
    """
    if (
        resolution.effective_compile_context is not None
        and snapshot.from_headers
        and fmt is not None
        and dump_manifest is None
    ):
        return resolution.effective_compile_context
    return None
