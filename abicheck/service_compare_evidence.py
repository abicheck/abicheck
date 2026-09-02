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

"""ADR-055 D1 helpers: resolve the new :class:`CompareRequest`/:class:`InputSpec`
build/source-evidence fields into the primitives :func:`service.run_compare_request`
already knows how to drive (a collect mode, a per-side header list, a per-side
:class:`CompileContext`).

G33 Phase 5 gave :class:`~abicheck.api_types.DumpRequest` the same fields, so
the per-input half of that resolution is shared:
:func:`resolve_side_evidence` answers for one :class:`InputSpec`, and
:func:`resolve_compare_request_evidence` / :func:`resolve_dump_request_evidence`
are the two-input and one-input callers of it. (The module keeps its
``compare``-shaped name: that is where all of this started, and renaming it
would churn every import for no reader benefit.)

Split out of ``service.py`` rather than inlined there: that module sits at the
2000-line AI-readiness hard cap. Deliberately kept a *leaf* module (only
``compile_context``/``buildsource.scan_levels``, both stdlib-deps-only) rather
than reusing ``cli_dump_helpers.resolve_dump_depth`` -- that module is a member
of the CLI/service import-cycle-allowlisted cluster (CLAUDE.md "M1-3"), and
this module is called from ``service.py`` (also a cluster member), so
importing a cluster module here would fold this module into that cluster too
(AGENTS.md "What NOT to do": prefer a leaf module over extending
``IMPORT_CYCLE_ALLOWLIST``). The depth->collect-mode mapping is small enough
to inline directly against ``buildsource.scan_levels`` instead.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

from .buildsource.scan_levels import (
    EvidenceDepth,
    SourceScope,
    depth_to_method,
    level_to_collect_mode,
)
from .compile_context import CompileContext

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .api_types import CompareRequest, DumpRequest, InputSpec
    from .dump_manifest import DumpManifest

__all__ = [
    "SideEvidence",
    "collect_mode_for",
    "dump_collect_mode_for",
    "L4_SOURCE_EXTRACTORS",
    "effective_frontend",
    "explicit_source_extractor",
    "normalized_debug_format",
    "reject_compile_db_filter_scope_mismatch",
    "reject_debug_format_for_binaries",
    "reject_debug_format_for_non_elf",
    "resolve_compare_request_evidence",
    "resolve_dump_request_evidence",
    "resolve_side_evidence",
]


def effective_frontend(compile: CompileContext | None, header_backend: str) -> str:
    """The L2 frontend `resolve_input`/`run_dump` actually use for *compile*
    (an explicit `compile.frontend` wins over the bare `header_backend`
    default), resolved to a concrete backend -- reused so `embed_build_
    source`'s `extractor` matches instead of silently diverging (Codex
    review, three rounds: an explicit override wasn't case-normalized in the
    first pass; "auto" itself was forwarded unresolved in the second --
    `_make_source_extractor` doesn't special-case "auto" and falls back to
    Clang, while L2's own "auto" resolves to castxml by default; and the
    "is this actually 'auto'" check itself was still case-sensitive in the
    third -- `compile.frontend="AUTO"` is an accepted spelling
    (`frontend_value_errors` validates case-insensitively) that used to be
    treated as an *explicit* override instead of the no-op it means)."""
    from .dumper import _resolve_header_backend

    requested = (
        compile.frontend
        if (compile is not None and compile.frontend.lower() != "auto")
        else header_backend
    )
    return _resolve_header_backend(requested)


#: The backends an ``--ast-frontend`` request can actually select for L4
#: source-ABI replay: the overlap between ``dumper.HEADER_BACKENDS`` (what the
#: flag accepts) and what ``buildsource.inline._make_source_extractor``
#: implements. Deliberately *not* every value :func:`effective_frontend` can
#: return -- ``hybrid`` is an L2-only, dual-backend header-AST mode with no L4
#: counterpart at all, which is why ``_make_source_extractor`` answers it with
#: a ``skipped`` ExtractorRecord rather than an extractor. (The ``android``
#: adapter in ``buildsource.source_extractors.resolver`` is a third L4 backend,
#: but it needs a pre-captured dump and is unreachable from ``--ast-frontend``,
#: so no frontend request can ever resolve to it here.)
L4_SOURCE_EXTRACTORS: frozenset[str] = frozenset({"castxml", "clang"})


def explicit_source_extractor(
    compile: CompileContext | None,
) -> str | None:
    """The L4 replay backend explicitly *named*, or ``None`` for anything else.

    Exists so a caller that must not change its own ``auto`` behaviour can
    still honor a *named* backend the same way every other resolver does.
    ``scan``'s candidate resolution is that caller: it has always let its L4
    replay take ``_make_source_extractor``'s own clang reading of ``"auto"``
    (see ``scan_engine``'s call site), so adopting :func:`effective_frontend`
    wholesale would newly *require* castxml for a plain ``scan --depth
    source`` that works with clang today. Answering only the named case keeps
    that behaviour untouched while closing the real defect underneath it --
    ``scan --ast-frontend castxml`` silently replaying L4 through clang, which
    is what made a ``scan --against`` candidate non-comparable with a ``dump``
    baseline taken with the identical flag.

    **"Named" means ``castxml``/``clang``, never ``auto`` -- including an
    ``auto`` the user typed out** (Codex review, PR #990). That is a real
    remaining divergence, not an oversight: ``scan`` resolves ``auto`` to
    clang and ``dump``/``compare`` resolve it to castxml, so
    ``--ast-frontend auto`` on both sides still reproduces the spurious
    ``source_fact_coverage_incomplete`` verdict on unchanged source
    (reproduced directly while reviewing this). It is deliberately left to
    the plan's PR 3A item 2, because the only coherent fix is to make the
    *word* mean one thing across commands -- i.e. the default change this
    function exists to avoid. Honoring a typed ``auto`` while an omitted one
    kept clang would be strictly worse: the same word would select different
    backends depending on whether it was typed, and ``CompileContext.frontend``
    is the same string either way (the CLI's own typed-vs-default signal
    exists only to rank an explicit value above a config one, not to change
    what the value resolves to).

    Deliberately delegates to :func:`effective_frontend` rather than
    re-deriving the resolution, so an explicit request cannot resolve to one
    backend here and a different one there: the two agree *by construction*,
    not by two copies of the same rule staying in sync. ``header_backend`` is
    passed as ``"auto"`` because it is only consulted when ``compile``
    states nothing explicit -- the branch this function has already returned
    ``None`` for.

    **Total over its input domain, and deliberately not a validator.** It
    answers only "is there an explicit L4 backend request here?", returning
    ``None`` -- "the caller's own default stands" -- for everything else: a
    missing context, an ``auto``/unstated frontend, ``hybrid`` and
    ``android`` (neither is an L4 request this path could honor -- see
    :data:`L4_SOURCE_EXTRACTORS`), and any value that is not a frontend at
    all. It never raises.

    That the membership test comes *before* the delegation is the point, not
    an optimization (CodeRabbit review, PR #990). ``effective_frontend``
    resolves through ``dumper._resolve_header_backend``, which raises
    ``ValidationError`` on anything outside the four *header-AST* backends --
    a strictly narrower set than ``api_types.SUPPORTED_FRONTENDS``, which
    also admits ``android``. Delegating first would therefore turn a value
    the typed API calls valid into a crash on ``scan``, which previously
    could not fail here at all. Today three upstream validators (the CLI's
    own ``AST_FRONTENDS`` choice, the ``.abicheck.yml`` ``compile.frontend``
    check, and ``frontend_value_errors``) each reject ``android`` before it
    could reach a ``scan`` compile context, so this is latent rather than
    live -- but a resolver that is correct only while two independently
    maintained vocabularies stay in sync is precisely the failure this whole
    change set exists to remove. Validating frontends stays those
    validators' job; this function's job is to answer a question.
    """
    if compile is None:
        return None
    if compile.frontend.lower() not in L4_SOURCE_EXTRACTORS:
        return None
    # Still routed through `effective_frontend` rather than returned
    # directly, so the "agrees with dump/compare by construction" property
    # above survives any future normalization it grows.
    resolved = effective_frontend(compile, "auto")
    return resolved if resolved in L4_SOURCE_EXTRACTORS else None


@dataclasses.dataclass(frozen=True)
class SideEvidence:
    """One side's resolved ADR-055 D1 evidence, ready for ``resolve_input``/
    ``embed_build_source``."""

    headers: list[Path]
    compile: CompileContext | None
    collect_mode: str
    dump_manifest: DumpManifest | None


def _resolve_depth_collect_mode(depth: str | None, default_mode: str) -> str:
    # Mirrors cli_dump_helpers.resolve_dump_depth's own depth->collect-mode
    # mapping (duplicated here, not imported, to stay a leaf module -- see
    # this module's own docstring).
    if depth is None:
        return default_mode
    evidence_depth = EvidenceDepth(depth.lower())
    method = depth_to_method(evidence_depth)
    if method is None:
        return "off"
    return level_to_collect_mode(
        method, evidence_depth, source_scope=SourceScope.TARGET
    )


def collect_mode_for(depth: str | None, *sides: InputSpec) -> str:
    """The build-source collect mode *depth* (or the inputs) implies.

    Mirrors the CLI's own --depth-omitted inference
    (cli_compare_helpers._resolve_compare_collect_mode): explicit depth
    always wins; else sources infers "source", build_info infers "build",
    nothing at all stays "off". Variadic in *sides* so ``compare`` asks over
    both of its inputs and ``dump`` over its single one — the rule is the
    same, only the number of inputs that can carry the evidence differs.
    """
    if depth is not None:
        return _resolve_depth_collect_mode(depth, "off")
    if any(side.sources for side in sides):
        return _resolve_depth_collect_mode("source", "off")
    if any(side.build_info for side in sides):
        return _resolve_depth_collect_mode("build", "off")
    return "off"


def dump_collect_mode_for(depth: str | None) -> str:
    """The build-source collect mode a *dump* resolves ``depth`` to.

    Deliberately **not** :func:`collect_mode_for` (CLI cleanup phase two, PR 3A
    blocker 5, sub-issue 2). That function mirrors ``compare``'s own rule
    (``cli_compare_helpers._resolve_compare_collect_mode``): omitted depth is
    *inferred* from whichever inputs were supplied, defaulting to ``"off"``.
    The ``dump`` CLI has always resolved omitted depth differently — one fixed
    default, ``"source-target"``, regardless of inputs
    (``cli_dump_helpers.resolve_dump_collect_context``'s
    ``resolve_dump_depth(depth, "source-target")``) — and the two genuinely
    disagreed for one measurable input shape: ``--build-info`` with no
    ``--depth`` resolved to ``"source-target"`` on the CLI and ``"build"``
    through the typed path, so the same invocation attempted L4 replay in one
    front end and stopped at L3 in the other.

    The CLI's default is canonical: it is the older, documented, tested
    behaviour, and changing it would silently stop a ``dump --build-info
    <pack>`` at L3 that reaches L4 today. So the typed ``dump`` path resolves
    through this function instead, mirroring ``resolve_dump_depth`` exactly.
    Pinned by ``tests/test_dump_cli_typed_api_parity.py``'s
    ``TestCollectModeParity``, which enumerates every
    ``(depth, sources-given, build_info-given)`` combination against the real
    CLI resolver.

    ``compare`` is untouched — :func:`collect_mode_for` keeps its own rule,
    which is correct for its own front end.
    """
    return _resolve_depth_collect_mode(depth, "source-target")


def _headers(side: InputSpec, depth: str | None) -> list[Path]:
    # depth == "binary" clears headers before resolving (matches the CLI's
    # cli_compare_helpers._normalize_compare_options) -- otherwise a
    # binary-only depth request that still carried headers would silently
    # keep running L2.
    if depth is not None and depth.lower() == "binary":
        return []
    return list(side.headers)


def _dump_manifest(side: InputSpec, depth: str | None) -> DumpManifest | None:
    # depth == "binary" also clears dump_manifest (Codex review) -- otherwise
    # a binary-only depth request still runs the manifest's own multi-TU L2
    # header extraction, exactly the L2 evidence "binary" is meant to skip.
    if depth is not None and depth.lower() == "binary":
        return None
    return side.dump_manifest


def _compile_context(
    side_compile: CompileContext | None,
    pair_compile: CompileContext | None,
    frontend_context: str,
) -> CompileContext | None:
    # Merge a side's explicit compile override, the pair-wide C++20 dialect
    # override, and the request-level frontend_context default.
    #
    # frontend_context specifically has a known, accepted limitation (Codex
    # review, two rounds): CompileContext.frontend_context is a plain
    # ``str = "host"`` field with no way to represent "unset" -- so a bare
    # equality check can never distinguish a side that explicitly pinned
    # "host" from one that just happens to carry the untouched default
    # because it set some unrelated field (e.g. only `sysroot`). Giving
    # CompileContext a real sentinel would fix this properly, but that field
    # is read/constructed in ~20 other modules (service.py's own
    # `cc.frontend_context == "host"` branch included), so a wider type
    # change is out of scope for this fix. Resolved by always applying the
    # request-level default whenever a side's frontend_context reads as the
    # class default ("host") -- i.e. treating an unrelated override the same
    # as no override at all for this one field. The accepted cost: a side
    # that deliberately wants "host" while the request otherwise defaults to
    # "device" cannot express that through `InputSpec.compile.frontend_context`
    # alone; it must instead pin the *other* side to "device" explicitly and
    # leave the request-level `frontend_context` at "host".
    base = side_compile
    # Codex review: request.frontend_context is normalized to lowercase above
    # (resolve_compare_request_evidence), but a per-side InputSpec.compile.
    # frontend_context passed straight through unchanged -- an accepted
    # case-insensitive spelling like "DEVICE" then compared unequal to the
    # lowercase literal every real consumer (e.g. sycl_context.py) checks
    # against. api_types.py's validation_errors() now rejects anything that
    # doesn't lowercase to "host"/"device", so normalizing here is safe.
    if base is not None and base.frontend_context != base.frontend_context.lower():
        base = dataclasses.replace(base, frontend_context=base.frontend_context.lower())
    if pair_compile is not None and pair_compile.gcc_option_tokens:
        # Codex review (P2): an unrelated side_compile override (e.g. only a
        # sysroot) must not silently discard the pair-wide C++20 dialect
        # decision -- merge its tokens in unless this side already pins its
        # own explicit standard.
        from ._compiler_options import has_explicit_std

        if base is None:
            base = pair_compile
        elif not has_explicit_std(base.gcc_options, base.gcc_option_tokens):
            base = dataclasses.replace(
                base,
                gcc_option_tokens=base.gcc_option_tokens + pair_compile.gcc_option_tokens,
            )
    if base is None:
        return (
            CompileContext(frontend_context=frontend_context)
            if frontend_context != "host"
            else None
        )
    if frontend_context != "host" and base.frontend_context == "host":
        base = dataclasses.replace(base, frontend_context=frontend_context)
    return _header_ast_frontend_only(base)


def _header_ast_frontend_only(base: CompileContext) -> CompileContext:
    """Strip a non-header-AST ``frontend`` from a resolved compile context.

    ``android`` is a member of ``SUPPORTED_FRONTENDS`` but not of
    ``HEADER_AST_FRONTENDS``: it is source-ABI only, with no header-AST path.
    Both pipelines already honour that when computing the bare
    ``header_backend`` (anything outside ``HEADER_AST_FRONTENDS`` falls back to
    ``"auto"``) — but ``service._run_dump_uncached`` gives an explicit
    ``compile.frontend`` *precedence* over that argument, and
    ``dumper._resolve_header_backend`` raises ``ValidationError`` for anything
    outside ``castxml``/``clang``/``hybrid``/``auto``. So a resolved context
    still carrying ``"android"`` made the whole extraction fail with "Unknown
    AST frontend 'android'" before any build/source evidence was embedded —
    for a value both front ends document as accepted (Codex review, P2).

    Fixed here rather than at either caller because the invariant is about
    what may reach ``resolve_input``, and it holds for both: a
    ``CompareRequest`` whose ``InputSpec.compile.frontend`` is ``"android"``
    failed identically (verified), so nothing can be relying on the old
    behaviour. The request-level ``frontend`` field still carries the value
    for L4 source-ABI replay, which is the only layer that can act on it.
    """
    from .api_types import HEADER_AST_FRONTENDS, SUPPORTED_FRONTENDS

    frontend = base.frontend.lower()
    if frontend in HEADER_AST_FRONTENDS:
        return base
    # Only a *known* frontend with no header path is downgraded. An unknown
    # value falls through untouched so `dumper._resolve_header_backend` still
    # raises `Unknown AST frontend` for it: rewriting a typo like "clnag" to
    # "auto" would turn it into a successful default-backend run, trading the
    # bug this function fixes for a worse one (Codex review). Both request
    # types now also reject such a value in `validate()`, so this is the
    # second line of defence rather than the only one.
    if frontend not in SUPPORTED_FRONTENDS:
        return base
    return dataclasses.replace(base, frontend="auto")


def resolve_side_evidence(
    side: InputSpec,
    *,
    depth: str | None,
    collect_mode: str,
    pair_compile: CompileContext | None,
    frontend_context: str,
) -> SideEvidence:
    """One input's resolved evidence, ready for ``resolve_input``/``embed_build_source``.

    *frontend_context* must already be lowercased by the caller (both request
    types validate it case-insensitively; every real consumer compares against
    the lowercase literals).
    """
    return SideEvidence(
        headers=_headers(side, depth),
        compile=_compile_context(side.compile, pair_compile, frontend_context),
        collect_mode=collect_mode,
        dump_manifest=_dump_manifest(side, depth),
    )


def resolve_dump_request_evidence(request: DumpRequest) -> SideEvidence:
    """Resolve a :class:`~abicheck.api_types.DumpRequest`'s single input.

    No pair-wide compile context: the C++20 dialect override
    (``service_scan.pair_wide_cxx20_std_override``) exists so two *sides* of a
    comparison cannot silently disagree on a language standard. A lone dump has
    nothing to disagree with, and ``dumper.py``'s own per-input C++20 heuristic
    already runs — so forcing a pair-wide decision here would change what a
    single ``dump`` produces relative to today for no benefit.

    ``request.resolved_collect_mode``, when set, wins over the ``depth``-only
    derivation (Codex review) — see that field's own docstring in
    ``api_types.py`` for why re-deriving it here would disagree with what
    ``compare``'s implicit-dump path already resolved.
    """
    return resolve_side_evidence(
        request.input,
        depth=request.depth,
        collect_mode=(
            request.resolved_collect_mode
            if request.resolved_collect_mode is not None
            else dump_collect_mode_for(request.depth)
        ),
        pair_compile=None,
        frontend_context=request.frontend_context.lower(),
    )


def resolve_compare_request_evidence(
    request: CompareRequest,
    pair_compile: CompileContext | None,
) -> tuple[SideEvidence, SideEvidence]:
    """Resolve ADR-055 D1's ``depth``/``sources``/``build_info``/``compile``/
    ``frontend_context`` fields into (old, new) :class:`SideEvidence`, ready
    for ``resolve_input``/``embed_build_source``.

    *pair_compile* is the pre-existing pair-wide C++20 dialect override
    (``service_scan.pair_wide_cxx20_std_override``); each side's own
    ``InputSpec.compile`` takes precedence over it.
    """
    collect_mode = collect_mode_for(request.depth, request.old, request.new)
    # Codex review: validate() accepts frontend_context case-insensitively,
    # but every real consumer compares against the lowercase "host"/"device"
    # literals -- normalize once here so an accepted "DEVICE" doesn't
    # silently behave as neither.
    frontend_context = request.frontend_context.lower()

    def _side(side: InputSpec) -> SideEvidence:
        return resolve_side_evidence(
            side,
            depth=request.depth,
            collect_mode=collect_mode,
            pair_compile=pair_compile,
            frontend_context=frontend_context,
        )

    return _side(request.old), _side(request.new)


def reject_compile_db_filter_scope_mismatch(
    sides: Iterable[tuple[str, InputSpec, SideEvidence]],
) -> None:
    """Raise if any side's ``InputSpec.compile_db_filter`` would narrow the L2
    header parse while its L3 build evidence -- collected by
    ``embed_build_source``, which has no filter concept of its own -- stays
    unfiltered.

    Mirrors the native ``dump`` CLI's own
    ``header_conditionals.compile_db_filter_scope_error`` refusal (``cli.py``'s
    ``dump_cmd``), computed per side from that side's own resolved
    :class:`~abicheck.service_compare_evidence.SideEvidence` --
    ``evidence.collect_mode``/``evidence.headers`` are the same resolved
    values the CLI reads ``compile_db_from_build_info`` back against.

    *sides* is ``(label, side, evidence)`` triples, mirroring
    :func:`reject_debug_format_for_binaries`'s ``(label, ...)`` shape --
    ``dump`` passes its single ``input``, ``compare`` passes ``old``/``new``.
    Without this, a typed ``CompareRequest`` side could reach the exact
    L2-filtered/L3-unfiltered snapshot shape the CLI refuses outright: a
    `compile_db_filter` narrows `resolve_side_snapshot`'s own P0.3 L3->L2 fold
    (`_seeded_includes_and_compile_context`) but `embed_side_build_source`
    collects L3 evidence from the *whole* compile database regardless, so the
    resulting snapshot carries build facts for translation units the header
    parse never saw (Codex review — `resolve_dump_request`'s own guard,
    landed first, covered `DumpRequest` alone).

    Resolves the checked compile database via
    ``header_conditionals.compile_db_for_filter_scope_check`` -- not the
    narrower ``compile_db_from_build_info`` -- so a side whose database is
    only auto-discovered from ``sources`` (no explicit ``build_info`` at
    all) is covered too (Codex review, fresh evidence: the fold and the L3
    embed both resolve that same auto-discovered database independently,
    so the identical mismatch reproduces with no ``build_info`` in play).
    """
    from .errors import ValidationError
    from .header_conditionals import (
        compile_db_filter_scope_error,
        compile_db_for_filter_scope_check,
    )

    for label, side, evidence in sides:
        error = compile_db_filter_scope_error(
            side.compile_db_filter,
            compile_db_for_filter_scope_check(
                side.build_info, side.sources, tuple(evidence.headers)
            ),
            evidence.collect_mode,
        )
        if error is not None:
            raise ValidationError(f"{label}: {error}")


def normalized_debug_format(request: CompareRequest | DumpRequest) -> str | None:
    """A request's ``debug_format`` in the form the extraction layer takes.

    Two normalizations, both mirroring what the CLI already does to the same
    flag (``cli_compare_helpers``/``cli_dump_helpers``), so the typed path
    behaves identically:

    * lowercased — ``validate()`` accepts the four values case-insensitively,
      the way ``--debug-format``'s ``case_sensitive=False`` choice does, but
      ``dumper_debug._resolve_debug_metadata`` compares against lowercase
      literals only;
    * ``"auto"`` becomes ``None`` — auto-detection *is* "no format forced" at
      that layer. Passing the literal string through instead reaches an
      explicit ``raise ValueError`` for anything outside ``dwarf``/``btf``/
      ``ctf``, so an accepted, documented value crashed during extraction
      (Codex review, second round; the first round's fix validated the value
      but still forwarded it verbatim).
    """
    if request.debug_format is None:
        return None
    normalized = request.debug_format.lower()
    return None if normalized == "auto" else normalized


def reject_debug_format_for_binaries(
    debug_format: str | None, sides: Iterable[tuple[str, str | None]]
) -> None:
    """Raise if a forced ELF debug format meets a PE/Mach-O input.

    *sides* is ``(label, detected format)`` pairs, so the message names the
    input that cannot honour the request — ``compare`` passes ``old``/``new``,
    ``dump`` its single ``input``. See
    :func:`reject_debug_format_for_non_elf` for why this is rejected up front
    rather than silently dropped.
    """
    from .errors import ValidationError

    if debug_format is None:
        return
    for side, binary_fmt in sides:
        if binary_fmt in ("pe", "macho"):
            raise ValidationError(
                f"debug_format={debug_format!r} is only supported for ELF "
                f"binaries, but the {side} input is {binary_fmt.upper()}."
            )


def reject_debug_format_for_non_elf(
    debug_format: str | None, old_fmt: str | None, new_fmt: str | None
) -> None:
    """Raise if a forced ELF debug format meets a PE/Mach-O side.

    The PE and Mach-O dump paths take no debug-format argument, so a forced
    ``dwarf``/``btf``/``ctf`` is silently dropped there and the run reports
    success having ignored what the caller asked for. The CLI rejects the
    combination up front (``cli_compare_helpers._reject_debug_format_for_non_elf``);
    without this the typed surface claimed that parity without having it
    (Codex review).

    A JSON-snapshot or dump input has ``fmt is None`` and is unaffected, same
    as on the CLI side.
    """
    reject_debug_format_for_binaries(
        debug_format, (("old", old_fmt), ("new", new_fmt))
    )
