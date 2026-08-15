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

import dataclasses
from typing import TYPE_CHECKING

from ._compiler_options import split_gcc_options
from .errors import SnapshotError, ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from .api_types import InputSpec
    from .compile_context import CompileContext
    from .model import AbiSnapshot
    from .service_compare_evidence import SideEvidence

__all__ = [
    "embed_side_build_source",
    "enforce_requested_depth",
    "is_raw_source_tree",
    "reject_hybrid_source_frontend",
    "resolve_side_snapshot",
]


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


def _seeded_includes(
    side: InputSpec, evidence: SideEvidence
) -> tuple[list[Path], list[Callable[[], None]]]:
    """This input's include dirs, plus any the build already knows about.

    When headers are given with ``sources``/``build_info`` but no explicit
    ``includes``, the L2 public-header parse cannot see the include dirs the
    build knows (the pvxs/EPICS case: public headers that reach into a
    dependency SDK). The CLI has seeded them from the build since ADR-033
    (``cli_dump_helpers``' two ``seed_l2_includes`` calls); the typed path did
    not, so an identical ``DumpRequest``/``CompareRequest`` parsed less than
    the equivalent CLI invocation and degraded or failed (Codex review).

    ``allow_inferred_build_query=False``, unlike the CLI's
    ``collect_mode != "off"``: passive discovery of an existing compile
    database still applies, but a Tier-2 API call must never *execute* a
    build system (cmake/make/bazel) as a side effect of resolving an input.
    That is a surprise a library caller cannot see coming, and the CLI only
    permits it because the user typed a command that says so.

    Returns the cleanups the caller must run **after** the parse consumes the
    dirs — an inferred build dir may hold the generated headers they point at.
    """
    if not (side.sources or side.build_info):
        return list(side.includes), []
    from .buildsource.l2_seed import seed_l2_includes

    ctx = evidence.compile
    return seed_l2_includes(
        headers=evidence.headers,
        includes=side.includes,
        sources=side.sources,
        build_info=side.build_info,
        build_config=None,
        defer_cleanup=None,
        gcc_options=ctx.gcc_options if ctx is not None else None,
        gcc_option_tokens=ctx.gcc_option_tokens if ctx is not None else (),
        allow_inferred_build_query=False,
    )


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
    return dataclasses.replace(
        explicit,
        sysroot=None,
        gcc_options=None,
        gcc_option_tokens=(
            *derived.gcc_option_tokens,
            *explicit_tail,
            *explicit.gcc_option_tokens,
        ),
    )


def _seeded_compile_context(
    side: InputSpec,
    evidence: SideEvidence,
    *,
    lang: str = "c++",
    lang_explicit: bool = False,
) -> tuple[CompileContext | None, bool, list[Callable[[], None]]]:
    """Fold L3 ``CompileUnit``-derived ABI context onto this side (P0.3).

    Genuinely applies the real build's compile context (standard, defines/
    undefines, include search paths, sysroot, target triple, ABI-relevant
    flags) to the L2 header-AST invocation when ``sources``/``build_info`` L3
    evidence is available and a ``CompileUnit`` references one of this side's
    headers — instead of only the advisory ``header_parse_context_drift``/
    ``header_build_context_mismatch`` findings this repo already emitted for
    the gap. See ``buildsource.header_compile_context`` for the header→
    ``CompileUnit`` matching heuristic and the single-context/fail-closed-on-
    ambiguity contract (``HeaderCompileContextAmbiguousError`` propagates
    unchanged — a genuine ABI-relevant disagreement across compile units is
    never silently resolved by picking one).

    A no-op (``(evidence.compile, False, [])``) when there is no L3 evidence
    or no headers to match, or when the matched evidence resolves to nothing
    — the exact same behavior as before this function existed, so a caller
    with no build evidence for this side sees no change (backward
    compatible). Returns ``(context, applied, cleanups)`` — ``applied`` is
    True only when a real L3 context was found and folded in, which is what
    the caller uses to decide whether to stamp
    ``AbiSnapshot.parsed_with_build_context``.

    *lang*/*lang_explicit* (``discussion_r3787398644``, Codex review):
    this side's own requested parse language, forwarded unchanged to
    :func:`~abicheck.buildsource.l2_seed.derive_l2_compile_context` so a
    matched compile unit's derived ``-std=`` whose language family
    conflicts with an explicitly forced language is omitted rather than
    forwarded into a parse that would reject it (e.g. a matched C compile
    unit's ``-std=c17`` forwarded into an explicitly-forced C++ parse).
    """
    if not (side.sources or side.build_info) or not evidence.headers:
        return evidence.compile, False, []
    from .buildsource.l2_seed import derive_l2_compile_context

    derived, cleanups = derive_l2_compile_context(
        headers=list(evidence.headers),
        build_info=side.build_info,
        sources=side.sources,
        build_config=None,
        allow_inferred_build_query=False,
        # Finding 3: fold the caller's own already-explicit context into
        # ambiguity resolution so a field it already pins (e.g. an explicit
        # -std=c++20) excuses a same-field-only disagreement across matched
        # compile units instead of failing closed on it.
        explicit=evidence.compile,
        lang=lang,
        lang_explicit=lang_explicit,
    )
    if derived is None:
        return evidence.compile, False, cleanups
    return _merge_l3_compile_context(evidence.compile, derived), True, cleanups


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
    """
    from . import service

    # Accumulated incrementally (not built from two independent calls before
    # entering `try`) so a HeaderCompileContextAmbiguousError raised by
    # _seeded_compile_context still drains whatever temp-build-dir cleanups
    # _seeded_includes already created, instead of leaking them.
    cleanups: list[Callable[[], None]] = []
    try:
        includes, includes_cleanups = _seeded_includes(side, evidence)
        cleanups.extend(includes_cleanups)
        compile_ctx, context_applied, context_cleanups = _seeded_compile_context(
            side, evidence, lang=lang, lang_explicit=lang_explicit
        )
        cleanups.extend(context_cleanups)
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
            )
    finally:
        # Only after the L2 parse (and any embed) has consumed the seeded dirs:
        # an inferred CMake build dir can hold the generated headers they point
        # into, so draining earlier would delete them mid-parse.
        if cleanups:
            from .buildsource.inline import _run_cleanups

            _run_cleanups(cleanups)
    return snap


def embed_side_build_source(
    snap: AbiSnapshot,
    side: InputSpec,
    evidence: SideEvidence,
    header_backend: str,
    public_headers: list[Path],
    public_header_dirs: list[Path],
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
