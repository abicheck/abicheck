# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Inline build/source collection for ``dump --build-info``/``--sources``.

The source-tree-centric model (ADR-028..033 amendment, 2026-06-12): instead of
attaching a prebuilt pack directory, ``dump`` collects
the normalized L3/L4/L5 facts *inline* from raw inputs and embeds them in the
``.abi.json``:

- ``--sources <tree>`` — a source checkout (e.g. at the build tag). Runs L4
  source ABI replay and the L5 source graph summary internally.
- ``--build-info <path>`` — an optional build dir / ``compile_commands.json`` /
  pre-captured build-evidence pack supplying L3 build context. When omitted, a
  ``compile_commands.json`` inside the source tree is auto-discovered.

A per-project ``.abicheck.yml`` ``build:`` block can name the build system and a
*query* command that emits a compile DB without performing a full build; running
that query is gated by an explicit, operator-supplied ``--config`` alone
(ADR-032 D5 ``query_build_system`` action ceiling — read by default, trusted
query opt-in, full build never). ``--allow-build-query`` is a deprecated
no-op kept only for backward compatibility — it neither grants nor restricts
this permission (see :func:`collect_inline_pack`'s ``allow_build_query``
docstring). The separate abicheck-authored *inferred* cmake/bazel/make query
(:func:`_resolve_compile_db`) runs whenever ``--sources`` needs L3 regardless
of any flag — pointing abicheck at a source tree is itself the request to
analyse it.

Everything here is best-effort (ADR-028 D3): a missing tool or unreadable input
degrades L3/L4/L5 to partial/not-collected coverage and never aborts the dump —
the artifact tiers (L0/L1/L2) stay authoritative.

The ``.abicheck.yml`` config *schema* itself (``BuildConfig``,
``load_build_config``, ``discover_build_config``) now lives in
:mod:`abicheck.buildsource.build_config` — split out (G38 Phase 15 file-split
prerequisite) because that schema has no dependency on the collection
pipeline below, and this file was within a couple of lines of the
AI-readiness 2000-line hard cap. Re-exported here for back-compat.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import shlex
import subprocess
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from .. import deadline
from .build_config import (
    KNOWN_TOP_LEVEL_KEYS as KNOWN_TOP_LEVEL_KEYS,
    BuildConfig as BuildConfig,
    discover_build_config as discover_build_config,
    load_build_config as load_build_config,
)
from .build_evidence import BuildEvidence, comdat_scan_requested, l3_coverage_fields
from .model import (
    CoverageStatus,
    DataLayer,
    ExtractorRecord,
    LayerConfidence,
    LayerCoverage,
)
from .pack import BuildSourcePack
from .pack_shape import is_pack_dir as is_pack_dir
from .redaction import DEFAULT_REDACTION

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary
    from .source_abi import SourceAbiSurface
    from .source_extractors import SourceAbiExtractor

#: Default places to look for a compile DB inside a source checkout, in order.
logger = logging.getLogger(__name__)

_COMPILE_DB_NAME = "compile_commands.json"
#: ``builddir`` is the name the Meson docs/tutorials use for `meson setup builddir`
#: (P12); ``build``/``_build``/``out`` cover CMake/Ninja conventions.
_COMPILE_DB_HINTS = ("", "build", "builddir", "out", "_build", "cmake-build-debug")

#: Build-query subprocess wall-clock ceiling. A query/extraction command
#: (cquery/aquery/ninja -t/make -n) should be fast; a runaway one is treated as
#: a failed extractor rather than hanging the dump.
_QUERY_TIMEOUT_S = 300
# build_query extractor statuses worth surfacing as an A3 diagnostic (no facts):
# skipped (not allowed), failed (errored/unparseable), partial (ran, no compile
# DB produced). "ok" means a DB was produced, so it needs no special handling.
_BUILD_QUERY_DIAG_STATUSES = ("failed", "skipped", "partial")

# Extractor names that carry a build-query no-facts diagnostic: the explicit
# trusted `build.query` ("build_query") and the zero-config inferred query
# ("build_query_auto"). Both must be treated alike in the pack-survival gate and
# the L3 coverage row so an inferred-query-only run keeps its explanation.
_BUILD_QUERY_DIAG_NAMES = ("build_query", "build_query_auto")

# ``BuildConfig``/``load_build_config``/``discover_build_config`` are
# re-exported above (``import ... as ...``) for the callers that have always
# imported them from here — the ``.abicheck.yml`` config schema itself now
# lives in :mod:`abicheck.buildsource.build_config` (split out purely to
# keep this file under the AI-readiness line-count cap; see that module's
# own docstring).
#
# ``is_pack_dir`` is re-exported above the same way. It is owned by
# :mod:`abicheck.buildsource.pack_shape` since ADR-061 Phase 3: it has no
# first-party dependencies, so keeping it in this (oversized) module forced
# every engine-side consumer to import `inline` for a filesystem predicate.


def effective_graph_scope(graph_detail: str, scope: str) -> str:
    """Apply the ADR-037 D6 ``sources.graph`` detail cap to a replay scope.

    ``full`` deepens a ``changed`` scope to ``target`` (full replay); ``summary``
    (the default) leaves the requested scope untouched. The override only ever
    *widens* — it never silently drops evidence.
    """
    if graph_detail == "full" and scope == "changed":
        return "target"
    return scope


def _run_cleanups(cleanups: list[Callable[[], None]]) -> None:
    """Run every registered cleanup, never letting one failure skip the rest.

    A failure (a temp tree already gone, a read-only mount) must not abort
    collection — but it is logged rather than swallowed silently (bandit B110),
    so a leaked scratch directory is diagnosable instead of invisible.
    """
    for fn in cleanups:
        try:
            fn()
        except Exception:  # noqa: BLE001
            logger.debug("inline pack cleanup failed: %r", fn, exc_info=True)


def collect_inline_pack(
    *,
    sources: Path | None,
    build_info: Path | None,
    build_config: BuildConfig | None = None,
    allow_build_query: bool = False,
    build_config_trusted_for_query: bool = True,
    compile_db_explicit: bool = False,
    allow_inferred_build_query: bool = True,
    base_build: BuildEvidence | None = None,
    clang_bin: str = "clang",
    extractor: str = "clang",
    scope: str = "target",
    layers: tuple[str, ...] = ("L3", "L4", "L5"),
    build_cache_dir: Path | None = None,
    source_abi_cache_dir: Path | None = None,
    exported_symbols: tuple[str, ...] = (),
    changed_paths: tuple[str, ...] = (),
    public_header_roots: tuple[str, ...] = (),
    defer_cleanup: list[Callable[[], None]] | None = None,
) -> BuildSourcePack | None:
    """Collect an in-memory pack from raw source-tree / build-info inputs.

    Resolves L3 build evidence (from ``build_info`` or an auto-discovered /
    queried compile DB), runs L4 source ABI replay over a source tree, folds both
    into an L5 graph summary, and returns an embeddable :class:`BuildSourcePack`
    (``root=""``). Returns ``None`` when no input produced any facts.

    ``base_build`` seeds the L3 evidence from an already-loaded pack (e.g. an
    explicit ``--build-info`` pack directory) so a raw ``--sources`` tree can
    replay L4 against it without re-resolving a compile DB.

    ``build_config_trusted_for_query`` must be true before a tree-local
    ``build.query`` command can run. CLI auto-discovered ``.abicheck.yml`` files
    live inside the supplied source tree and may be attacker-controlled, so they
    are not trusted for subprocess execution. (The abicheck-authored *inferred*
    cmake/bazel query is separate — it runs whenever ``--sources`` needs L3, since
    pointing abicheck at a source tree is itself the request to analyse it; see
    :func:`_resolve_compile_db`.) ``allow_build_query`` is accepted only for
    backward compatibility and is ignored — ``--allow-build-query`` is a
    deprecated no-op.

    ``layers`` selects which layers to collect (ADR-033 D2 CI modes): the
    ``build`` mode passes ``("L3",)`` to capture build context only, skipping the
    L4 source replay and L5 graph entirely. ``L5`` requires ``L4``.
    """
    cfg = build_config or BuildConfig()
    scope = effective_graph_scope(cfg.graph_detail, scope)
    merged = BuildEvidence()
    extractors: list[ExtractorRecord] = []
    # Cleanup thunks for temp build dirs (out-of-tree inferred cmake) that must
    # outlive L4 replay — clang runs with each compile unit's `directory` (the cmake
    # build dir) as cwd, so the dir can't be removed (nor its lock released) until
    # after replay. Invoked below once L3/L4/L5 are collected into in-memory
    # evidence. Each thunk removes its dir and releases the dir's exclusive lock.
    query_build_cleanups: list[Callable[[], None]] = []

    try:
        if base_build is not None:
            merged.merge(base_build)

        if merged.compile_units:
            compile_db = None  # already seeded from a build-info pack
        elif _maybe_collect_bazel_build_info(
            build_info, merged, extractors, sources, tuple(cfg.targets)
        ):
            # A pre-captured Bazel aquery/cquery jsonproto produces BuildEvidence
            # directly (no compile_commands.json to load) — ADR-037 D5 #5 sniffing.
            compile_db = None
        else:
            compile_db = _resolve_compile_db(
                build_info,
                sources,
                cfg,
                build_config_trusted_for_query,
                merged,
                extractors,
                cleanup=query_build_cleanups,
                compile_db_explicit=compile_db_explicit,
                allow_inferred_build_query=allow_inferred_build_query,
            )
        if compile_db is not None:
            _run_compile_db(compile_db, cfg.system, merged, extractors, build_cache_dir)

        # Opt-in; needs the *merged* compile-unit set (BuildEvidence.scan_comdat).
        if comdat_scan_requested():
            merged.scan_comdat()

        # A4: with both a --sources tree and L3 compile units, flag when the build
        # metadata describes a different checkout than the source tree (decoupled
        # inputs assembled from different trees). Collection-time diagnostic, not a
        # ChangeKind — collection has no findings list (cf. A2).
        _check_build_info_source_mismatch(merged, sources, extractors)

        surface = None
        call_graph_units: list[Any] | None = None
        if "L4" in layers:
            # A 'changed' scope with no PR diff would select zero TUs and embed an
            # empty L4 surface (Codex review), so fall back to a non-empty scope that
            # still enables the source-only checks. But when the caller *did* thread an
            # explicit changed-path set (PR replay, ADR-035 D7 POI focusing), honour
            # 'changed' so the scan narrows to the affected TUs.
            #
            # The unseeded fallback is 'headers-only' (the public-API-covering TU
            # subset), NOT 'target' (the whole target): an unseeded s5/pr run otherwise
            # silently pays full-target (== s6) replay cost — the ADR-035 P3 cliff
            # (found during a real-world UXL field run). 'headers-only' keeps a
            # non-empty public surface for the cross-checks at a fraction of the cost;
            # the caller (cli_scan) emits the advisory naming --since to focus further.
            replay_scope = (
                "headers-only" if (scope == "changed" and not changed_paths) else scope
            )
            # L4 per-TU cache dir: explicit arg wins, else the ABICHECK_L4_CACHE_DIR
            # env (the CI-friendly knob — point it at a restored cache directory).
            l4_cache_dir = source_abi_cache_dir
            if l4_cache_dir is None:
                env_dir = os.environ.get("ABICHECK_L4_CACHE_DIR")
                l4_cache_dir = Path(env_dir) if env_dir else None
            surface, l4_selected_units = _run_inline_source_abi(
                sources,
                merged,
                extractors,
                extractor=extractor,
                scope=replay_scope,
                clang_bin=clang_bin,
                exported_symbols=exported_symbols,
                source_abi_cache_dir=l4_cache_dir,
                changed_paths=changed_paths,
                public_header_roots=public_header_roots,
            )
            # Gap-1: on an unseeded headers-only replay, scope the L5 call-graph
            # pass to the *same* TU set L4 used instead of the whole compile DB.
            # (Seeded runs scope by changed_paths; full/target keep the broad pass.)
            #
            # Only narrow when L4 *actually* selected units. An empty set means L4
            # could not select (no --sources tree, no compile units, or no
            # extractor) — NOT "scope to zero" — so a build-info-only deep scan must
            # keep the broad call-graph pass over ``merged`` rather than silently
            # collecting zero call edges (Codex review).
            if (
                replay_scope == "headers-only"
                and not changed_paths
                and l4_selected_units
            ):
                call_graph_units = l4_selected_units
        # Fold a call graph (DECL_CALLS_DECL edges) into the L5 graph whenever L4 also
        # ran — i.e. a semantic source mode (source-*/graph-summary/graph-full), not
        # the structural-only graph-build (L3+L5, no L4). This is what makes the
        # decl-dependency cross-checks (public_to_internal_dependency, ADR-035 D4)
        # reachable from `scan --source-method s5`/`--depth graph`; best-effort and
        # gated on clang++ availability (ADR-035 D4 reviewer wiring request).
        with_call_graph = "L5" in layers and "L4" in layers
        graph = (
            _build_inline_graph(
                merged,
                surface,
                with_call_graph=with_call_graph,
                clang_bin=clang_bin,
                extractors=extractors,
                changed_paths=changed_paths,
                call_graph_units=call_graph_units,
            )
            if "L5" in layers
            else None
        )

    # Always hand off (or drain) the inferred-build-dir cleanup thunks — even if
    # _resolve_compile_db / _run_compile_db / L4 replay / L5 fold raised — so the
    # build dir and its lock never leak. With `defer_cleanup`, the caller's finally
    # owns them (it runs after the scan's later phases, e.g. S2 `clang -E`); without
    # it (e.g. `dump --sources`), drain immediately (CodeRabbit).
    finally:
        if defer_cleanup is not None:
            defer_cleanup.extend(query_build_cleanups)
        else:
            from .build_query import drain_build_dir_cleanups

            drain_build_dir_cleanups(query_build_cleanups)

    has_build = bool(
        merged.compile_units
        or merged.targets
        or merged.toolchains
        or merged.link_units
        or merged.build_options
    )
    # A3: a failed/blocked build query produces no facts but is still worth
    # surfacing — keep the (near-empty) pack so its `partial` L3 coverage row and
    # the build_query diagnostic reach `compare`, rather than dropping it as if
    # nothing was attempted (Codex).
    has_query_diag = any(
        e.name in _BUILD_QUERY_DIAG_NAMES and e.status in _BUILD_QUERY_DIAG_STATUSES
        for e in extractors
    )
    if not (has_build or surface is not None or graph is not None or has_query_diag):
        return None

    pack = BuildSourcePack.empty(
        Path(""),
        abicheck_version="",
        created_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    pack.manifest.extractors = extractors
    pack.manifest.inputs = {
        "sources": DEFAULT_REDACTION.path(str(sources)) if sources else None,
        "build_info": DEFAULT_REDACTION.path(str(build_info)) if build_info else None,
        "collected": "inline",
    }
    if has_build:
        pack.build_evidence = merged
    if surface is not None:
        pack.source_abi = surface
    if graph is not None:
        pack.source_graph = graph
    pack.manifest.coverage = build_inline_coverage(
        merged, has_build, surface, graph, extractors
    )
    return pack


# ── L3: compile-DB resolution ─────────────────────────────────────────────────


def _resolve_compile_db(
    build_info: Path | None,
    sources: Path | None,
    cfg: BuildConfig,
    build_config_trusted_for_query: bool,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    cleanup: list[Callable[[], None]] | None = None,
    compile_db_explicit: bool = False,
    allow_inferred_build_query: bool = True,
) -> Path | None:
    """Resolve the compile DB to feed L3 (zero-config; ADR-032 amended).

    Order: an explicit ``--build-info`` path (file or dir) → a trusted
    ``--config`` ``build.query`` command result → ``build.compile_db`` in the
    source tree → an auto-discovered ``compile_commands.json`` → the **inferred,
    abicheck-authored** build-system query (cmake/make/bazel). No
    ``--allow-build-query`` flag is required: providing ``--sources`` is the
    request to collect build evidence. The only command never auto-run is an
    arbitrary ``build.query`` string from an auto-discovered (untrusted)
    ``.abicheck.yml`` — that still needs an explicit ``--config``.
    """
    # Track whether the operator gave an EXPLICIT L3 input (--build-info or a
    # build.compile_db path) that yielded nothing. If so, the default inferred
    # query must not run: a cleaned/mistyped build-info path should surface, not
    # be masked by a fresh `cmake`/`bazel` query under different flags (review).
    explicit_input_missed = False
    if build_info is not None:
        found = _compile_db_at(build_info)
        if found is not None:
            return found
        merged.diagnostics.append(
            f"build-info {build_info}: no {_COMPILE_DB_NAME} found"
        )
        explicit_input_missed = True

    # build.query (ADR-032 D5 query_build_system): a tree-supplied command that
    # EMITS a compile DB / exports without a full build. Runs only when the config
    # came from an explicit operator-supplied path (build_config_trusted_for_query);
    # an auto-discovered .abicheck.yml is never trusted to execute. No
    # --allow-build-query flag is involved any more (it is a deprecated no-op).
    if cfg.query:
        if not build_config_trusted_for_query:
            extractors.append(
                ExtractorRecord(
                    name="build_query",
                    status="skipped",
                    detail=(
                        "build.query ignored from auto-discovered .abicheck.yml; "
                        "pass a trusted config with --config to permit queries"
                    ),
                )
            )
            # Untrusted query is never run — fall through to compile_db /
            # auto-discovery / the abicheck-authored inferred query below.
        else:
            # Trusted operator config (--config): run its query automatically. No
            # --allow-build-query flag is required any more — pointing abicheck at
            # sources *is* the request to collect build evidence (ADR-032 amended).
            queried = _run_build_query(cfg, sources, merged, extractors)
            if queried is not None:
                return queried
            # The operator supplied an explicit query and it failed / produced no
            # compile DB. Surface that — do NOT mask it by falling back to a
            # compile_db glob, a stale auto-discovered DB from a prior/default
            # configure, or abicheck's default inferred query, which would collect
            # L3 with the wrong flags the custom query existed to avoid (review).
            # The build_query diagnostic _run_build_query recorded explains the miss.
            return None

    if cfg.compile_db and sources is not None:
        # Only an *operator-supplied* build.compile_db (a `build_compile_db`
        # argument from a programmatic caller, or
        # an explicit --config path) counts as an explicit input whose miss should
        # suppress fallback — tracked by `compile_db_explicit`, which is distinct
        # from query-execution trust (review): a `build_compile_db` makes the DB
        # explicit without trusting a query, and a `build_query` trusts a query
        # without making a DB explicit. A build.compile_db from an auto-discovered
        # .abicheck.yml is not something the user chose, so a stale/cleaned path
        # there still falls through to the zero-config inferred query.
        if compile_db_explicit:
            explicit_input_missed = True
        for match in sorted(sources.glob(cfg.compile_db)):
            if match.is_file():
                return match

    if explicit_input_missed:
        # An explicit --build-info / build_compile_db / --config compile-DB input
        # was given but resolved to nothing. Surface that miss rather than masking
        # it with a stale auto-discovered DB OR abicheck's default inferred query
        # under different flags — checked BEFORE auto-discovery so a stray
        # build/compile_commands.json can't silently stand in (review).
        return None

    discovered = _autodiscover_compile_db(sources)
    if discovered is not None:
        return discovered

    if not allow_inferred_build_query:
        # An L2-only caller (--depth headers / collect_mode "off") reached the
        # zero-config fallback: it wants build-derived include dirs to parse headers,
        # but no evidence was requested, so we must not run a build system. Passive
        # discovery above is honoured; the inferred cmake/make/bazel query is not —
        # that would violate the L2-only depth contract and could spend up to the
        # inferred-query timeout evaluating build scripts (Codex review).
        merged.diagnostics.append(
            "inferred build-system query skipped: no evidence depth requested "
            "(L2-only); pass --build-info or generate a compile_commands.json to "
            "seed include dirs"
        )
        return None

    # Zero-config fallback: no compile DB exists and no explicit L3 input was
    # given, but a --sources tree is present. Detect the build system and run
    # abicheck's OWN fixed query (cmake configure / bazel aquery / make dry-run)
    # to produce L3 —
    # so "just provide sources" works with no flag and no manual build step. Only
    # an abicheck-authored command runs here; an arbitrary tree-local
    # .abicheck.yml `build.query` string is never auto-executed.
    from .build_query import run_inferred_build_query

    return run_inferred_build_query(
        sources, merged, extractors, cleanup=cleanup, bazel_targets=tuple(cfg.targets)
    )


def _compile_db_at(path: Path) -> Path | None:
    """Resolve a build-info input to a concrete ``compile_commands.json``.

    A directory is searched with the shared P4 strategy (hint dirs + any
    immediate subdirectory) so ``--build-info <dir>`` honours the same contract
    as ``--sources`` auto-discovery (Codex review).
    """
    if path.is_file():
        # An explicit --build-info file is honoured as the compile DB whatever
        # its name (the user pointed straight at it).
        return path
    if path.is_dir():
        return _find_compile_db_in_dir(path)
    return None


#: How many bytes to sniff from the head of a ``--build-info`` file when
#: classifying its format (ADR-037 D5 #5). Enough to see the top-level JSON
#: shape + the first discriminating key without reading a huge aquery dump.
_BUILD_INFO_SNIFF_BYTES = 65536


def sniff_build_info_format(path: Path) -> str:
    """Classify a ``--build-info`` path by content (ADR-037 D5 #5).

    Returns one of ``"pack"`` (a ``collect`` pack dir), ``"build_dir"`` (a
    directory to search for ``compile_commands.json``), ``"compile_db"`` (a
    Clang/CMake ``compile_commands.json`` — a JSON *array*), ``"bazel_aquery"`` /
    ``"bazel_cquery"`` (Bazel ``--output=jsonproto`` — a JSON *object* keyed by
    ``actions`` / ``results``), or ``"unknown"``. Lets a Bazel query result and a
    pack "just work" when passed to ``--build-info`` instead of being mis-parsed
    as a compile DB. The top-level shape is read from a bounded head (``[`` = a
    compile-DB array); a ``{`` object is fully parsed so a large aquery preamble
    can't hide the discriminating key (Codex review). Never executes anything.
    """
    if path.is_dir():
        return "pack" if is_pack_dir(path) else "build_dir"
    try:
        with open(path, "rb") as f:
            head = f.read(_BUILD_INFO_SNIFF_BYTES)
    except OSError:
        return "unknown"
    text = head.decode("utf-8", "replace").lstrip()
    if not text:
        return "unknown"
    if text[0] == "[":
        return "compile_db"  # compile_commands.json is a top-level JSON array
    if text[0] != "{":
        return "unknown"
    # A JSON object: a Bazel jsonproto (aquery→"actions", cquery→"results") or an
    # object-wrapped compile DB. The discriminating key can sit far past the sniff
    # window in a large aquery dump (long artifacts/pathFragments preamble), so
    # parse the whole object to classify by key, not a bounded prefix (Codex).
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        # Truncated / not-quite-JSON: fall back to the bounded-prefix heuristic.
        if '"actions"' in text:
            return "bazel_aquery"
        if '"results"' in text:
            return "bazel_cquery"
        return "unknown"
    if isinstance(data, dict):
        if "actions" in data:
            return "bazel_aquery"
        if "results" in data:
            return "bazel_cquery"
        if any(k in data for k in ("file", "command", "arguments")):
            return "compile_db"
    return "unknown"


def _maybe_collect_bazel_build_info(
    build_info: Path | None,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    sources: Path | None = None,
    configured_targets: tuple[str, ...] = (),
) -> bool:
    """Route a pre-captured Bazel aquery/cquery ``--build-info`` to the adapter.

    Returns ``True`` (and merges the normalized :class:`BuildEvidence` into
    *merged*) when *build_info* is a Bazel jsonproto file, else ``False`` so the
    caller falls back to compile-DB resolution. Pre-captured only — the adapter is
    constructed with ``allow_query=False`` so no ``bazel`` subprocess ever runs.

    *sources* (the caller's own ``--sources`` tree root, when given) is passed
    through as the adapter's ``workspace`` -- the same anchor
    :func:`~abicheck.buildsource.build_query.run_inferred_build_query`'s own
    Bazel path already supplies (Codex review, fresh evidence): without a
    workspace, a captured aquery's own relative exec paths (``bazel-out/.../
    libfoo.a``) leave both ``CompileUnit.directory`` and ``LinkUnit.directory``
    empty (the adapter deliberately refuses to persist a meaningless relative
    ``"."`` when no workspace is known -- see ``BazelAdapter._compile_unit``),
    so ``_default_archive_search_roots`` returns no roots at all and
    ``archive_graph``'s own pass reports every such static library as missing.
    ``None`` when the caller has no source tree (e.g. an out-of-tree
    ``--build-info`` with no ``--sources``) -- unchanged from before, since
    ``BazelAdapter``'s own ``workspace`` parameter already tolerates ``None``.
    """
    if build_info is None or not build_info.is_file():
        return False
    fmt = sniff_build_info_format(build_info)
    if fmt not in ("bazel_aquery", "bazel_cquery"):
        return False
    if configured_targets:  # ADR-063 Phase 4: no `targets` param on this adapter
        from ..errors import ValidationError

        raise ValidationError(
            f"build_targets={list(configured_targets)!r} requested, but --build-info is a pre-captured Bazel {fmt.removeprefix('bazel_')} jsonproto ({build_info}); root-target scoping only applies to a *live* `bazel query` (pass --sources/a workspace with no --build-info, or pre-capture the jsonproto already scoped to the desired targets first)"
        )
    from .adapters.bazel import BazelAdapter

    if fmt == "bazel_aquery":
        kind = "aquery"
        adapter = BazelAdapter(aquery=build_info, workspace=sources, allow_query=False)
    else:
        kind = "cquery"
        adapter = BazelAdapter(cquery=build_info, workspace=sources, allow_query=False)
    ev = adapter.collect()
    merged.merge(ev)
    extractors.append(
        ExtractorRecord(
            name="bazel",
            status="present" if ev.compile_units else "partial",
            detail=(
                f"pre-captured {kind} jsonproto from --build-info, "
                f"{len(ev.compile_units)} compile unit(s)"
            ),
        )
    )
    return True


def _find_compile_db_in_dir(
    directory: Path, skip_segments: frozenset[str] = frozenset()
) -> Path | None:
    """Locate a ``compile_commands.json`` under *directory* (the P4 strategy).

    Conventional build-dir hints first (fast, deterministic), then a fallback to
    *any* immediate subdirectory holding a compile DB — so a non-standard but
    common out-of-tree dir (``cmake-build-debug-gcc``, ``build-release``, an
    IDE/preset dir, …) is still found instead of silently yielding no L3
    evidence. The fallback stays at depth 1 to remain cheap and is deterministic
    (sorted). Shared by ``--sources`` auto-discovery and ``--build-info <dir>``
    resolution so both honour the same "any immediate subdirectory" contract.

    *skip_segments* names immediate subdirectories to ignore — used by
    auto-discovery to skip a stale ``.abicheck-build`` left by an older in-tree
    inferred-CMake run, so it can't short-circuit a fresh out-of-tree query with
    stale flags (Codex P2).
    """
    for hint in _COMPILE_DB_HINTS:
        if hint in skip_segments:
            continue
        candidate = (
            (directory / hint / _COMPILE_DB_NAME)
            if hint
            else (directory / _COMPILE_DB_NAME)
        )
        if candidate.is_file():
            return candidate
    fallback = sorted(
        p
        for p in directory.glob("*/" + _COMPILE_DB_NAME)
        if p.is_file() and p.parent.name not in skip_segments
    )
    return fallback[0] if fallback else None


def _autodiscover_compile_db(source_tree: Path | None) -> Path | None:
    """Best-effort search for a ``compile_commands.json`` inside a source tree.

    Skips a stale ``.abicheck-build/compile_commands.json`` (an older in-tree
    inferred-CMake artifact) so a zero-config ``--sources`` run refreshes the build
    query instead of replaying with stale flags/include paths (Codex P2).
    """
    if source_tree is None or not source_tree.is_dir():
        return None
    from .build_query import ABICHECK_BUILD_DIR

    return _find_compile_db_in_dir(
        source_tree, skip_segments=frozenset({ABICHECK_BUILD_DIR})
    )


def _run_compile_db(
    compile_db: Path,
    system: str,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    cache_dir: Path | None = None,
) -> None:
    """Normalize a compile DB into L3 build evidence (never raises).

    With ``cache_dir`` set, a content-addressed L3 cache (ADR-033 D5) skips the
    adapter when the same compile DB was normalized before (false-miss-preferring).
    """
    from .adapters import CompileDbAdapter

    hint = system if system in ("cmake", "ninja", "bazel", "make") else "generic"
    cache = None
    key = None
    if cache_dir is not None:
        from .build_cache import BuildEvidenceCache, compute_build_cache_key

        cache = BuildEvidenceCache(cache_dir)
        key = compute_build_cache_key(compile_db, hint)
        cached = cache.get(key)
        if cached is not None:
            merged.merge(cached)
            extractors.append(
                ExtractorRecord(
                    name="compile_commands",
                    status="ok",
                    inputs=[DEFAULT_REDACTION.path(str(compile_db))],
                    detail=f"{len(cached.compile_units)} compile units (cached)",
                )
            )
            return
    try:
        ev = CompileDbAdapter(compile_db, build_system=hint).collect()
    except (OSError, ValueError) as exc:
        extractors.append(
            ExtractorRecord(
                name="compile_commands",
                status="failed",
                inputs=[DEFAULT_REDACTION.path(str(compile_db))],
                detail=str(exc),
            )
        )
        merged.diagnostics.append(f"compile_commands: {exc}")
        return
    if cache is not None and key is not None:
        cache.put(key, ev)
    merged.merge(ev)
    extractors.append(
        ExtractorRecord(
            name="compile_commands",
            status="ok",
            inputs=[DEFAULT_REDACTION.path(str(compile_db))],
            detail=f"{len(ev.compile_units)} compile units",
        )
    )


def _run_build_query(
    cfg: BuildConfig,
    sources: Path | None,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
) -> Path | None:
    """Run the configured ``build.query`` command and return the emitted DB.

    Runs the explicit operator-configured command with ``shell=False`` (parsed
    via ``shlex``) in the source-tree cwd. This is the ADR-032 D5 ``query_build_system``
    tier: it emits flags/exports (a configured-graph/action query, ``make -n``,
    a CMake File API regeneration) — never ``cmake --build`` / ``make all``. A
    non-zero exit, missing tool, or timeout is recorded as a failed extractor and
    collection continues with whatever else is available (ADR-028 D3).
    """
    cwd = sources if sources is not None and sources.is_dir() else None
    try:
        argv = shlex.split(cfg.query)
    except ValueError as exc:
        extractors.append(
            ExtractorRecord(
                name="build_query",
                status="failed",
                detail=f"could not parse build.query command: {exc}",
            )
        )
        return None
    if not argv:
        return None
    scan_remaining = deadline.remaining()
    effective_timeout = (
        _QUERY_TIMEOUT_S
        if scan_remaining is None
        else min(_QUERY_TIMEOUT_S, scan_remaining)
    )
    try:
        # Bound by min(local 300s default, active scan --budget) —
        # run_bounded() alone would honor a generous outer deadline verbatim
        # instead of this query's own cap, letting a hung configured query
        # burn the whole remaining scan budget — and process-group-safe on
        # timeout. This operator-configured query runs inside
        # run_scan_core's L2-L5 deadline scope just like the zero-config
        # inferred query (Codex review, PR #591, round 8).
        with deadline.deadline_scope(effective_timeout):
            proc = deadline.run_bounded(  # noqa: S603 - operator-configured, shell=False, opt-in
                argv,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                timeout=_QUERY_TIMEOUT_S,
            )
    except deadline.DeadlineExceeded as exc:
        extractors.append(
            ExtractorRecord(
                name="build_query",
                status="failed",
                detail=f"build.query aborted: scan deadline exceeded ({exc})",
            )
        )
        merged.diagnostics.append(f"build_query: scan deadline exceeded ({exc})")
        return None
    except (OSError, subprocess.SubprocessError) as exc:
        extractors.append(
            ExtractorRecord(
                name="build_query",
                status="failed",
                detail=f"build.query failed to run ({argv[0]}): {exc}",
            )
        )
        merged.diagnostics.append(f"build_query: {exc}")
        return None
    if proc.returncode != 0:
        extractors.append(
            ExtractorRecord(
                name="build_query",
                status="failed",
                detail=f"build.query exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}",
            )
        )
        merged.diagnostics.append(f"build_query: command exited {proc.returncode}")
        return None
    # The query is expected to have written/refreshed the configured compile DB.
    db: Path | None = None
    if cfg.compile_db and sources is not None:
        # The operator told us exactly where this query writes its DB. Use only
        # that path: if the query exited 0 but didn't actually produce it, do NOT
        # fall back to an auto-discovered stale compile_commands.json — that would
        # collect L3 with the wrong (default) flags the custom query existed to
        # set, while reporting success (Codex P2). Surface the miss as partial.
        for match in sorted(sources.glob(cfg.compile_db)):
            if match.is_file():
                db = match
                break
    else:
        # No explicit path configured: discover the conventional compile DB the
        # query is expected to have refreshed.
        db = _autodiscover_compile_db(sources)
    extractors.append(
        ExtractorRecord(
            name="build_query",
            status="ok" if db is not None else "partial",
            detail=(
                f"ran `{argv[0]} …`; compile DB at {DEFAULT_REDACTION.path(str(db))}"
                if db is not None
                else f"ran `{argv[0]} …` but no compile DB was produced"
            ),
        )
    )
    return db


# ── L4: source ABI replay ─────────────────────────────────────────────────────


# A4 thresholds: fire only on a *strong* signal (almost no compile-DB source
# resolves under the tree) over a non-trivial number of units, so an unusual
# build layout is not mistaken for a wrong checkout.
_MISMATCH_MIN_UNITS = 3
_MISMATCH_THRESHOLD = 0.9


def _check_build_info_source_mismatch(
    merged: BuildEvidence,
    sources: Path | None,
    extractors: list[ExtractorRecord],
) -> None:
    """A4: record a diagnostic when the L3 compile units describe a different
    checkout than the ``--sources`` tree.

    Collection-time only: ``merge``/collection has no ``DiffResult`` list, so this
    is **not** a ``ChangeKind`` — it rides in the extractor ledger and
    ``BuildEvidence.diagnostics`` (the channels the later compare's coverage
    report surfaces), never as a verdict-bearing finding. Conservative by design
    (see thresholds) so it does not trip the FP-rate gate on unusual layouts.
    """
    if sources is None or not merged.compile_units:
        return
    tree = Path(sources)
    if not tree.is_dir():
        return

    # Match each compile-DB source against the tree by its *relative* path
    # (directory-prefix-stripped, forward-slash normalized), falling back to the
    # basename only when the source is not under its own compile-DB directory.
    # All comparison is string-based on precomputed posix paths — no filesystem
    # resolution — so it is robust to platform separators/drives (Windows CI) and
    # to redacted home prefixes (`~/proj/...`), while still distinguishing two
    # different checkouts that merely share filenames (review).
    tree_rel: set[str] = set()
    tree_names: set[str] = set()
    # Two-component suffixes (`parent/name`) of every tree file, so an
    # absolute/redacted compile-DB source can be matched on more than its bare
    # basename — a wrong checkout that ships `tests/foo.cpp` must not satisfy a
    # compile unit whose source is `src/foo.cpp` (review).
    tree_tail2: set[str] = set()
    for root, _dirs, files in os.walk(tree):
        for fn in files:
            rel = (Path(root) / fn).relative_to(tree).as_posix()
            tree_rel.add(rel)
            tree_names.add(fn)
            parts = rel.split("/")
            if len(parts) >= 2:
                tree_tail2.add("/".join(parts[-2:]))

    def _present(cu: object) -> bool | None:
        src = getattr(cu, "source", "")
        if not src:
            return None
        posix = str(src).replace("\\", "/")
        name = PurePosixPath(posix).name
        directory = (
            str(getattr(cu, "directory", "") or "").replace("\\", "/").rstrip("/")
        )
        if directory and posix.startswith(directory + "/"):
            return posix[len(directory) + 1 :] in tree_rel
        # A genuinely relative source (not rooted at "/", a drive "X:", or a
        # redacted home "~") can be matched against the tree's relative paths.
        rooted = (
            posix.startswith("/")
            or posix.startswith("~")
            or (len(posix) >= 2 and posix[1] == ":")
        )
        if not rooted:
            return posix in tree_rel
        # Absolute / redacted with an unknown root → the redacted/abs prefix is
        # unrecoverable, but require the source's `parent/name` suffix to exist in
        # the tree rather than its basename alone, so a same-named file in a
        # different subtree does not mask a checkout mismatch. Sources with no
        # parent component fall back to the basename.
        parts = [p for p in posix.split("/") if p and p != "~"]
        if len(parts) >= 2:
            return "/".join(parts[-2:]) in tree_tail2
        return name in tree_names

    flags = [r for r in (_present(cu) for cu in merged.compile_units) if r is not None]
    if len(flags) < _MISMATCH_MIN_UNITS:
        return
    missing = sum(1 for present in flags if not present)
    if missing / len(flags) >= _MISMATCH_THRESHOLD:
        detail = (
            f"{missing}/{len(flags)} compile-DB source files are absent from the "
            "--sources tree; build metadata and sources may be different checkouts"
        )
        extractors.append(
            ExtractorRecord(
                name="build_info_source_tree_mismatch", status="failed", detail=detail
            )
        )
        merged.diagnostics.append(f"build_info/source mismatch: {detail}")


def _run_inline_source_abi(
    sources: Path | None,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    *,
    extractor: str,
    scope: str,
    clang_bin: str,
    exported_symbols: tuple[str, ...] = (),
    source_abi_cache_dir: Path | None = None,
    changed_paths: tuple[str, ...] = (),
    public_header_roots: tuple[str, ...] = (),
) -> tuple[SourceAbiSurface | None, list[Any]]:
    """Run L4 replay over a source tree; ``(None, [])`` when no source tree given.

    Returns ``(surface, selected_units)`` — the L4 surface plus the exact
    compile-unit set the replay scope selected, so the L5 call-graph pass can match
    that scope on an unseeded run (Gap-1 fix) instead of re-parsing all TUs.

    Requires L3 compile units to replay against (ADR-030 D5). A missing source
    extractor (clang/castxml) yields a partial surface and a clear note rather
    than aborting — the artifact tiers stay authoritative (ADR-028 D3).

    ``extractor == "hybrid"`` is likewise recorded as skipped rather than run:
    L4 source-ABI replay has only ever had ONE extractor implementation per
    TU (``_make_source_extractor`` special-cases "castxml", else clang) —
    there is no dual-backend merge here the way ``dumper_hybrid.py`` provides
    for the L2 header-AST snapshot. ``--ast-frontend hybrid`` reaches this
    function unchanged (it is the shared ``compile_context_options`` flag,
    passed straight through as ``extractor`` by ``dump_source_only`` — see
    `cli.py`), so treating it like any other extractor name would silently
    run clang alone while recording ``source_abi:hybrid`` as if both
    backends had (Codex review).
    """
    if sources is None:
        return None, []
    from .source_abi import SourceAbiSurface
    from .source_replay import (
        SourceAbiCache,
        public_header_roots_for,
        run_source_replay,
    )

    if extractor == "hybrid":
        extractors.append(
            ExtractorRecord(
                name="source_abi:hybrid",
                status="skipped",
                detail=(
                    "L4 source-ABI replay has no dual-backend hybrid extractor "
                    "(unlike the L2 header-AST snapshot); pass "
                    "--ast-frontend castxml or --ast-frontend clang for a "
                    "--sources/--build-info dump"
                ),
            )
        )
        return None, []

    if not merged.compile_units:
        # No L3 to replay against: source ABI replay needs compile commands to
        # know how each TU is parsed. Record why, but do not synthesize an empty
        # L4 surface — otherwise a bare tree with no build info would embed an
        # all-empty pack. With no other facts the caller drops the pack entirely.
        extractors.append(
            ExtractorRecord(
                name=f"source_abi:{extractor}",
                status="skipped",
                detail=(
                    "no compile units (L3) to replay; pass --build-info or add a "
                    "compile_commands.json to the source tree"
                ),
            )
        )
        return None, []

    impl, tool_name = _make_source_extractor(extractor, clang_bin)
    if not impl.available():
        extractors.append(
            ExtractorRecord(
                name=f"source_abi:{extractor}",
                status="failed",
                detail=f"{tool_name} not found in PATH; source-only checks disabled",
            )
        )
        return SourceAbiSurface(), []

    roots = sorted(set(public_header_roots_for(merged)) | set(public_header_roots))
    include_map = _include_map_for_replay(
        merged,
        scope=scope,
        roots=tuple(roots),
        clang_bin=clang_bin,
        extractors=extractors,
    )
    # The exact compile-unit set this replay scope selects (pure, reuses the
    # already-computed include graph — no extra clang pass). Returned so the L5
    # call-graph pass can match the L4 scope for an unseeded run (Gap-1 fix) rather
    # than re-parsing the whole compile DB.
    from .source_replay import select_compile_units

    selected_units = select_compile_units(
        merged,
        scope=scope,
        changed_paths=changed_paths,
        include_map=include_map,
        public_header_roots=roots,
    )
    # D8 per-TU cache: re-extracting every TU on every `dump --sources` is the
    # cold-start cost (eval E4: zstd 48.6 s cold → 3.4 s warm). Wire the cache
    # when a dir is given (CLI/env), so a persisted dir restored across CI runs
    # makes each run start warm. Absent a dir, behaviour is unchanged (no cache).
    cache = SourceAbiCache(source_abi_cache_dir) if source_abi_cache_dir else None
    started = time.monotonic()
    surface, diagnostics = run_source_replay(
        merged,
        impl,
        scope=scope,
        changed_paths=changed_paths,
        public_header_roots=roots,
        exported_symbols=exported_symbols,
        cache=cache,
        include_map=include_map,
    )
    elapsed = time.monotonic() - started
    if surface is not None:
        surface.coverage.setdefault("elapsed_s", round(elapsed, 3))
    if cache is not None:
        rate = cache.hit_rate
        if rate is not None:
            merged.diagnostics.append(
                f"source_abi: L4 cache hit rate {rate:.0%} "
                f"({cache.hits}/{cache.hits + cache.misses})"
            )
        # Thread the cache stats into the surface so the live L4 coverage row can
        # report them too (ADR-035 P5) — not only `scan --estimate` (which probes
        # the cache up front). `build_inline_coverage` reads these keys.
        if surface is not None:
            surface.coverage["cache_hits"] = cache.hits
            surface.coverage["cache_misses"] = cache.misses
    for diag in diagnostics:
        merged.diagnostics.append(f"source_abi: {diag}")
    parsed = int(surface.coverage.get("compile_units_parsed", 0) or 0)
    selected = int(surface.coverage.get("compile_units_selected", 0) or 0)
    extra = f", {elapsed:.2f}s"
    if surface.coverage.get("scope_widened_to_full"):
        extra += ", widened-to-full"
    extractors.append(
        ExtractorRecord(
            name=f"source_abi:{extractor}",
            status="ok" if parsed else "partial",
            detail=(
                f"scope={scope}, {parsed}/{selected} TUs parsed, "
                f"{len(diagnostics)} failures{extra}"
            ),
        )
    )
    return surface, selected_units


def _include_map_for_replay(
    build: BuildEvidence,
    *,
    scope: str,
    roots: tuple[str, ...],
    clang_bin: str,
    extractors: list[ExtractorRecord],
) -> dict[str, list[str]]:
    """Best-effort include map for narrowing L4 replay.

    ``headers-only`` can shrink from all TUs to the TUs that include public
    headers, but only when it has an exact textual include graph. Recorded action
    inputs are an over-approximation, so headers-only replay uses a cheap depfile
    pass instead. Failure keeps the old fail-open selector, never drops evidence.
    """
    if scope != "headers-only" or not roots or not build.compile_units:
        return {}
    from .include_graph import ClangIncludeExtractor

    extractor = ClangIncludeExtractor(
        clang_bin=clang_bin if clang_bin != "clang" else "clang++"
    )
    include_map = extractor.extract_from_build(build)
    status = "ok" if include_map else "skipped"
    detail = f"{len(include_map)}/{len(build.compile_units)} compile units"
    if extractor.diagnostics:
        status = "partial" if include_map else "failed"
        detail += "; " + "; ".join(extractor.diagnostics[:3])
    extractors.append(
        ExtractorRecord(name="include_graph:clang", status=status, detail=detail)
    )
    return include_map


def _make_source_extractor(
    extractor: str, clang_bin: str
) -> tuple[SourceAbiExtractor, str]:
    if extractor == "castxml":
        from .source_extractors import CastxmlSourceExtractor

        return CastxmlSourceExtractor(), "castxml"
    from .source_extractors import ClangSourceExtractor

    # pick_compiler_binary() only consults compiler_binary for CL-vs-GNU mode
    # detection, not clang_bin (the binary to invoke) -- forward an explicit
    # --compiler override (e.g. dpcpp-cl) so mode detection sees it too,
    # instead of silently falling back to each CompileUnit's own argv[0]
    # (Codex review). Skip it for the generic "clang" default so
    # auto-detection from a mixed-toolchain compile database still works.
    compiler_binary = clang_bin if clang_bin != "clang" else None
    return (
        ClangSourceExtractor(clang_bin=clang_bin, compiler_binary=compiler_binary),
        clang_bin,
    )


# ── L5: source graph ──────────────────────────────────────────────────────────


def _build_inline_graph(
    merged: BuildEvidence,
    surface: SourceAbiSurface | None,
    *,
    with_call_graph: bool = False,
    clang_bin: str = "clang",
    extractors: list[ExtractorRecord] | None = None,
    changed_paths: tuple[str, ...] = (),
    call_graph_units: list[Any] | None = None,
) -> SourceGraphSummary | None:
    """Fold L3 + optional L4 into the compact L5 source graph (always when L3).

    Per the amendment D2 the graph is built whenever a source surface or build
    evidence exists — it is compact by design (ADR-031 D7), so there is no
    separate opt-in flag.

    When ``with_call_graph`` is set, :func:`inline_graph_fold.fold_semantic_graphs`
    folds the Clang call/type/override/template/include-graph edges into the
    graph (best-effort throughout — see its own docstring for the edge kinds
    and scoping precedence, including the template-instantiation pass, G29
    Phase 5 item 1, see ``template_graph.py``); gated to the semantic L4
    modes by the caller, with no separate opt-in flag (ADR-041
    header-only-graph addendum follow-up: these used to be ``collect``-only,
    explicit-flag-gated passes with no equivalent here at all).
    ``fold_archive_graph`` (G29 Phase 5 item 6) needs no clang and always
    runs, independent of ``with_call_graph``.
    """
    # link_units too: build_source_graph() folds them regardless, so a link-only input must not return None first (Codex).
    has_build = bool(merged.compile_units or merged.targets or merged.link_units)
    if not has_build and surface is None:
        return None
    from .source_graph_build import build_source_graph

    graph = build_source_graph(merged, source_abi=surface)
    if with_call_graph:
        from .inline_graph_fold import fold_semantic_graphs

        # NOTE: this always runs the replay passes even when `surface`'s
        # source_edges are already confirmed complete (build_source_graph()
        # above already folded those in via fold_source_edges) -- an earlier
        # revision skipped the replay in that case, but the raw source_edges
        # wire format carries only bare endpoint identities, not the
        # dst_file/project-file provenance fold_call_graph/fold_type_graph
        # attach via `project_files` (`defined_in_project`). Without that
        # provenance, `crosscheck.public_to_internal_dependency` cannot
        # classify an unannotated callee/referenced node as internal, so a
        # public-to-internal dependency addition would silently go
        # undetected (Codex review). The replay stays unconditional until
        # source_edges carries equivalent provenance end-to-end.
        fold_semantic_graphs(
            graph,
            merged,
            clang_bin,
            extractors,
            changed_paths,
            scoped_units=call_graph_units,
        )
    from .inline_graph_fold import fold_archive_graph

    fold_archive_graph(graph, merged, extractors)
    graph.finalize()
    return graph


# ── coverage rows ─────────────────────────────────────────────────────────────


def _l4_coverage_detail(surface: SourceAbiSurface) -> str:
    """A human L4 coverage detail from the surface's recorded counts (ADR-035 P5).

    The live row was previously blank — only ``scan --estimate`` reported TU
    counts. Mirror that here: replay scope, parsed/selected TUs, matched/exported
    symbols, and (when an L4 cache ran) its hit/miss tally.
    """
    cov = surface.coverage
    scope = cov.get("replay_scope")
    parts: list[str] = []
    if scope:
        parts.append(f"scope={scope}")
    selected = cov.get("compile_units_selected")
    parsed = cov.get("compile_units_parsed")
    if selected is not None or parsed is not None:
        parts.append(f"{int(parsed or 0)}/{int(selected or 0)} TUs parsed")
    matched = cov.get("matched_symbols")
    exported = cov.get("exported_symbols")
    if matched is not None or exported is not None:
        m = int(matched or 0)
        e = int(exported or 0)
        parts.append(f"{m}/{e} symbols matched")
        # A bare "matched/exported" ratio reads like a coverage gap: for a real
        # C++ library most exports are RTTI/vtable/thunk (synthesized) or
        # stdlib/internal (classified), not direct decl matches, so "matched"
        # alone can look ~50% while every symbol is in fact accounted for.
        # Surface the full accounting so 100% coverage is visible, not hidden.
        attributed = (
            int(cov.get("synthesized_symbols_matched", 0) or 0)
            + int(cov.get("template_instantiation_symbols_matched", 0) or 0)
            + int(cov.get("allocator_interposer_symbols_matched", 0) or 0)
            + int(cov.get("non_public_symbols_classified", 0) or 0)
        )
        unmatched = cov.get("unmatched_symbols")
        if attributed or unmatched is not None:
            accounted = m + attributed
            u = int(unmatched if unmatched is not None else max(e - accounted, 0))
            parts.append(f"{accounted}/{e} accounted, {u} unmatched")
    if "cache_hits" in cov or "cache_misses" in cov:
        hits = int(cov.get("cache_hits", 0) or 0)
        misses = int(cov.get("cache_misses", 0) or 0)
        total = hits + misses
        if total:
            parts.append(f"cache {hits}/{total} hit ({hits / total:.0%})")
    if cov.get("scope_widened_to_full"):
        parts.append("headers-only widened to full")
    uncovered = int(cov.get("public_headers_uncovered", 0) or 0)
    if uncovered:
        parts.append(f"{uncovered} public header(s) not reached by include graph")
    elapsed = cov.get("elapsed_s")
    if elapsed is not None:
        parts.append(f"{float(elapsed):.2f}s")
    failures = int(cov.get("extractor_failures", 0) or 0)
    if failures:
        parts.append(f"{failures} extractor failures")
    return ", ".join(parts)


def build_inline_coverage(
    merged: BuildEvidence,
    has_build: bool,
    surface: SourceAbiSurface | None,
    graph: SourceGraphSummary | None,
    extractors: list[ExtractorRecord] | tuple[ExtractorRecord, ...] = (),
) -> list[LayerCoverage]:
    """Build L3/L4/L5 coverage rows for an inline-collected pack (ADR-028 D7)."""
    if has_build:
        systems = sorted({g.kind for g in merged.generators}) or ["generic"]
        p02 = l3_coverage_fields(merged)  # P0.2 root-target-scoping fields
        detail = (
            f"{'+'.join(systems)}, {len(merged.compile_units)} compile units, "
            f"{len(merged.targets)} targets" + p02.pop("detail_suffix")
        )
        l3 = LayerCoverage(
            layer=DataLayer.L3_BUILD.value,
            status=CoverageStatus.PRESENT,
            confidence=LayerConfidence.HIGH
            if merged.targets
            else LayerConfidence.REDUCED,
            detail=detail,
            **p02,
        )
    else:
        # A3: a build query that was attempted but failed (or was blocked because
        # --allow-build-query was not set) yielded no L3 facts. Surface that as a
        # `partial` row with the reason instead of a silent `not_collected`, so
        # the coverage/capability report tells the user exactly what to fix.
        bq = next(
            (
                e
                for e in extractors
                if e.name in _BUILD_QUERY_DIAG_NAMES
                and e.status in _BUILD_QUERY_DIAG_STATUSES
            ),
            None,
        )
        if bq is not None:
            l3 = LayerCoverage(
                layer=DataLayer.L3_BUILD.value,
                status=CoverageStatus.PARTIAL,
                confidence=LayerConfidence.UNKNOWN,
                detail=f"build query {bq.status}: {bq.detail}",
            )
        else:
            l3 = LayerCoverage(
                layer=DataLayer.L3_BUILD.value, status=CoverageStatus.NOT_COLLECTED
            )

    if surface is not None:
        any_entities = bool(
            surface.reachable_declarations
            or surface.reachable_types
            or surface.reachable_macros
            or surface.reachable_templates
            or surface.reachable_inline_bodies
        )
        cov = surface.coverage or {}
        exported = int(cov.get("exported_symbols", 0) or 0)
        matched = int(cov.get("matched_symbols", 0) or 0)
        zero_match_degraded = exported > 0 and matched == 0
        l4 = LayerCoverage(
            layer=DataLayer.L4_SOURCE_ABI.value,
            status=CoverageStatus.PRESENT
            if any_entities and not zero_match_degraded
            else CoverageStatus.PARTIAL,
            confidence=LayerConfidence.HIGH
            if any_entities and not zero_match_degraded
            else LayerConfidence.REDUCED,
            detail=_l4_coverage_detail(surface),
            elapsed_s=float(cov.get("elapsed_s", 0.0) or 0.0),
        )
    else:
        l4 = LayerCoverage(
            layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.NOT_COLLECTED
        )

    if graph is not None:
        # AC-006: a degraded call/type pass folded structural/plugin edges but the
        # live replay it stands in for never completed (`degraded_passes`, set by
        # `mark_source_edges_extractor_coverage` and the scoped-graph fold). Those
        # edges make `graph.edges` non-empty, which must NOT let L5 read as a full
        # `present` graph — the failed pass would be silently hidden. Downgrade to
        # `partial` whenever any pass is degraded, and name the passes so the
        # report says which live walk is missing.
        degraded = sorted(k for k, v in graph.degraded_passes.items() if v)
        l5_present = bool(graph.edges) and not degraded
        l5 = LayerCoverage(
            layer=DataLayer.L5_SOURCE_GRAPH.value,
            status=CoverageStatus.PRESENT if l5_present else CoverageStatus.PARTIAL,
            confidence=LayerConfidence.REDUCED
            if l5_present
            else LayerConfidence.UNKNOWN,
            detail=(
                "degraded passes (structural/plugin edges only, live replay "
                f"incomplete): {', '.join(degraded)}"
                if degraded
                else ""
            ),
        )
    else:
        l5 = LayerCoverage(
            layer=DataLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.NOT_COLLECTED
        )
    return [l3, l4, l5]


def __getattr__(name: str) -> object:
    """Lazily re-export the L2 include-seeding helpers from :mod:`l2_seed`.

    ``derive_l2_include_dirs`` / ``seed_l2_includes`` were split into a sibling
    module to keep this file under the size cap, but they have historically been
    imported from ``inline`` (the CLI callers and tests use that path). Resolving
    them here via ``importlib`` on attribute access preserves those import paths
    without a static ``inline`` -> ``l2_seed`` import edge, which would re-create
    the import cycle the split avoids (l2_seed imports collect_inline_pack etc.
    from here). See ADR-037 D10.1's cli_buildsource shim for the same pattern.
    """
    if name in ("derive_l2_include_dirs", "seed_l2_includes"):
        import importlib

        return getattr(importlib.import_module(".l2_seed", __package__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
