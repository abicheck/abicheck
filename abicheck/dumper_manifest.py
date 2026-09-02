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

"""ADR-050 D3/D4 (G32 Phase B/C) — the per-TU header-AST invocation loop and
the real compatible merge across translation units.

Lives in a sibling module, not inline in ``dumper.py``, because ``dumper.py``
has essentially no headroom left before the AI-readiness file-size gate's
2000-line hard cap (see ``AGENTS.md``'s "Files that are large" and this
phase's own G32 plan entry) — the same split precedent ``dumper_castxml.py``/
``dumper_clang.py`` already set for per-backend logic.

**Dependency direction**: this module never imports from ``dumper.py`` at
module level, even though its whole purpose is to be called *from*
``dumper.py``'s manifest-driven ``dump()`` path (wired in a later G32 task,
not this one). Importing ``dumper._header_ast_parser`` directly here would
create ``dumper -> dumper_manifest -> dumper``, an import cycle. Instead,
the caller (i.e. ``dumper.py``, once wired) injects
``dumper._header_ast_parser`` as the ``header_ast_parser`` callable
parameter below -- the exact same "pass the function in rather than import
it" technique ``dumper_hybrid.run_hybrid_dump(dump_fn, ...)`` already uses
for the identical reason (see that module's own docstring).

**Merge scope**: ``TuFragment``/``MergedTuFragments``/``entity_key`` live in
the leaf module :mod:`abicheck.tu_fragment` (not here) so that both this
module and :mod:`abicheck.tu_merge` -- which implements the real
compatible-merge lattice (ADR-050 D4: union provenance, keep the richer
declaration when two TUs' declarations are ODR-compatible; reject with
:class:`abicheck.errors.TuMergeError` otherwise) -- can depend on those
shapes without ``dumper_manifest -> tu_merge -> dumper_manifest`` forming an
import cycle. ``merge_tu_fragments`` below is :func:`abicheck.tu_merge.merge_fragments`
re-exported under its original Phase B name, kept as a stable public alias
of the same callable now that its placeholder ("any repeat is an error")
implementation has been replaced by the real one.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import deadline, process_resources
from .dump_manifest import DumpManifest, IncludeEntry, TranslationUnit
from .dumper_clang import _ClangAstParser
from .dumper_clang_streaming import (
    streaming_prune_suppressed,
    suppress_streaming_prune,
)
from .dumper_toolchain import (
    _parser_ast_fallback_reason,
    _parser_ast_supported,
    _parser_ast_toolchain,
    _parser_ast_unsupported_reasons,
    _parser_frontend_context_kind,
)
from .extract.semantic_normalizer import normalize_header_ast
from .model import EnumType, Function, RecordType, Variable
from .model.identity import EntityId
from .model.semantic_ir import SemanticIR
from .tu_fragment import (
    MergedTuFragments as MergedTuFragments,
    TuFragment as TuFragment,
    entity_key as entity_key,
)
from .tu_merge import merge_fragments as merge_tu_fragments

if TYPE_CHECKING:
    from .dumper_castxml import _CastxmlParser

log = logging.getLogger(__name__)

#: Rough peak resident memory budget per concurrent per-TU worker (GiB) --
#: same default as buildsource.source_replay's L4 pool
#: (``_L4_JOB_MEM_BUDGET_GIB``), since both pools run the identical kind of
#: work (one castxml/clang invocation, one heavy AST parse, per TU). Tunable
#: via ``ABICHECK_TU_JOB_MEM_GIB``; the cap is skipped when RAM can't be read.
_TU_JOB_MEM_BUDGET_GIB = 3.0


def _tu_jobs(n_units: int) -> int:
    """Worker count for the per-TU manifest-dump pool (ADR-050 D6, G32 Phase
    E) -- ``run_tu_loop`` runs one castxml/clang invocation per translation
    unit, instead of today's single aggregate-then-parse call, and this
    function decides how many of those invocations run concurrently.

    Ports ``buildsource.source_replay._l4_jobs``'s already-proven policy
    verbatim (G32 Phase E's own "no new scheduling policy" note): auto ==
    ``min(n_units, cpu_count, 8)``, clamped by available memory; an explicit
    ``ABICHECK_TU_JOBS`` override (set ``1`` to force serial, e.g. for
    deterministic tests) is clamped the same way. Uses this pool's own
    ``ABICHECK_TU_JOBS``/``ABICHECK_TU_JOB_MEM_GIB`` env vars and log
    messages rather than the L4 pool's ``ABICHECK_L4_*`` ones -- a
    manifest dump and an L4 source replay are independent processes that
    may run concurrently (e.g. ``compare --dump-manifest`` alongside a
    separate ``--sources`` scan) and should be tunable independently.
    Shares :mod:`abicheck.process_resources`'s RAM-probing/ceiling
    primitives with the L4 pool rather than reimplementing them.
    """
    budget = process_resources.job_mem_budget_gib(
        "ABICHECK_TU_JOB_MEM_GIB", _TU_JOB_MEM_BUDGET_GIB
    )
    cap = process_resources.mem_cap(budget)
    env = os.environ.get("ABICHECK_TU_JOBS")
    if env:
        try:
            requested = max(1, int(env))
        except ValueError:
            log.warning(
                "ABICHECK_TU_JOBS=%r is not a valid integer; falling back to 1 "
                "(serial) worker.",
                env,
            )
            return 1
        ceiling = process_resources.jobs_ceiling()
        if requested > ceiling:
            log.warning(
                "ABICHECK_TU_JOBS=%d exceeds the oversubscription ceiling (%d "
                "for %d CPUs); clamping to %d",
                requested,
                ceiling,
                os.cpu_count() or 1,
                ceiling,
            )
            requested = ceiling
        if cap is not None and requested > cap:
            log.warning(
                "ABICHECK_TU_JOBS=%d may not fit in available memory (~%.1f GiB "
                "at ~%.1f GiB/worker); clamping to %d to avoid an OOM-killed "
                "manifest dump. Tune ABICHECK_TU_JOB_MEM_GIB, or split the "
                "manifest into fewer translation units.",
                requested,
                process_resources.available_mem_gib() or 0.0,
                budget,
                cap,
            )
            return cap
        return requested
    auto = max(1, min(n_units, os.cpu_count() or 1, 8))
    if cap is not None and cap < auto:
        log.info(
            "Per-TU manifest-dump workers reduced %d -> %d to fit available "
            "memory (~%.1f GiB at ~%.1f GiB/worker); set ABICHECK_TU_JOBS / "
            "ABICHECK_TU_JOB_MEM_GIB to override.",
            auto,
            cap,
            process_resources.available_mem_gib() or 0.0,
            budget,
        )
        return cap
    return auto


#: Signature of ``dumper._header_ast_parser`` -- injected by the caller
#: rather than imported, see this module's own docstring.
HeaderAstParserFn = Callable[..., "_CastxmlParser | _ClangAstParser"]

# Re-exported for backward compatibility: TuFragment/MergedTuFragments/
# entity_key used to be defined here (G32 Phase B) and moved to the leaf
# module abicheck.tu_fragment (G32 Phase C) so both this module and
# abicheck.tu_merge could depend on them without an import cycle -- see this
# module's own docstring. merge_tu_fragments is now a direct alias of
# abicheck.tu_merge.merge_fragments, the real compatible-merge
# implementation (ADR-050 D4), under its original Phase B name.


def run_tu_fragment(
    tu: TranslationUnit,
    *,
    header_ast_parser: HeaderAstParserFn,
    backend: str,
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    sysroot: Path | None,
    nostdinc: bool,
    lang: str | None,
    exported_dynamic: set[str],
    exported_static: set[str],
    public_header_paths: list[str],
    public_dir_paths: list[str],
    extra_hash_dirs: tuple[Path, ...] = (),
    frontend_context: str = "host",
    pruning_header_roots: tuple[str, ...] | None = None,
) -> TuFragment:
    """Run one castxml/clang invocation for *tu* via *header_ast_parser*
    (``dumper._header_ast_parser``, injected -- see this module's own
    docstring) and normalize its output into a :class:`TuFragment`.

    *tu*'s own ``forced_includes`` become the parser's ``headers`` and its
    own ``includes`` (just the resolved paths) become ``extra_includes``.
    *public_header_paths*/*public_dir_paths* are the manifest's own
    base-profile provenance inputs (``roots`` +
    ``public_header_paths``/``public_header_dirs``), passed identically to
    every TU's call -- not derived per-TU from *tu*'s own
    ``forced_includes`` -- since a TU may force-include a private support
    header alongside a public one (:mod:`abicheck.dump_manifest`'s own
    docstring), so only the manifest's declared ``roots`` are the actual
    public surface, regardless of which TU happens to force-include them.

    ``pruning_header_roots`` (Codex review, PR #840): the streaming pruner's
    root set must match ``dumper_scoping.dump_manifest_header_roots``'s own
    computation exactly, or it can misclassify (and permanently drop) a
    declaration the authoritative post-hoc filter would have retained --
    that function folds in *every* TU's ``forced_includes`` and
    ``project_owned`` includes, not just this one TU's. When the caller
    already knows the full manifest (``run_tu_loop``, the only real caller
    with "other TUs" to union with), it computes that manifest-wide union
    once and passes it in here directly, and it is used unchanged. Only
    when no such value is given (``resolve_header_ast_result``'s legacy
    single-TU call, which has no manifest to see beyond this one TU) does
    this function fall back to reproducing just its own per-TU contribution
    (this TU's own ``forced_includes`` + its own ``project_owned``-marked
    ``includes``, folded with *public_header_paths*/*public_dir_paths*) --
    correct for that caller since a lone TU has no sibling to omit.
    """
    if pruning_header_roots is None:
        pruning_header_roots = tuple(
            str(p)
            for p in (
                *tu.forced_includes,
                *(entry.path for entry in tu.includes if entry.project_owned),
                *public_header_paths,
                *public_dir_paths,
            )
        )
    parser = header_ast_parser(
        list(tu.forced_includes),
        [entry.path for entry in tu.includes],
        backend=backend,
        compiler=compiler,
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot,
        nostdinc=nostdinc,
        lang=lang,
        exported_dynamic=exported_dynamic,
        exported_static=exported_static,
        public_header_paths=public_header_paths,
        public_dir_paths=public_dir_paths,
        extra_hash_dirs=extra_hash_dirs,
        frontend_context=frontend_context,
        pruning_header_roots=pruning_header_roots,
    )
    return TuFragment(
        tu_name=tu.name,
        functions=tuple(parser.parse_functions()),
        variables=tuple(parser.parse_variables()),
        types=tuple(parser.parse_types()),
        enums=tuple(parser.parse_enums()),
        typedefs=parser.parse_typedefs(),
        typedefs_qualified=parser.parse_typedefs_qualified(),
        constants=parser.parse_constants(),
        typedef_entity_ids=parser.parse_typedef_entity_ids(),
        constant_entity_ids=parser.parse_constant_entity_ids(),
        ast_producer="clang" if isinstance(parser, _ClangAstParser) else "castxml",
        ast_toolchain=_parser_ast_toolchain(parser),
        ast_fallback_reason=_parser_ast_fallback_reason(parser),
        ast_toolchain_supported=_parser_ast_supported(parser),
        ast_toolchain_unsupported_reasons=tuple(
            _parser_ast_unsupported_reasons(parser)
        ),
        frontend_context_kind=_parser_frontend_context_kind(parser),
    )


def _run_tu_fragments(
    tus: Sequence[TranslationUnit],
    *,
    header_ast_parser: HeaderAstParserFn,
    backend: str,
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    sysroot: Path | None,
    nostdinc: bool,
    lang: str | None,
    exported_dynamic: set[str],
    exported_static: set[str],
    public_header_paths: list[str],
    public_dir_paths: list[str],
    extra_hash_dirs: tuple[Path, ...],
    frontend_context: str = "host",
    pruning_header_roots: tuple[str, ...] | None = None,
) -> list[TuFragment]:
    """Run :func:`run_tu_fragment` for every TU in *tus*, under a
    RAM-aware thread pool (ADR-050 D6, G32 Phase E) instead of a fully
    sequential loop -- ``run_tu_loop``'s own extraction step, split out so
    the pooled-vs-serial dispatch has one obvious home.

    Fragments are always **assembled** in *tus*' own declared order (never
    completion order) for the final return value -- :func:`merge_tu_fragments`
    treats TU order as significant (which TU's declaration "wins" an
    ODR-compatible merge), so a pool that returned results out of order would
    make the merge outcome depend on scheduling luck instead of the
    manifest's own declared TU order. Failures are, by contrast, *observed*
    in completion order (not submission order) precisely so a required TU's
    failure gets reacted to -- and its siblings' cancellation attempted --
    as soon as it happens, not delayed behind an earlier-submitted, merely
    slower TU's still-pending future (Codex review, PR #636). Each future's
    successful result is recorded by its declared index as it completes;
    the assembly pass at the end reads that index-ordered list, so
    observation order and assembly order are decoupled rather than the same
    loop conflating the two.

    A ``required=True`` TU's failure (the default) still propagates and
    fails the whole manifest dump -- any not-yet-started future is cancelled
    (best effort; already-running ones are left to finish rather than
    force-killed mid-subprocess) and the original exception re-raised
    unchanged, so a killed/timed-out TU's real error (e.g. the descriptive
    ``SnapshotError`` a castxml/clang timeout already raises, naming the
    terminated process) surfaces exactly as it would from the sequential
    loop -- never silently swallowed or replaced with an empty fragment. A
    ``required=False`` TU's failure still degrades to a logged diagnostic,
    identical to the pre-pool behavior.

    A single TU, or a resolved worker count of 1 (``ABICHECK_TU_JOBS=1``,
    used by tests to force determinism), takes the plain sequential path --
    no pool overhead, and no ordering ambiguity to reason about at all.
    """
    jobs = _tu_jobs(len(tus))

    def _run_one(tu: TranslationUnit) -> TuFragment:
        return run_tu_fragment(
            tu,
            header_ast_parser=header_ast_parser,
            backend=backend,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang,
            exported_dynamic=exported_dynamic,
            exported_static=exported_static,
            public_header_paths=public_header_paths,
            public_dir_paths=public_dir_paths,
            extra_hash_dirs=extra_hash_dirs,
            frontend_context=frontend_context,
            pruning_header_roots=pruning_header_roots,
        )

    def _handle_failure(tu: TranslationUnit, exc: BaseException) -> None:
        if tu.required:
            raise exc
        log.warning(
            "Optional translation unit %r failed to extract; skipping "
            "(required=false). Its declarations are absent from this "
            "snapshot.",
            tu.name,
            exc_info=exc,
        )

    fragments: list[TuFragment] = []
    if jobs <= 1 or len(tus) <= 1:
        for tu in tus:
            try:
                fragments.append(_run_one(tu))
            except Exception as exc:
                _handle_failure(tu, exc)
        return fragments

    # Results are recorded by INDEX (declared order), but observed in
    # COMPLETION order (via as_completed), not submission order -- a
    # required TU submitted late that fails immediately must not sit behind
    # an earlier-submitted, merely-slow TU's future before its failure is
    # even noticed. Waiting on futures strictly in submission order would
    # let already-queued heavyweight AST jobs keep starting during that
    # delay, making the cancellation below far less effective on a manifest
    # already known to be doomed (Codex review, PR #636). The final
    # `results`-to-`fragments` pass below is what restores declared order
    # for the merge step, which still needs it.
    # contextvars don't cross a ThreadPoolExecutor boundary, so a worker
    # submitted here would otherwise see no active deadline and each TU's
    # clang/castxml invocation would silently fall back to its fixed default
    # timeout regardless of an active deadline.deadline_scope (e.g. `scan
    # --budget`) around the caller -- the same class of gap PR #591 already
    # closed for source_replay.py's L4 pool via _deadline_bound_worker
    # (Codex review, PR #636). Captured once on the submitting thread and
    # re-established inside each worker.
    deadline_ts = deadline.current_deadline_ts()

    # Same contextvar-boundary problem, for a second ambient signal (Codex
    # review, PR #840): `suppress_streaming_prune()` -- set around this
    # whole call whenever a full/unscoped dump was requested -- would
    # otherwise be invisible to a pooled worker thread, silently letting the
    # opt-in streaming pruner drop dependency-header declarations a
    # full-scope request explicitly asked to keep, exactly the bug this
    # PR's own suppression mechanism exists to prevent. Captured once here
    # and re-established inside each worker the same way.
    prune_suppressed = streaming_prune_suppressed()

    def _run_one_with_deadline(tu: TranslationUnit) -> TuFragment:
        with deadline.with_deadline_ts(deadline_ts):
            if prune_suppressed:
                with suppress_streaming_prune():
                    return _run_one(tu)
            return _run_one(tu)

    # Deliberately NOT a `with ThreadPoolExecutor(...) as pool:` block: that
    # context manager's __exit__ calls shutdown(wait=True), which blocks
    # until every already-running future finishes -- silently re-imposing
    # the wait a required failure is supposed to skip past. A still-running
    # sibling can't be force-killed from here regardless, so waiting on it
    # only delays the diagnostic and keeps burning its CPU/RAM for nothing
    # (Codex review, PR #636 follow-up).
    pool = ThreadPoolExecutor(max_workers=jobs)
    results: list[TuFragment | None] = [None] * len(tus)
    future_to_index = {
        pool.submit(_run_one_with_deadline, tu): i for i, tu in enumerate(tus)
    }
    for fut in as_completed(future_to_index):
        idx = future_to_index[fut]
        tu = tus[idx]
        try:
            results[idx] = fut.result()
        except Exception as exc:
            if tu.required:
                # Cancel whatever hasn't started yet (best effort -- a
                # future already running its subprocess can't be
                # force-killed from here) and shut down without waiting on
                # whatever's still running -- a `with`-managed executor's
                # implicit shutdown(wait=True) on exit would silently
                # re-impose exactly that wait (Codex review, PR #636
                # follow-up). An optional TU's failure, by contrast, must
                # not cancel its siblings -- the loop is meant to keep
                # collecting the rest.
                for other_fut in future_to_index:
                    other_fut.cancel()
                pool.shutdown(wait=False)
            _handle_failure(tu, exc)
    pool.shutdown(wait=True)
    fragments.extend(r for r in results if r is not None)
    return fragments


def run_tu_loop(
    tus: Sequence[TranslationUnit],
    *,
    header_ast_parser: HeaderAstParserFn,
    roots: Sequence[Path],
    public_header_paths: Sequence[Path] = (),
    public_header_dirs: Sequence[Path] = (),
    backend: str,
    compiler: str,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    exported_dynamic: set[str],
    exported_static: set[str],
    extra_hash_dirs: tuple[Path, ...] = (),
    frontend_context: str = "host",
) -> MergedTuFragments:
    """Run every TU in *tus* (one castxml/clang invocation each) and merge
    the results via :func:`merge_tu_fragments` -- ADR-050 D3's "one
    castxml/clang invocation per TU... instead of today's single
    aggregate-then-parse call".

    A TU with ``required=False`` whose invocation raises degrades to a
    logged diagnostic instead of failing the whole loop; a ``required=True``
    TU's failure (the default) propagates and fails the whole manifest dump,
    matching D3's "A required TU's compile failure is a hard extraction
    failure for the whole snapshot... an optional TU's failure degrades to a
    diagnostic". Parse-time already guarantees ``contributes_to_abi=True``
    implies ``required=True`` (:mod:`abicheck.dump_manifest`), so an optional
    TU's silently-skipped failure can never drop an entity the merged
    snapshot claims to speak for.

    **Two deliberately different "public" sets are threaded through this
    function** (Codex review, PR #635 round 14) -- conflating them was a
    real bug: *resolved_public_paths*/*resolved_public_dirs* (``roots`` +
    *public_header_paths*/*public_header_dirs*) feed each TU's own
    :func:`run_tu_fragment` call, where they scope constant extraction the
    same roots-are-always-public way the legacy CLI's ``-H``/``--header``
    already does (see :class:`abicheck.dumper_castxml._CastxmlParser`'s own
    docstring) -- but :func:`merge_tu_fragments` gets only the manifest's
    *explicit* ``public_header_paths``/``public_header_dirs`` (``roots``
    excluded), because that is what the *later*, authoritative
    ``apply_provenance`` call in ``dumper.py`` classifies origin against
    too (:mod:`abicheck.dump_manifest`'s own docstring: ``roots`` is a
    *scope* declaration, D1's ``scope_fingerprint`` input, not an ADR-015
    provenance input -- "a manifest with public headers but no separate
    provenance fields behaves exactly like a legacy dump -H foo.h
    invocation naming a lone file", i.e. every declaration stays
    ``UNKNOWN``). Passing the roots-augmented set into the merge step
    instead would let :func:`abicheck.tu_merge._more_public_of` treat a
    root-only declaration as public during the merge, keep *that* TU's
    ``source_location`` as the merged entity's representative, and then
    have the later ``apply_provenance`` call -- which never considered
    ``roots`` public to begin with -- classify the very same declaration
    as private/unknown, hiding a real public API change even though the
    identical declaration also reached this manifest through a genuinely
    public header in another TU.
    """
    resolved_public_paths = [str(r) for r in roots] + [
        str(p) for p in public_header_paths
    ]
    resolved_public_dirs = [str(d) for d in public_header_dirs]
    explicit_public_paths = [str(p) for p in public_header_paths]
    explicit_public_dirs = [str(d) for d in public_header_dirs]

    # Codex review, PR #840: the streaming pruner's root set must match
    # `dumper_scoping.dump_manifest_header_roots`'s own computation exactly
    # -- the *manifest-wide* union across every TU's own `forced_includes`/
    # `project_owned` includes, not just the one TU currently being parsed.
    # A per-TU-only slice (this function's own siblings compute one, for
    # the legacy single-TU call that has no "other TUs" to union with)
    # misses a root a *different* TU declares ownership of -- e.g. a
    # non-contributing support TU that owns a shared include directory --
    # letting the current TU's own declaration under that same directory
    # be misclassified as a dependency and permanently pruned, even though
    # the authoritative post-hoc filter would retain it via the real union.
    # Computed once here (this is the one place that sees every TU) and
    # passed down unchanged to every TU's own `run_tu_fragment` call.
    manifest_pruning_roots = tuple(
        resolved_public_paths
        + resolved_public_dirs
        + [str(p) for tu in tus for p in tu.forced_includes]
        + [
            str(entry.path)
            for tu in tus
            for entry in tu.includes
            if entry.project_owned
        ]
    )

    fragments = _run_tu_fragments(
        tus,
        header_ast_parser=header_ast_parser,
        backend=backend,
        compiler=compiler,
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot,
        nostdinc=nostdinc,
        lang=lang,
        exported_dynamic=exported_dynamic,
        exported_static=exported_static,
        public_header_paths=resolved_public_paths,
        public_dir_paths=resolved_public_dirs,
        extra_hash_dirs=extra_hash_dirs,
        frontend_context=frontend_context,
        pruning_header_roots=manifest_pruning_roots,
    )

    # A `contributes_to_abi: false` TU exists purely to satisfy other TUs'
    # compiles (e.g. a private support header) -- its own declarations must
    # never reach the merged ABI surface. Parse-time's `contributes_to_abi
    # =True ⇒ required=True` invariant only guarantees the FAILURE half of
    # this (a dropped optional TU can never have been meant to contribute);
    # this is the separate SUCCESS half -- a non-contributing TU that parses
    # fine must still be excluded here, which neither this function nor
    # merge_tu_fragments previously did (Codex review). `fragments` isn't
    # reliably index-aligned with `tus` (an optional TU's failure drops its
    # entry rather than leaving a placeholder), so match by name instead.
    contributing_names = {tu.name for tu in tus if tu.contributes_to_abi}
    fragments = [f for f in fragments if f.tu_name in contributing_names]

    return merge_tu_fragments(
        fragments,
        public_header_paths=explicit_public_paths,
        public_header_dirs=explicit_public_dirs,
    )


@dataclass(frozen=True)
class ElfHeaderAstResult:
    """The single result shape :func:`resolve_header_ast_result` returns for
    both the legacy single-header path and a real manifest -- everything a
    format handler's snapshot-assembly step needs, so it never has to know
    which of the two actually ran.
    """

    functions: tuple[Function, ...]
    variables: tuple[Variable, ...]
    types: tuple[RecordType, ...]
    enums: tuple[EnumType, ...]
    typedefs: dict[str, str]
    typedefs_qualified: dict[str, str]
    constants: dict[str, str]
    typedef_entity_ids: dict[str, EntityId]
    constant_entity_ids: dict[str, EntityId]
    ast_producer: str
    ast_toolchain: dict[str, str]
    ast_fallback_reason: str | None
    ast_toolchain_supported: bool | None
    ast_toolchain_unsupported_reasons: tuple[str, ...]
    is_clang: bool
    provenance_headers: tuple[Path, ...]
    frontend_context_kind: str | None = None
    # ADR-063 Phase 6 (second/third/fourth slices): the canonical SemanticIR
    # projection of this same merged result -- computed once, here, so both
    # the legacy single-TU dump (`_dump_elf`) and a real manifest dump share
    # one normalizer call instead of each format handler recomputing it from
    # `functions`/`variables`/`types`/`enums`/`typedefs_qualified`/
    # `typedef_entity_ids`/`constants`/`constant_entity_ids` above. See
    # `extract/semantic_normalizer.py`'s own docstring for each slice's scope.
    semantic_ir: SemanticIR | None = None


def resolve_header_ast_result(
    *,
    dump_manifest: DumpManifest | None,
    headers: list[Path],
    extra_includes: list[Path],
    header_ast_parser: HeaderAstParserFn,
    backend: str,
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    sysroot: Path | None,
    nostdinc: bool,
    lang: str | None,
    exported_dynamic: set[str],
    exported_static: set[str],
    public_headers: list[Path] | None,
    public_header_dirs: list[Path] | None,
    extra_hash_dirs: tuple[Path, ...] = (),
    frontend_context: str = "host",
) -> ElfHeaderAstResult:
    """Run the header-AST parse for one dump -- manifest-driven (one
    invocation per TU, merged) when *dump_manifest* is given, otherwise the
    legacy single-TU path -- and return one common result shape either way.

    The legacy branch is a real, one-TU call into :func:`run_tu_fragment`
    (a synthetic ``legacy-main`` :class:`~abicheck.dump_manifest.TranslationUnit`
    built from *headers*/*extra_includes*), not a second, parallel
    implementation of the parse-and-normalize step -- ADR-050 D3's "the
    existing single-TU code path becomes the manifest path's one-TU special
    case". Its *public_header_paths* input is *headers* itself plus
    *public_headers* (matching the legacy CLI's own "the headers you pass
    are always public" rule) since there is no manifest ``roots`` field to
    read it from instead.

    This function -- not the per-format ``dumper.py`` builders -- is where
    the legacy-vs-manifest branch lives, so each builder (``_dump_elf``
    today; ``_dump_pe``/``_dump_macho`` once they support manifests) needs
    only one call site instead of duplicating the branch inline (``dumper.py``
    has no line-count headroom for that -- see this module's own docstring).
    """
    if dump_manifest is not None:
        merged = run_tu_loop(
            dump_manifest.translation_units,
            header_ast_parser=header_ast_parser,
            roots=dump_manifest.roots,
            public_header_paths=dump_manifest.public_header_paths,
            public_header_dirs=dump_manifest.public_header_dirs,
            backend=backend,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang,
            exported_dynamic=exported_dynamic,
            exported_static=exported_static,
            extra_hash_dirs=extra_hash_dirs,
            # A manifest's own frontend_context field is authoritative for
            # every one of its TUs (ADR-050 D3's parse-time rule already
            # forces one compiler/target per manifest) -- it takes
            # precedence over the caller's own requested value, mirroring
            # how public_header_paths/public_header_dirs above already
            # prefer the manifest's own fields.
            frontend_context=dump_manifest.frontend_context,
        )
        provenance_headers = tuple(dump_manifest.roots)
    else:
        legacy_tu = TranslationUnit(
            name="legacy-main",
            forced_includes=tuple(headers),
            includes=tuple(IncludeEntry(path=p) for p in extra_includes),
        )
        fragment = run_tu_fragment(
            legacy_tu,
            header_ast_parser=header_ast_parser,
            backend=backend,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang,
            exported_dynamic=exported_dynamic,
            exported_static=exported_static,
            public_header_paths=[str(h) for h in headers]
            + [str(h) for h in (public_headers or [])],
            public_dir_paths=[str(d) for d in (public_header_dirs or [])],
            extra_hash_dirs=extra_hash_dirs,
            frontend_context=frontend_context,
        )
        merged = MergedTuFragments(
            functions=fragment.functions,
            variables=fragment.variables,
            types=fragment.types,
            enums=fragment.enums,
            typedefs=fragment.typedefs,
            typedefs_qualified=fragment.typedefs_qualified,
            constants=fragment.constants,
            typedef_entity_ids=fragment.typedef_entity_ids,
            constant_entity_ids=fragment.constant_entity_ids,
            ast_producer=fragment.ast_producer,
            ast_toolchain=fragment.ast_toolchain,
            ast_fallback_reason=fragment.ast_fallback_reason,
            ast_toolchain_supported=fragment.ast_toolchain_supported,
            ast_toolchain_unsupported_reasons=fragment.ast_toolchain_unsupported_reasons,
            frontend_context_kind=fragment.frontend_context_kind,
        )
        provenance_headers = tuple(headers)

    return ElfHeaderAstResult(
        functions=merged.functions,
        variables=merged.variables,
        types=merged.types,
        enums=merged.enums,
        typedefs=merged.typedefs,
        typedefs_qualified=merged.typedefs_qualified,
        constants=merged.constants,
        typedef_entity_ids=merged.typedef_entity_ids,
        constant_entity_ids=merged.constant_entity_ids,
        ast_producer=merged.ast_producer,
        ast_toolchain=merged.ast_toolchain,
        ast_fallback_reason=merged.ast_fallback_reason,
        ast_toolchain_supported=merged.ast_toolchain_supported,
        ast_toolchain_unsupported_reasons=merged.ast_toolchain_unsupported_reasons,
        frontend_context_kind=merged.frontend_context_kind,
        is_clang=merged.ast_producer == "clang",
        provenance_headers=provenance_headers,
        semantic_ir=normalize_header_ast(
            types=merged.types,
            enums=merged.enums,
            typedefs_qualified=merged.typedefs_qualified,
            typedef_entity_ids=merged.typedef_entity_ids,
            producer=merged.ast_producer,
            functions=merged.functions,
            variables=merged.variables,
            constants=merged.constants,
            constant_entity_ids=merged.constant_entity_ids,
        ),
    )
