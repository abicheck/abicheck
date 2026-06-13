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
attaching a prebuilt pack produced by ``abicheck collect``, ``dump`` collects
the normalized L3/L4/L5 facts *inline* from raw inputs and embeds them in the
``.abi.json``:

- ``--sources <tree>`` — a source checkout (e.g. at the build tag). Runs L4
  source ABI replay and the L5 source graph summary internally.
- ``--build-info <path>`` — an optional build dir / ``compile_commands.json`` /
  pre-captured build-evidence pack supplying L3 build context. When omitted, a
  ``compile_commands.json`` inside the source tree is auto-discovered.

A per-project ``.abicheck.yml`` ``build:`` block can name the build system and a
*query* command that emits a compile DB without performing a full build; running
that query is gated by ``--allow-build-query`` (ADR-032 D5 ``query_build_system``
action ceiling — read by default, query opt-in, full build never).

Everything here is best-effort (ADR-028 D3): a missing tool or unreadable input
degrades L3/L4/L5 to partial/not-collected coverage and never aborts the dump —
the artifact tiers (L0/L1/L2) stay authoritative.
"""
from __future__ import annotations

import datetime as _dt
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from .build_evidence import BuildEvidence
from .model import (
    CoverageStatus,
    DataLayer,
    ExtractorRecord,
    LayerConfidence,
    LayerCoverage,
)
from .pack import BuildSourcePack
from .redaction import DEFAULT_REDACTION

if TYPE_CHECKING:
    from .source_abi import SourceAbiSurface
    from .source_extractors import SourceAbiExtractor
    from .source_graph import SourceGraphSummary

#: Default places to look for a compile DB inside a source checkout, in order.
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


@dataclass
class BuildConfig:
    """Parsed ``.abicheck.yml`` ``build:`` + ``sources:`` block (amendment D4).

    All fields are optional; an absent file yields the all-defaults config and
    inline collection falls back to auto-detection. ``system`` is advisory (it
    selects the compile-DB adapter hint); ``query`` is the *query/extraction*
    command run only under ``--allow-build-query``; ``compile_db`` is where that
    query (or the build) lands its ``compile_commands.json``.
    """

    system: str = "auto"
    query: str = ""
    compile_db: str = ""
    public_headers: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> BuildConfig:
        build = data.get("build") if isinstance(data, dict) else None
        build = build if isinstance(build, dict) else {}
        sources = data.get("sources") if isinstance(data, dict) else None
        sources = sources if isinstance(sources, dict) else {}

        def _str(d: dict[str, object], key: str, default: str = "") -> str:
            v = d.get(key)
            return v if isinstance(v, str) else default

        def _strs(d: dict[str, object], key: str) -> list[str]:
            v = d.get(key)
            if isinstance(v, list):
                return [str(x) for x in v]
            if isinstance(v, str):
                return [v]
            return []

        return cls(
            system=_str(build, "system", "auto") or "auto",
            query=_str(build, "query"),
            compile_db=_str(build, "compile_db"),
            public_headers=_strs(sources, "public_headers"),
            exclude=_strs(sources, "exclude"),
        )


def load_build_config(path: Path) -> BuildConfig:
    """Load a ``.abicheck.yml`` build config; tolerant of a missing/empty file."""
    if not path.is_file():
        return BuildConfig()
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"cannot read build config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        return BuildConfig()
    return BuildConfig.from_dict(raw)


def discover_build_config(source_tree: Path | None) -> Path | None:
    """Return the ``.abicheck.yml`` at the source-tree root, if present."""
    if source_tree is None or not source_tree.is_dir():
        return None
    candidate = source_tree / ".abicheck.yml"
    return candidate if candidate.is_file() else None


def is_pack_dir(path: Path | None) -> bool:
    """True when *path* is a pack directory produced by ``abicheck collect``."""
    return path is not None and path.is_dir() and (path / "manifest.json").is_file()


def collect_inline_pack(
    *,
    sources: Path | None,
    build_info: Path | None,
    build_config: BuildConfig | None = None,
    allow_build_query: bool = False,
    base_build: BuildEvidence | None = None,
    clang_bin: str = "clang",
    extractor: str = "clang",
    scope: str = "target",
    layers: tuple[str, ...] = ("L3", "L4", "L5"),
    build_cache_dir: Path | None = None,
    exported_symbols: tuple[str, ...] = (),
) -> BuildSourcePack | None:
    """Collect an in-memory pack from raw source-tree / build-info inputs.

    Resolves L3 build evidence (from ``build_info`` or an auto-discovered /
    queried compile DB), runs L4 source ABI replay over a source tree, folds both
    into an L5 graph summary, and returns an embeddable :class:`BuildSourcePack`
    (``root=""``). Returns ``None`` when no input produced any facts.

    ``base_build`` seeds the L3 evidence from an already-loaded pack (e.g. an
    explicit ``--build-info`` pack directory) so a raw ``--sources`` tree can
    replay L4 against it without re-resolving a compile DB.

    ``layers`` selects which layers to collect (ADR-033 D2 CI modes): the
    ``build`` mode passes ``("L3",)`` to capture build context only, skipping the
    L4 source replay and L5 graph entirely. ``L5`` requires ``L4``.
    """
    cfg = build_config or BuildConfig()
    merged = BuildEvidence()
    extractors: list[ExtractorRecord] = []

    if base_build is not None:
        merged.merge(base_build)

    if merged.compile_units:
        compile_db = None  # already seeded from a build-info pack
    else:
        compile_db = _resolve_compile_db(
            build_info, sources, cfg, allow_build_query, merged, extractors
        )
    if compile_db is not None:
        _run_compile_db(compile_db, cfg.system, merged, extractors, build_cache_dir)

    # A4: with both a --sources tree and L3 compile units, flag when the build
    # metadata describes a different checkout than the source tree (decoupled
    # inputs assembled from different trees). Collection-time diagnostic, not a
    # ChangeKind — collection has no findings list (cf. A2).
    _check_build_info_source_mismatch(merged, sources, extractors)

    surface = None
    if "L4" in layers:
        # Inline dump has no PR diff, so a 'changed' scope would select zero TUs
        # and embed an empty L4 surface (Codex review). Fall back to 'target' —
        # the non-empty choice that still enables the source-only checks the
        # 'source-changed' mode is meant to turn on. The changed-only narrowing
        # applies when a caller threads an explicit changed-path set (PR replay).
        replay_scope = "target" if scope == "changed" else scope
        surface = _run_inline_source_abi(
            sources, merged, extractors,
            extractor=extractor, scope=replay_scope, clang_bin=clang_bin,
            exported_symbols=exported_symbols,
        )
    graph = _build_inline_graph(merged, surface) if "L5" in layers else None

    has_build = bool(
        merged.compile_units or merged.targets or merged.toolchains
        or merged.link_units or merged.build_options
    )
    # A3: a failed/blocked build query produces no facts but is still worth
    # surfacing — keep the (near-empty) pack so its `partial` L3 coverage row and
    # the build_query diagnostic reach `compare`, rather than dropping it as if
    # nothing was attempted (Codex).
    has_query_diag = any(
        e.name == "build_query" and e.status in _BUILD_QUERY_DIAG_STATUSES
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
    allow_build_query: bool,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
) -> Path | None:
    """Resolve the compile DB to feed L3, honouring the action ceiling (D5).

    Order: an explicit ``--build-info`` path (file or dir) → a ``build.query``
    command result (only with ``--allow-build-query``) → ``build.compile_db`` in
    the source tree → an auto-discovered ``compile_commands.json`` in the tree.
    """
    if build_info is not None:
        found = _compile_db_at(build_info)
        if found is not None:
            return found
        merged.diagnostics.append(
            f"build-info {build_info}: no {_COMPILE_DB_NAME} found"
        )

    # build.query (ADR-032 D5 query_build_system): opt-in command that EMITS a
    # compile DB / exports without a full build. Off unless --allow-build-query.
    if cfg.query:
        if allow_build_query:
            queried = _run_build_query(cfg, sources, merged, extractors)
            if queried is not None:
                return queried
        else:
            extractors.append(ExtractorRecord(
                name="build_query", status="skipped",
                detail=(
                    "build.query configured but --allow-build-query not set; "
                    "only existing build outputs were inspected (ADR-032 D5)"
                ),
            ))

    if cfg.compile_db and sources is not None:
        for match in sorted(sources.glob(cfg.compile_db)):
            if match.is_file():
                return match

    return _autodiscover_compile_db(sources)


def _compile_db_at(path: Path) -> Path | None:
    """Resolve a build-info input to a concrete ``compile_commands.json``."""
    if path.is_file():
        return path if path.name == _COMPILE_DB_NAME else path
    if path.is_dir():
        for hint in _COMPILE_DB_HINTS:
            candidate = (path / hint / _COMPILE_DB_NAME) if hint else (path / _COMPILE_DB_NAME)
            if candidate.is_file():
                return candidate
    return None


def _autodiscover_compile_db(source_tree: Path | None) -> Path | None:
    """Best-effort search for a ``compile_commands.json`` inside a source tree."""
    if source_tree is None or not source_tree.is_dir():
        return None
    for hint in _COMPILE_DB_HINTS:
        candidate = (
            (source_tree / hint / _COMPILE_DB_NAME) if hint
            else (source_tree / _COMPILE_DB_NAME)
        )
        if candidate.is_file():
            return candidate
    return None


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
            extractors.append(ExtractorRecord(
                name="compile_commands", status="ok",
                inputs=[DEFAULT_REDACTION.path(str(compile_db))],
                detail=f"{len(cached.compile_units)} compile units (cached)",
            ))
            return
    try:
        ev = CompileDbAdapter(compile_db, build_system=hint).collect()
    except (OSError, ValueError) as exc:
        extractors.append(ExtractorRecord(
            name="compile_commands", status="failed",
            inputs=[DEFAULT_REDACTION.path(str(compile_db))], detail=str(exc),
        ))
        merged.diagnostics.append(f"compile_commands: {exc}")
        return
    if cache is not None and key is not None:
        cache.put(key, ev)
    merged.merge(ev)
    extractors.append(ExtractorRecord(
        name="compile_commands", status="ok",
        inputs=[DEFAULT_REDACTION.path(str(compile_db))],
        detail=f"{len(ev.compile_units)} compile units",
    ))


def _run_build_query(
    cfg: BuildConfig,
    sources: Path | None,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
) -> Path | None:
    """Run the configured ``build.query`` command and return the emitted DB.

    Runs the operator-configured command with ``shell=False`` (parsed via
    ``shlex``) in the source-tree cwd. This is the ADR-032 D5 ``query_build_system``
    tier: it emits flags/exports (a configured-graph/action query, ``make -n``,
    a CMake File API regeneration) — never ``cmake --build`` / ``make all``. A
    non-zero exit, missing tool, or timeout is recorded as a failed extractor and
    collection continues with whatever else is available (ADR-028 D3).
    """
    cwd = sources if sources is not None and sources.is_dir() else None
    try:
        argv = shlex.split(cfg.query)
    except ValueError as exc:
        extractors.append(ExtractorRecord(
            name="build_query", status="failed",
            detail=f"could not parse build.query command: {exc}",
        ))
        return None
    if not argv:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 - operator-configured, shell=False, opt-in
            argv, cwd=str(cwd) if cwd else None, capture_output=True,
            text=True, timeout=_QUERY_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        extractors.append(ExtractorRecord(
            name="build_query", status="failed",
            detail=f"build.query failed to run ({argv[0]}): {exc}",
        ))
        merged.diagnostics.append(f"build_query: {exc}")
        return None
    if proc.returncode != 0:
        extractors.append(ExtractorRecord(
            name="build_query", status="failed",
            detail=f"build.query exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}",
        ))
        merged.diagnostics.append(
            f"build_query: command exited {proc.returncode}"
        )
        return None
    # The query is expected to have written/refreshed the configured compile DB.
    db: Path | None = None
    if cfg.compile_db and sources is not None:
        for match in sorted(sources.glob(cfg.compile_db)):
            if match.is_file():
                db = match
                break
    if db is None:
        db = _autodiscover_compile_db(sources)
    extractors.append(ExtractorRecord(
        name="build_query",
        status="ok" if db is not None else "partial",
        detail=(
            f"ran `{argv[0]} …`; compile DB at {DEFAULT_REDACTION.path(str(db))}"
            if db is not None
            else f"ran `{argv[0]} …` but no compile DB was produced"
        ),
    ))
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
        directory = str(getattr(cu, "directory", "") or "").replace("\\", "/").rstrip("/")
        if directory and posix.startswith(directory + "/"):
            return posix[len(directory) + 1:] in tree_rel
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
) -> SourceAbiSurface | None:
    """Run L4 replay over a source tree; ``None`` when no source tree is given.

    Requires L3 compile units to replay against (ADR-030 D5). A missing source
    extractor (clang/castxml) yields a partial surface and a clear note rather
    than aborting — the artifact tiers stay authoritative (ADR-028 D3).
    """
    if sources is None:
        return None
    from .source_abi import SourceAbiSurface
    from .source_replay import public_header_roots_for, run_source_replay

    if not merged.compile_units:
        # No L3 to replay against: source ABI replay needs compile commands to
        # know how each TU is parsed. Record why, but do not synthesize an empty
        # L4 surface — otherwise a bare tree with no build info would embed an
        # all-empty pack. With no other facts the caller drops the pack entirely.
        extractors.append(ExtractorRecord(
            name=f"source_abi:{extractor}", status="skipped",
            detail=(
                "no compile units (L3) to replay; pass --build-info or add a "
                "compile_commands.json to the source tree"
            ),
        ))
        return None

    impl, tool_name = _make_source_extractor(extractor, clang_bin)
    if not impl.available():
        extractors.append(ExtractorRecord(
            name=f"source_abi:{extractor}", status="failed",
            detail=f"{tool_name} not found in PATH; source-only checks disabled",
        ))
        return SourceAbiSurface()

    roots = public_header_roots_for(merged)
    surface, diagnostics = run_source_replay(
        merged, impl, scope=scope, public_header_roots=roots,
        exported_symbols=exported_symbols,
    )
    for diag in diagnostics:
        merged.diagnostics.append(f"source_abi: {diag}")
    parsed = int(surface.coverage.get("compile_units_parsed", 0) or 0)
    selected = int(surface.coverage.get("compile_units_selected", 0) or 0)
    extractors.append(ExtractorRecord(
        name=f"source_abi:{extractor}",
        status="ok" if parsed else "partial",
        detail=f"scope={scope}, {parsed}/{selected} TUs parsed, {len(diagnostics)} failures",
    ))
    return surface


def _make_source_extractor(extractor: str, clang_bin: str) -> tuple[SourceAbiExtractor, str]:
    if extractor == "castxml":
        from .source_extractors import CastxmlSourceExtractor
        return CastxmlSourceExtractor(), "castxml"
    from .source_extractors import ClangSourceExtractor
    return ClangSourceExtractor(clang_bin=clang_bin), clang_bin


# ── L5: source graph ──────────────────────────────────────────────────────────


def _build_inline_graph(
    merged: BuildEvidence, surface: SourceAbiSurface | None
) -> SourceGraphSummary | None:
    """Fold L3 + optional L4 into the compact L5 source graph (always when L3).

    Per the amendment D2 the graph is built whenever a source surface or build
    evidence exists — it is compact by design (ADR-031 D7), so there is no
    separate opt-in flag.
    """
    has_build = bool(merged.compile_units or merged.targets)
    if not has_build and surface is None:
        return None
    from .source_graph import build_source_graph

    graph = build_source_graph(merged, source_abi=surface)
    graph.finalize()
    return graph


# ── coverage rows ─────────────────────────────────────────────────────────────


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
        l3 = LayerCoverage(
            layer=DataLayer.L3_BUILD.value,
            status=CoverageStatus.PRESENT,
            confidence=LayerConfidence.HIGH if merged.targets else LayerConfidence.REDUCED,
            detail=(
                f"{'+'.join(systems)}, {len(merged.compile_units)} compile units, "
                f"{len(merged.targets)} targets"
            ),
        )
    else:
        # A3: a build query that was attempted but failed (or was blocked because
        # --allow-build-query was not set) yielded no L3 facts. Surface that as a
        # `partial` row with the reason instead of a silent `not_collected`, so
        # the coverage/capability report tells the user exactly what to fix.
        bq = next(
            (e for e in extractors
             if e.name == "build_query" and e.status in _BUILD_QUERY_DIAG_STATUSES),
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
            surface.reachable_declarations or surface.reachable_types
            or surface.reachable_macros or surface.reachable_templates
            or surface.reachable_inline_bodies
        )
        l4 = LayerCoverage(
            layer=DataLayer.L4_SOURCE_ABI.value,
            status=CoverageStatus.PRESENT if any_entities else CoverageStatus.PARTIAL,
            confidence=LayerConfidence.HIGH if any_entities else LayerConfidence.REDUCED,
        )
    else:
        l4 = LayerCoverage(layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.NOT_COLLECTED)

    if graph is not None:
        l5 = LayerCoverage(
            layer=DataLayer.L5_SOURCE_GRAPH.value,
            status=CoverageStatus.PRESENT if graph.edges else CoverageStatus.PARTIAL,
            confidence=LayerConfidence.REDUCED if graph.edges else LayerConfidence.UNKNOWN,
        )
    else:
        l5 = LayerCoverage(layer=DataLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.NOT_COLLECTED)
    return [l3, l4, l5]
