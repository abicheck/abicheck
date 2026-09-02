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

"""One artifact's resolved plan — the ``ArtifactRequest -> ResolvedPlan`` half.

ADR-061 Phase 3. This is the *resolution* side of the per-artifact pipeline
this phase names: everything that decides **what** an extraction will do,
without doing it. Nothing here parses a header, runs a build query, or
produces a snapshot; :mod:`abicheck.workflows.artifact.execute` does that,
from what this module decided.

The split is the point, not a filing convenience. It is what lets
``dump --dry-run`` render the same resolved plan a real run consumes rather
than re-deriving one from the raw flags: a preview computed by a second
resolver looks authoritative while being connected to nothing, which is worse
than two implementations kept in sync by hand.

Everything here was ``abicheck/service_input_resolution.py``'s, which in turn
held it for ``service_compare_pipeline``. It kept moving inward because the
rule it encodes is genuinely per-*input*: a change to how one artifact resolves
must land on ``dump``, on both ``compare`` sides, and on ``scan``'s candidate
at once. Pair-shaped decisions stayed behind in the pair workflow -- the
pair-wide C++20 dialect override exists because two sides must agree on a
standard, and the sequential-resolution rule is about two extractions running
at once. Neither means anything for a lone artifact.

:class:`BaselineReuseContext` is the one exception, and it is deliberate: it
carries the *other* side's already-resolved scope as an opt-in hint, so a
paired caller can ask the per-input resolver a pair-shaped question without
the resolver acquiring standing knowledge of two sides.

Mechanical note, inherited unchanged: everything this module needs from
``service`` is looked up **through the module object at call time**
(``from ... import service`` inside the function), never bound at import time,
so ``monkeypatch.setattr(service, "resolve_input", ...)`` keeps working. The
function-local import also keeps this module out of ``service``'s import
cycle (AGENTS.md "What NOT to do").
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ...api_types import InputSpec
    from ...compile_context import CompileContext
    from ...service_compare_evidence import SideEvidence

__all__ = [
    "BaselineReuseContext",
    "is_raw_source_tree",
    "reject_hybrid_source_frontend",
    "resolve_baseline_compile_context",
]


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
    from ...buildsource.inputs_pack import is_any_pack_dir

    return path is not None and not is_any_pack_dir(path)


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
    import abicheck.service_compare_evidence as _sce

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
    ``.abicheck.yml``, or a programmatic ``build_query``); ``build_config`` is a
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
    build_compile_db`` vs. ``build_query`` distinction, and
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


def _fold_legacy_compile_db_tokens(
    ctx: CompileContext | None, tokens: tuple[str, ...]
) -> CompileContext | None:
    """Merge already-derived legacy ``-p``/``--compile-db`` castxml flags into
    *ctx* (ADR-063 Phase 1 -- see ``docs/contribute/known-gaps.md``'s
    "ADR-063 Phase 1" entry).

    A no-op (*ctx* returned unchanged, ``None`` included) when *tokens* is
    empty -- the overwhelmingly common case, since a caller passes a
    non-empty tuple only when the CLI's own legacy ``-p``/``--compile-db``
    auto-match genuinely derived something.

    *tokens* are already-split argv entries (``build_context.to_castxml_
    flags()``'s own return -- e.g. ``("-I", "/opt/SDK Files/include")``, one
    element per argv position, never pre-joined), so they ride verbatim in
    :attr:`CompileContext.gcc_option_tokens` rather than being ``" ".join``-ed
    into the :attr:`~CompileContext.gcc_options` free-form string and later
    re-split by :func:`~abicheck._compiler_options.split_gcc_options`
    (Codex review, fresh evidence on ``8f2c22d``): a token containing
    embedded whitespace -- a Windows SDK path with a space, or a compile-db
    ``-DNAME=a b`` define -- would otherwise silently split back into the
    wrong number of tokens, corrupting the derived include path or macro
    value the moment it reached the real parse.

    Precedence is preserved exactly: this function's caller already
    guarantees *ctx*'s own ``gcc_options``/``gcc_option_tokens`` are never a
    legacy-derived value (see ``_seeded_includes_and_compile_context``'s own
    docstring, "Precedence" paragraph) -- an explicit, caller-supplied value
    must still win over the legacy match for a conflicting flag. Since
    :func:`~abicheck._compiler_options.split_gcc_options`'s combined-token
    order always places ``gcc_options`` ahead of ``gcc_option_tokens``
    (later wins), *ctx*'s own ``gcc_options`` string is split *here* -- with
    the identical splitter every consumer already applies to it downstream,
    so this changes no token list, only where the split happens -- and
    interleaved as ``(*tokens, *split(ctx.gcc_options), *ctx.gcc_option_
    tokens)``: the legacy tokens first (lowest precedence), then whatever
    *ctx* already carried, in the same relative order it always had.
    Mirrors ``cli_helpers_compare._merge_gcc_options``'s own ordering intent
    (that function cannot be imported here: it lives in a ``cli_*`` module,
    and this file is under ``workflows/artifact/`` -- an engine-layer tree
    ``scripts/check_ai_readiness.py``'s ``engine-cli-boundary`` check
    forbids importing a CLI sibling from) -- the combined *effective* token
    sequence a real ``-p compile_commands.json`` run would produce is
    identical to what ``_merge_gcc_options``'s own string-join path
    produces for any token *without* embedded whitespace (every existing
    precedence test's fixture shape); it is *this* function that is now
    correct where ``_merge_gcc_options`` still is not, for a token that
    does carry embedded whitespace.
    """
    if not tokens:
        return ctx
    from ..._compiler_options import split_gcc_options

    base_tokens: tuple[str, ...] = ()
    existing_tokens: tuple[str, ...] = ()
    if ctx is not None:
        if ctx.gcc_options:
            base_tokens = tuple(split_gcc_options(ctx.gcc_options))
        existing_tokens = ctx.gcc_option_tokens
    combined = tuple(tokens) + base_tokens + existing_tokens
    if ctx is None:
        from ...compile_context import CompileContext

        return CompileContext(gcc_option_tokens=combined)
    return dataclasses.replace(ctx, gcc_options=None, gcc_option_tokens=combined)


def _legacy_compile_db_achieved(matched: bool, tokens: tuple[str, ...]) -> bool:
    """Whether the legacy ``-p``/``--compile-db`` auto-match should count as
    having achieved real build context (Codex review, fresh evidence on
    ``f381deb``).

    *matched* alone under-counts: a caller may pass non-empty *tokens*
    (proof the match already derived real castxml flags) while leaving
    *matched* at its default ``False``, as the tokens-only call shape in
    ``tests/test_legacy_compile_db_typed_threading.py`` does. Non-empty
    tokens are themselves sufficient evidence of a match, independent of
    whether *matched* was also passed -- and *matched* remains necessary on
    its own for a genuinely matched compile unit that legitimately derives
    zero flags, which an empty token tuple cannot represent at all.
    """
    return matched or bool(tokens)


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
    legacy_compile_db_tokens: tuple[str, ...] = (),
    legacy_compile_db_matched: bool = False,
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
    ``build_query``/``build_compile_db`` arguments and ``--config`` flag (``dump``'s
    ELF path, until PR 3C removes them) can route through this one shared
    primitive instead of a second, independent call to the same underlying
    function.

    *legacy_compile_db_tokens* (ADR-063 Phase 1, threading the ``-p``/
    ``--compile-db`` legacy auto-match into the typed pipeline -- see
    ``docs/contribute/known-gaps.md``'s "ADR-063 Phase 1" entry for the
    precise mechanism this closes): the castxml flags
    ``cli_helpers_compare._resolve_build_context_flags`` already derived
    from that *separate*, older ``build_context_for_header``/
    ``build_context_union_fallback`` match -- passed in already-computed,
    the same way ``perform_elf_dump``'s own ``legacy_build_context_flags``
    parameter carries them, rather than re-derived here (this function has
    no ``--compile-db``/``compile_db_filter`` matching logic of its own, and
    must not grow a second one). Defaulted to ``()`` so every existing
    caller is unaffected.

    **Precedence, mirroring ``perform_elf_dump``'s own "legacy-match
    overlap" fix exactly**: never fed to the P0.3 fold above as if it were
    explicit user context -- ``ctx.gcc_options`` (this side's own
    caller-supplied :class:`CompileContext`, never a legacy-derived value)
    is the fold's only explicit input, same as it already was. The tokens
    are folded in *after* the fold decides, only when the fold's own
    ``applied`` came back ``False`` -- the fold's result wins and supersedes
    the legacy match whenever the fold *does* match a header, identical to
    ``perform_elf_dump``'s own reassignment. This is additive threading
    only: no caller of this function is wired to actually pass a non-empty
    tuple yet (that is ``dump_cmd``'s real-execution branch, which still
    executes through ``perform_elf_dump``/``handle_non_elf_dump``, not this
    typed pipeline -- see the known-gaps entry for what remains open).

    *legacy_compile_db_matched* (Codex review, fresh evidence): whether the
    legacy match actually matched a compile unit at all -- the second
    element of ``cli_helpers_compare._resolve_build_context_flags``'s own
    return, mirroring ``perform_elf_dump``'s ``compile_db_context_matched``
    parameter exactly. A separate signal from *legacy_compile_db_tokens*
    on purpose: a genuinely matched compile unit that legitimately derives
    zero castxml flags is real build-context evidence (the returned
    ``applied`` must become ``True`` so ``parsed_with_build_context`` gets
    stamped, same as ``perform_elf_dump``'s own gate), but an empty token
    tuple alone cannot distinguish that case from "the legacy match never
    ran" -- collapsing the two would either wrongly claim context for an
    unmatched header or (this parameter's absence, before this fix) wrongly
    deny it for a matched one whose own flags folded in silently without
    ever flipping ``applied``.

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
    ``build_config``/``build_query`` when ``--config`` (or a programmatic
    ``build_query``) was
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
        return (
            list(side.includes),
            _fold_legacy_compile_db_tokens(evidence.compile, legacy_compile_db_tokens),
            _legacy_compile_db_achieved(
                legacy_compile_db_matched, legacy_compile_db_tokens
            ),
            [],
        )
    from ...buildsource.l2_seed import seed_includes_and_fold_compile_context

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
    # ADR-063 Phase 1: the legacy `-p`/`--compile-db` auto-match's own
    # already-derived flags are folded in ONLY when the P0.3 fold above did
    # NOT itself apply -- the fold's result wins and supersedes the legacy
    # match whenever the fold does match a header, exactly the same
    # "legacy-match overlap" precedence `perform_elf_dump`'s own
    # `l3_context_applied` reassignment already enforces for the CLI's
    # real-execution path. `applied=True` means real L3 evidence was folded
    # in above using `ctx.gcc_options` (never a legacy-derived value) as the
    # fold's own explicit input, so the legacy tokens are simply discarded
    # here rather than double-counted on top of it.
    if not applied:
        effective_ctx = _fold_legacy_compile_db_tokens(
            effective_ctx, legacy_compile_db_tokens
        )
        # Codex review, fresh evidence (twice over): folding the legacy
        # tokens into effective_ctx above is not enough on its own --
        # `applied` is what `_resolve_side_snapshot_impl` actually gates
        # `parsed_with_build_context` on (mirroring `perform_elf_dump`'s own
        # `compile_db_context_matched` OR `l3_context_applied` condition).
        # Two independent ways a call can prove a real match: an explicit
        # `legacy_compile_db_matched=True` (a real match with zero derived
        # tokens, which an empty token tuple alone can't represent), or a
        # non-empty `legacy_compile_db_tokens` (which is itself proof a
        # match already derived real flags, even when a caller left
        # `legacy_compile_db_matched` at its default). See
        # `_legacy_compile_db_achieved`.
        applied = _legacy_compile_db_achieved(
            legacy_compile_db_matched, legacy_compile_db_tokens
        )
    return includes, effective_ctx, applied, cleanups
