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

"""Source ABI replay orchestration: scope selection, per-TU cache, driver (ADR-030 D7, D8).

This is the *phase 7* layer that ties the phase-2 extractors, the phase-3 linker,
and the phase-4 diff into one runnable pipeline over an ADR-029
:class:`BuildEvidence` tree, without forcing a full re-parse of every translation
unit:

- :func:`select_compile_units` implements the D7 replay scopes
  (``off``/``headers-only``/``changed``/``target``/``full``) — a pure function of
  the build evidence plus a changed-path set / target id. The user-facing CI
  evidence modes (ADR-033 D2) map onto these scopes.
- :class:`SourceAbiCache` implements the D8 per-TU cache. The cache key folds the
  extractor identity, the source/header *content* hashes, and the normalized
  compile-context hash, so a TU is re-parsed only when something that could
  change its dump changed. Invalidation prefers false misses over false hits
  (ADR-033 D5): when the source content cannot be read the TU is treated as
  uncacheable and always re-extracted.
- :func:`run_source_replay` drives extraction over the selected units (through
  the cache when given), links the per-TU dumps into one
  :class:`SourceAbiSurface`, and records extractor failures as diagnostics
  (partial L4 coverage, ADR-028 D7) instead of aborting.

Linking and diffing stay cheap and uncached (ADR-030 D8); only the per-TU dumps
are cached.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import time
from collections.abc import Iterable, Mapping, Sequence
from functools import partial
from pathlib import Path

from .. import deadline
from ..storage.code_identity import abicheck_code_fingerprint
from ..storage.tu_parse_store import (
    TuParseStore,
    content_key,
    digest_file,
)
from .build_evidence import BuildEvidence, CompileUnit, Target
from .l5_ast_run import L5AstRun
from .source_abi import SOURCE_ABI_VERSION, SourceAbiSurface, SourceAbiTu
from .source_extractors.base import SourceAbiExtractor, SourceExtractionError
from .source_extractors.compile_invocation import (
    parse_compile_invocation,
    replayed_tokens_for,
)
from .source_link import link_source_abi
from .source_replay_pool import (
    _extract_cache_misses,
    _l4_jobs,
    _l4_use_process_pool,
)
from .source_replay_shared import run_shared_dumps, shares_ast_dump

_log = logging.getLogger(__name__)

#: The ADR-030 D7 replay scopes, in increasing breadth. ``off`` parses nothing;
#: ``full`` parses every compile unit in the build evidence.
REPLAY_SCOPES = ("off", "headers-only", "changed", "target", "full")

#: Map the user-facing CI evidence modes (ADR-033 D2) to a replay scope. The CI
#: mode selects which evidence layers run; internally it sets the replay scope
#: (ADR-033 D2 mapping table). ``graph-*`` modes reuse the source scopes since
#: the graph layer (ADR-031) builds on the same L4 facts.
CI_MODE_TO_SCOPE: dict[str, str] = {
    "off": "off",
    "build": "off",
    "graph-build": "off",  # L5 graph folds from L3 only — no source replay
    "source-changed": "changed",
    "source-target": "target",
    "graph-summary": "changed",
    "graph-full": "full",
}


def scope_for_ci_mode(mode: str) -> str:
    """Return the ADR-030 replay scope for an ADR-033 CI evidence mode.

    Unknown modes fall back to ``off`` (fail safe: no replay rather than a
    surprise full parse).
    """
    return CI_MODE_TO_SCOPE.get(mode, "off")


#: Which data layers each ADR-033 CI evidence mode collects. ``build`` is L3
#: only (build context, no source replay/graph); ``off`` collects nothing; the
#: source/graph modes collect all three (the replay scope above bounds the cost).
CI_MODE_TO_LAYERS: dict[str, tuple[str, ...]] = {
    "off": (),
    "build": ("L3",),
    # graph-build: L3 build facts + the L5 structural graph (target/source/header/
    # build_option nodes), skipping the costly L4 source replay. Feasible on
    # monorepos where full L4 is hours (field-eval P18 — LLVM graph in ~4s vs hours).
    "graph-build": ("L3", "L5"),
    "source-changed": ("L3", "L4", "L5"),
    "source-target": ("L3", "L4", "L5"),
    "graph-summary": ("L3", "L4", "L5"),
    "graph-full": ("L3", "L4", "L5"),
}


def collection_for_ci_mode(mode: str) -> tuple[str, tuple[str, ...]]:
    """Return ``(replay_scope, layers)`` for an ADR-033 CI evidence mode.

    Drives inline collection at ``dump`` time (ADR-028..033 amendment: the CI
    mode selects the inputs/scopes internally). ``layers`` is empty for ``off``
    so the caller skips embedding entirely; unknown modes fall back to that.
    """
    return scope_for_ci_mode(mode), CI_MODE_TO_LAYERS.get(mode, ())


# -- scope selection (ADR-030 D7) --------------------------------------------


def _norm(path: str) -> str:
    """Normalize a path for cross-source comparison: forward slashes, no ``./``."""
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _path_matches(candidate: str, changed: frozenset[str]) -> bool:
    """Whether ``candidate`` refers to one of the ``changed`` paths.

    Build-evidence paths are frequently absolute (``/work/src/foo.cpp``) while a
    PR's changed-path set is repo-relative (``src/foo.cpp``); match when either
    is a path-component suffix of the other so the two spellings line up without
    a false hit on a mere basename collision (``foo.cpp`` vs ``other/foo.cpp``
    only match when the whole relative tail agrees).
    """
    if not candidate:
        return False
    c = _norm(candidate)
    for ch in changed:
        n = _norm(ch)
        if c == n or c.endswith("/" + n) or n.endswith("/" + c):
            return True
    return False


def _target_owns_changed_header(target: Target, changed: frozenset[str]) -> bool:
    """Whether a target lists a public/private header among the changed paths."""
    return any(
        _path_matches(h, changed)
        for h in (*target.public_headers, *target.private_headers)
    )


def _norm_include_map(
    include_map: Mapping[str, Iterable[str]] | None,
) -> dict[str, list[str]]:
    """Normalize a ``{compile_unit_id: includes}`` map to lists of strings.

    Tolerates any iterable of paths per unit (the include extractor emits lists;
    a hand-built map might pass a set/tuple). Empty/None → ``{}`` so callers can
    treat "no include graph" uniformly.
    """
    if not include_map:
        return {}
    return {
        str(cu_id): [str(p) for p in (paths or ()) if p]
        for cu_id, paths in include_map.items()
    }


def select_compile_units(
    build: BuildEvidence,
    *,
    scope: str,
    changed_paths: Iterable[str] = (),
    include_map: Mapping[str, Iterable[str]] | None = None,
    public_header_roots: Sequence[str] | None = None,
) -> list[CompileUnit]:
    """Select which compile units to replay for an ADR-030 D7 ``scope``.

    Pure: a function of the build evidence plus the changed-path set,
    and — when supplied — a per-TU **include graph** ``{compile_unit_id:
    [included_path, ...]}`` (ADR-031 D3, from compiler depfiles via
    :func:`include_graph.parse_depfile` / :class:`include_graph.ClangIncludeExtractor`).
    The include graph makes ``headers-only`` and ``changed`` *precise* instead of
    approximate (ADR-030 follow-up #4); without it the previous target-ownership
    heuristics apply unchanged, so the parameter is fully optional.

    - ``off`` — nothing.
    - ``headers-only`` — a subset that covers the public API surface for fast
      replay. **With an include graph**: the minimal set of TUs (greedy set
      cover) whose included files together cover every public header — so a
      header included by no representative TU is not silently dropped.
      **Without one**: the first compile unit (by id) of each target that
      declares public headers (a representative subset). Either way falls back to
      every unit when there is nothing to scope by.
    - ``changed`` — units whose source is a changed path, plus the units a changed
      *header* actually affects. **With an include graph**: exactly the TUs whose
      transitive includes contain the changed header. **Without one**: every unit
      of any target that owns a changed header, falling back to a full fan-out
      when a header maps to no TU (the cache then skips the unaffected units).
      PR mode (ADR-025 changed-path signal).
    - ``target`` — every unit attached to some build target (release-baseline
      mode), falling back to all units.
    - ``full`` — every compile unit (nightly/deep mode).
    """
    if scope not in REPLAY_SCOPES:
        raise ValueError(
            f"unknown replay scope {scope!r}; expected one of {REPLAY_SCOPES}"
        )
    if scope == "off":
        return []
    # Drop assembler units *before* any scope chooses representatives: the
    # headers-only heuristic picks one unit per target and set cover picks one
    # per header, so filtering afterwards would lose that target's or header's
    # coverage whenever the chosen representative happened to be assembly.
    eligible = [cu for cu in build.compile_units if not is_assembly_unit(cu)]
    if len(eligible) != len(build.compile_units):
        build = dataclasses.replace(build, compile_units=eligible)
    if scope == "full":
        return list(build.compile_units)
    inc = _norm_include_map(include_map)
    if scope == "headers-only":
        return _select_headers_only(build, inc, public_header_roots)
    if scope == "target":
        return _select_target(build)
    return _select_changed(build, frozenset(changed_paths), inc)


#: Source suffixes of assembler translation units. They declare no C/C++ ABI,
#: so source replay has nothing to extract from them, and handing one to a
#: C-family front end (castxml) produces unparseable output (zstd's
#: ``huf_decompress_amd64.S`` in its library target). Case-sensitive on
#: purpose: ``.S`` (preprocessed asm) and ``.s`` are both assembly, while
#: ``.C`` is a C++ suffix.
_ASSEMBLY_SUFFIXES = (".s", ".S")
#: Assembler suffixes no C-family suffix shares in any case, so they are
#: matched case-insensitively (MASM sources are often spelled ``.ASM``).
_ASSEMBLY_SUFFIXES_ANY_CASE = (".sx", ".asm")


def is_assembly_unit(compile_unit: CompileUnit) -> bool:
    """Whether *compile_unit* compiles an assembler source (not C-family)."""
    source = str(compile_unit.source)
    return source.endswith(_ASSEMBLY_SUFFIXES) or source.lower().endswith(
        _ASSEMBLY_SUFFIXES_ANY_CASE
    )


def _select_headers_only(
    build: BuildEvidence,
    include_map: dict[str, list[str]],
    public_header_roots: Sequence[str] | None = None,
) -> list[CompileUnit]:
    if include_map:
        picked = _headers_only_set_cover(build, include_map, public_header_roots)
        if picked is not None:
            return picked
    return _select_headers_only_heuristic(build)


def _select_headers_only_heuristic(build: BuildEvidence) -> list[CompileUnit]:
    by_target: dict[str, list[CompileUnit]] = {}
    for cu in build.compile_units:
        by_target.setdefault(cu.target_id, []).append(cu)
    targets_with_headers = _public_header_compile_owner_ids(build)
    picked_heur: list[CompileUnit] = []
    for tid in sorted(targets_with_headers):
        group = sorted(by_target.get(tid, []), key=lambda c: c.id)
        if group:
            picked_heur.append(group[0])
    # No target declares public headers (or none of them own a compile unit):
    # there is nothing to scope by, so fall back to a full parse rather than
    # silently producing an empty surface.
    return picked_heur or list(build.compile_units)


def _public_header_compile_owner_ids(build: BuildEvidence) -> set[str]:
    compile_target_ids = {cu.target_id for cu in build.compile_units if cu.target_id}
    reverse_deps: dict[str, set[str]] = {}
    for target in build.targets:
        for dep in target.dependencies:
            reverse_deps.setdefault(dep, set()).add(target.id)
    owners: set[str] = set()
    for target in build.targets:
        if target.public_headers:
            owners.add(target.id)
            if target.id not in compile_target_ids:
                owners.update(reverse_deps.get(target.id, set()))
    return owners


def _headers_only_set_cover(
    build: BuildEvidence,
    include_map: dict[str, list[str]],
    public_header_roots: Sequence[str] | None = None,
) -> list[CompileUnit] | None:
    """Minimal TU set whose includes cover every public header (greedy set cover).

    Returns ``None`` to defer to the heuristic when there is no public-header set
    to cover, or when the include graph covers none of them (so the surface would
    otherwise be empty). The cover is over the headers the graph *can* reach; any
    public header no recorded TU includes is left to the heuristic by returning
    ``None`` only when the graph covers nothing at all.
    """
    public = sorted(
        set(public_header_roots_for(build)) | set(public_header_roots or ())
    )
    if not public:
        return None
    by_id = {cu.id: cu for cu in build.compile_units}
    header_owners = _public_header_owner_targets(build)
    coverage = _tu_public_header_coverage(by_id, include_map, public, header_owners)
    if not coverage:
        return None
    chosen, need = _greedy_set_cover(coverage, public)
    # A *partial* include graph may not reach every build-declared public header.
    # Returning the cover for only the reachable ones would silently drop a public
    # target whose source-only changes would never be parsed. Fall back to the
    # representative target heuristic for build-owned headers, but keep selected
    # external CLI-root TUs because those roots have no representative target.
    if need and any(header_owners.get(ph) for ph in need):
        return _cover_with_heuristic_fallback(
            build, chosen, coverage, header_owners, by_id
        )
    return [by_id[c] for c in chosen]


def _public_header_owner_targets(build: BuildEvidence) -> dict[str, set[str]]:
    """Map each declared public header to the target ids that own its compile context."""
    # Which target(s) *declare* each public header. A header must be fingerprinted
    # under the compile context of an owning target, not a downstream app/test TU
    # that merely includes it (different defines/include paths would mis-fingerprint
    # the surface) — so a TU may only "cover" a public header its own target
    # declares (Codex review).
    header_owners: dict[str, set[str]] = {}
    compile_target_ids = {cu.target_id for cu in build.compile_units if cu.target_id}
    reverse_deps: dict[str, set[str]] = {}
    for target in build.targets:
        for dep in target.dependencies:
            reverse_deps.setdefault(dep, set()).add(target.id)
    for target in build.targets:
        for ph in target.public_headers:
            owners = header_owners.setdefault(ph, set())
            owners.add(target.id)
            # Bazel often models public headers as a header-only helper target
            # (for example `:__kernel_headers__`) that compile targets depend on.
            # Those direct reverse deps are the owning compile contexts for the
            # header surface; accepting arbitrary downstream TUs would still be
            # too broad.
            if target.id not in compile_target_ids:
                owners.update(reverse_deps.get(target.id, set()))
    return header_owners


def _tu_public_header_coverage(
    by_id: dict[str, CompileUnit],
    include_map: dict[str, list[str]],
    public: list[str],
    header_owners: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Map each recorded TU to the public headers it includes and may cover."""
    public_suffixes = {ph: _suffixes(_norm(ph)) for ph in public}
    # cu_id -> set of public headers that TU includes (build-root-stable match) and
    # whose owning target it belongs to.
    coverage: dict[str, set[str]] = {}
    for cu_id, incs in include_map.items():
        cu = by_id.get(cu_id)
        if cu is None:
            continue
        inc_norms = {_norm(inc) for inc in incs if inc}
        inc_suffixes = {suffix for inc in inc_norms for suffix in _suffixes(inc)}
        covered = {
            ph
            for ph in public
            if _included(ph, inc_norms, inc_suffixes, public_suffixes[ph])
            and (
                not header_owners.get(ph)
                or cu.target_id in header_owners.get(ph, set())
            )
        }
        if covered:
            coverage[cu_id] = covered
    return coverage


def _greedy_set_cover(
    coverage: dict[str, set[str]], public: list[str]
) -> tuple[list[str], set[str]]:
    """Greedily pick TU ids covering ``public``; returns ``(chosen, still_needed)``."""
    need = set(public)
    chosen: list[str] = []
    # Greedy: repeatedly take the TU covering the most still-needed headers;
    # break ties by compile-unit id for determinism.
    while need:
        best = min(
            (cid for cid in coverage if coverage[cid] & need),
            key=lambda cid: (-len(coverage[cid] & need), cid),
            default=None,
        )
        if best is None:
            break
        chosen.append(best)
        need -= coverage[best]
    return chosen, need


def _cover_with_heuristic_fallback(
    build: BuildEvidence,
    chosen: list[str],
    coverage: dict[str, set[str]],
    header_owners: dict[str, set[str]],
    by_id: dict[str, CompileUnit],
) -> list[CompileUnit]:
    """Representative-target fallback for a partial cover, keeping external-root TUs."""
    picked = _select_headers_only_heuristic(build)
    picked_ids = {cu.id for cu in picked}
    for cu_id in chosen:
        if any(not header_owners.get(ph) for ph in coverage.get(cu_id, ())):
            cu = by_id[cu_id]
            if cu.id not in picked_ids:
                picked.append(cu)
                picked_ids.add(cu.id)
    return picked


def _uncovered_public_headers(
    roots: Sequence[str],
    units: Sequence[CompileUnit],
    include_map: dict[str, list[str]],
) -> list[str]:
    """Public roots not reached by the selected units' include graph."""
    if not roots or not include_map:
        return []
    inc_norms: set[str] = set()
    inc_suffixes: set[str] = set()
    selected = {cu.id for cu in units}
    for cu_id, incs in include_map.items():
        if cu_id not in selected:
            continue
        for inc in incs:
            norm = _norm(inc)
            inc_norms.add(norm)
            inc_suffixes.update(_suffixes(norm))
    out: list[str] = []
    for root in roots:
        suffixes = _suffixes(_norm(root))
        if not _included(root, inc_norms, inc_suffixes, suffixes):
            out.append(root)
    return out


def _suffixes(path: str) -> set[str]:
    parts = [p for p in path.split("/") if p]
    return {"/".join(parts[i:]) for i in range(len(parts))}


def _included(
    public_header: str,
    include_norms: set[str],
    include_suffixes: set[str],
    public_suffixes: set[str],
) -> bool:
    """Whether one of ``includes`` is the same file as ``public_header``.

    Reuses the build-root-stable path-suffix match so an absolute included path
    (``/work/include/foo.h``) lines up with a repo-relative public header
    (``include/foo.h``).
    """
    public_norm = _norm(public_header)
    public_dir_suffixes = {s for s in public_suffixes if "/" in s.strip("/")}
    include_dir_suffixes = {s for s in include_suffixes if "/" in s.strip("/")}
    return (
        public_norm in include_norms
        or ("/" in public_norm.strip("/") and public_norm in include_suffixes)
        or bool(public_dir_suffixes & include_norms)
        or bool(public_dir_suffixes & include_dir_suffixes)
    )


def _select_target(build: BuildEvidence) -> list[CompileUnit]:
    target_ids = {t.id for t in build.targets}
    attached = [cu for cu in build.compile_units if cu.target_id in target_ids]
    return attached or list(build.compile_units)


#: C/C++ header extensions used to tell a changed *header* (whose including TUs
#: we cannot enumerate without a build graph) from a changed source file.
_HEADER_EXTS = (".h", ".hpp", ".hh", ".hxx", ".h++", ".inc", ".ipp", ".tcc", ".inl")


def _looks_like_header(path: str) -> bool:
    return _norm(path).lower().endswith(_HEADER_EXTS)


def _select_changed(
    build: BuildEvidence, changed: frozenset[str], include_map: dict[str, list[str]]
) -> list[CompileUnit]:
    if not changed:
        return []
    units = build.compile_units
    header_changed = any(_looks_like_header(c) for c in changed)
    all_covered = bool(units) and all(cu.id in include_map for cu in units)
    graph_partial = bool(include_map) and not all_covered

    # A *partial* include graph cannot be trusted to **exclude** a TU on a header
    # change: a covered TU whose depfile omitted the changed header would be
    # wrongly marked unaffected, dropping its source-only macro/default/inline
    # changes from PR-mode replay. So when a header changed and the graph does not
    # cover every unit, fan out to all units — the per-TU dump cache (D8) then
    # skips the TUs whose recorded read_files did not actually change, so the
    # fan-out costs nothing for unaffected units (Codex review). Negative include
    # matches are trusted only when the graph covers every compile unit (below).
    if header_changed and graph_partial:
        return list(units)

    owning_targets = {
        t.id for t in build.targets if _target_owns_changed_header(t, changed)
    }
    picked: list[CompileUnit] = []
    seen: set[str] = set()
    for cu in units:
        if cu.id in seen:
            continue
        if _path_matches(cu.source, changed):
            hit = True
        elif cu.id in include_map:
            # Precise: the include graph knows exactly which files this TU pulls
            # in (and here it covers every unit, so a negative is trustworthy), so
            # it is affected iff a changed path is among them — the precision win
            # over target-ownership (ADR-030 follow-up #4).
            hit = any(_path_matches(inc, changed) for inc in include_map[cu.id])
        else:
            # No include-graph entry for this TU → fall back to the
            # target-ownership heuristic for it.
            hit = cu.target_id in owning_targets
        if hit:
            picked.append(cu)
            seen.add(cu.id)
    if picked:
        return picked
    # No unit matched. Fail open (ADR-025 D3) when there is **no** include graph
    # and a header changed but mapped to no TU (target header metadata lists only
    # a target's own headers, not transitive private ones like
    # include/detail/config.h). With a full include graph the empty result is
    # authoritative — a header included by no unit genuinely affects nothing.
    if not include_map and header_changed:
        return list(units)
    return []


def public_header_roots_for(build: BuildEvidence) -> list[str]:
    """Collect the public-header set across all build targets (D5 linker input).

    De-duplicated and sorted for determinism.
    """
    roots: set[str] = set()
    for target in build.targets:
        roots.update(target.public_headers)
    return sorted(roots)


# -- per-TU cache (ADR-030 D8) -----------------------------------------------


def _resolve_source(compile_unit: CompileUnit) -> Path | None:
    """Best-effort absolute path to a compile unit's source on disk.

    Expands a leading ``~`` home placeholder (the evidence redaction policy,
    ADR-032 D7) and joins a relative source against the unit's ``directory``.
    Returns ``None`` when the resolved path does not point at a readable file —
    the caller then treats the TU as uncacheable (prefer a false miss, D8).
    """
    raw = os.path.expanduser(compile_unit.source) if compile_unit.source else ""
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute() and compile_unit.directory:
        path = Path(os.path.expanduser(compile_unit.directory)) / path
    return path if path.is_file() else None


def _digest_path(raw: str) -> str:
    """Content digest of a header root, whether it is a file or a directory.

    A directory root folds in every contained file's path+content so adding,
    removing, or editing any header under it invalidates the key. An
    unreadable/missing root contributes only its (redacted) path string, so two
    runs that both cannot see it still agree — it is the *source* being
    unreadable, handled separately, that makes a TU uncacheable.
    """
    expanded = os.path.expanduser(raw) if raw else ""
    p = Path(expanded) if expanded else None
    if p and p.is_file():
        return digest_file(p)
    if p and p.is_dir():
        h = hashlib.sha256()
        for child in sorted(p.rglob("*")):
            if child.is_file():
                h.update(child.as_posix().encode("utf-8"))
                h.update(digest_file(child).encode("utf-8"))
        return h.hexdigest()
    return "path:" + _norm(raw)


def compute_tu_cache_key(
    *,
    extractor_name: str,
    extractor_version: str,
    compile_unit: CompileUnit,
    public_header_roots: Sequence[str],
    compiler_override: str | None = None,
) -> str | None:
    """Compute the D8 per-TU cache key, or ``None`` if the TU is uncacheable.

    The key is one content-addressed store key for a parsed invocation
    (:func:`~..storage.tu_parse_store.content_key`), built from three groups of
    parts: the **tool identity** (schema version, abicheck code fingerprint,
    extractor name and version), the **input digests** (the source file's content
    and location, and every public-header root's content), and the **parsed
    invocation** (:func:`parse_compile_invocation`, the same object every renderer
    reads). Keying on the parsed invocation rather than the raw compile unit means
    a fact the extractor actually replays -- the emulated driver, the rebased
    include directories, the replayed forced-include and ABI flags -- is in the key
    exactly as the parse decided it, not as the build spelled it.

    Returns ``None`` when the source content cannot be read, so the driver
    re-extracts rather than risk a false cache hit on stale content (ADR-033 D5).

    This is the *preliminary* key: it cannot see a TU's transitively included
    private/forced headers before parsing. :class:`SourceAbiCache` closes that gap
    by additionally re-validating the content hashes of every file the extractor
    recorded reading (``SourceAbiTu.read_files``), so the full D8 "transitive
    included … header hashes" requirement is met across key + dependency check.
    """
    source = _resolve_source(compile_unit)
    if source is None:
        return None
    inv = parse_compile_invocation(compile_unit, compiler_override)
    parts = [
        # Tool identity: which parser, at which version, with which abicheck code.
        "schema:" + str(SOURCE_ABI_VERSION),
        "code:" + abicheck_code_fingerprint(),
        "extractor:" + extractor_name,
        "extractor_version:" + extractor_version,
        # Input digests. The source location (not just its content) is folded in:
        # a relative `#include "local.h"` and `__FILE__` depend on the path, so two
        # TUs with identical contents must not share a dump (CodeRabbit review).
        "src_path:" + source.as_posix(),
        "src:" + digest_file(source),
        "cwd:" + _norm(os.path.expanduser(compile_unit.directory or "")),
        "roots:" + json.dumps(sorted(public_header_roots)),
        # The parsed invocation. Lists go through json.dumps so a comma inside one
        # value cannot make two different lists fold to the same text.
        "compiler:" + inv.compiler,
        "msvc:" + str(inv.msvc),
        "language:" + inv.language,
        "standard:" + inv.standard,
        "run_dir:" + (inv.run_directory or ""),
        "target:" + inv.target_triple,
        "sysroot:" + inv.sysroot,
        "defines:" + json.dumps(sorted(inv.defines)),
        "undefines:" + json.dumps(sorted(inv.undefines)),
        "include_dirs:" + json.dumps(list(inv.include_dirs)),
        "system_include_dirs:" + json.dumps(list(inv.system_include_dirs)),
        "replayed:" + json.dumps(replayed_tokens_for(compile_unit, compiler_override)),
        "replayed_include_dirs:" + json.dumps(list(inv.replayed_include_dirs)),
    ]
    for root in sorted(public_header_roots):
        parts.append(f"hdr:{_norm(root)}:{_digest_path(root)}")
    return content_key(parts)


class SourceAbiCache:
    """A content-addressed on-disk cache of per-TU :class:`SourceAbiTu` dumps (D8).

    A view over :class:`~..storage.tu_parse_store.TuParseStore`, the one store
    that also backs every other per-invocation parse result. The store owns the
    entry format and the dependency re-validation; this class owns the
    ``SourceAbiTu`` payload and the hit/miss instrumentation (ADR-033 D9).

    Keys come from :func:`compute_tu_cache_key`. Because that key cannot see a
    TU's *transitive* private/forced includes before parsing, each entry also
    records the content hash of every file the extractor actually read
    (`SourceAbiTu.read_files`). On lookup those hashes are re-validated, so an
    edit to any included header -- not just a configured public root -- is a
    cache miss (ADR-030 D8 "transitive included … header hashes"). A missing or
    unreadable dependency also misses (prefer a false miss over a false hit,
    ADR-033 D5). Parsing is the expensive step, so linking is still recomputed
    each run rather than cached.
    """

    def __init__(self, cache_dir: Path | str) -> None:
        self._store = TuParseStore(cache_dir)
        # ADR-033 D9 — hit/miss instrumentation for the cache_hit_rate metric.
        self.hits = 0
        self.misses = 0

    @property
    def cache_dir(self) -> Path:
        return self._store.cache_dir

    @property
    def hit_rate(self) -> float | None:
        """Fraction of cacheable lookups served from cache, or ``None`` if none."""
        total = self.hits + self.misses
        return self.hits / total if total else None

    def get(
        self, key: str | None, memo: dict[str, str | None] | None = None
    ) -> SourceAbiTu | None:
        """Look up a cached TU, re-validating its recorded dependencies.

        ``memo`` (optional) is a per-pass path→digest cache shared across all
        lookups in one ``run_source_replay`` invocation so a header included by
        many TUs is hashed once. It must **not** outlive a pass -- a file may
        change between passes -- so direct callers that omit it get the safe
        always-rehash behaviour.
        """
        tu = self._get(key, memo)
        # A None key is "uncacheable" (not a lookup); only count real lookups so
        # the hit rate reflects cacheable TUs.
        if key:
            if tu is not None:
                self.hits += 1
            else:
                self.misses += 1
        return tu

    def _get(
        self, key: str | None, memo: dict[str, str | None] | None = None
    ) -> SourceAbiTu | None:
        if not key:
            return None
        payload = self._store.lookup(key, memo)
        if not isinstance(payload, dict):
            return None
        try:
            return SourceAbiTu.from_dict(payload)
        except (KeyError, TypeError, ValueError):
            # A structurally bad payload is a miss, not a crash that aborts replay.
            return None

    def put(
        self,
        key: str | None,
        tu: SourceAbiTu,
        memo: dict[str, str | None] | None = None,
    ) -> None:
        if not key:
            return
        self._store.publish(key, tu.to_dict(), tu.read_files, memo)


# -- replay driver -----------------------------------------------------------


def run_source_replay(
    build: BuildEvidence,
    extractor: SourceAbiExtractor,
    *,
    scope: str = "target",
    changed_paths: Iterable[str] = (),
    exported_symbols: Iterable[str] = (),
    public_header_roots: Sequence[str] | None = None,
    cache: SourceAbiCache | None = None,
    include_map: Mapping[str, Iterable[str]] | None = None,
    l5_run: L5AstRun | None = None,
) -> tuple[SourceAbiSurface, list[str]]:
    """Run source ABI replay over a build tree and return the linked surface.

    Drives ``extractor`` over the units selected for ``scope`` (D7), routing each
    through ``cache`` when given (D8), links the per-TU dumps against the library's
    exported symbols and public-header set (D5), and returns
    ``(surface, diagnostics)``. Per-TU extractor failures are recorded as
    diagnostics — partial L4 coverage (ADR-028 D7) — and never abort the run.

    An empty selection (e.g. ``scope='off'`` or a ``changed`` scope with no
    matching paths) yields an empty surface and no diagnostics.

    With *l5_run* (a :func:`~.l5_ast_pass.plan_l5_run` scope) and an extractor
    that reads a shared AST dump (:func:`~.source_replay_shared.shares_ast_dump`), the L5 AST passes
    run in this same pass: a TU that both tiers read is dumped once, and
    ``l5_run.outcomes`` is filled from it. Without that, the caller runs the L5
    passes itself.
    """
    roots = (
        list(public_header_roots)
        if public_header_roots is not None
        else public_header_roots_for(build)
    )
    total_started = time.monotonic()
    units = select_compile_units(
        build,
        scope=scope,
        changed_paths=changed_paths,
        include_map=include_map,
        public_header_roots=roots,
    )
    scope_widened = _headers_only_scope_widened(scope, include_map, units, build)
    # Fresh per-pass dep-digest memo so a shared header is hashed once across all
    # TUs' cache validation (scoped to this call — a file may change before the
    # next pass, so it must not leak out to a later lookup).
    digest_memo: dict[str, str | None] = {}
    # P06: the per-TU extractor (clang/castxml subprocess) is the L4 bottleneck and
    # is embarrassingly parallel. We fan ONLY extractor.extract() out across a
    # thread pool; cache get/put stay single-threaded and results are reassembled
    # in unit order, so the linked surface and diagnostics are byte-for-byte
    # identical to the serial run regardless of worker count.
    cache_started = time.monotonic()
    keys, results, misses = _replay_cache_lookup(
        units, extractor, roots, cache, digest_memo
    )
    cache_elapsed = time.monotonic() - cache_started

    extract_started = time.monotonic()
    if l5_run is not None and shares_ast_dump(extractor):
        jobs, extracted = run_shared_dumps(
            extractor,
            roots,
            units,
            misses,
            l5_run,
            jobs_for=_l4_jobs,
            use_process_pool=_l4_use_process_pool(),
        )
    else:
        jobs = _l4_jobs(len(misses))
        miss_units = [units[i] for i in misses]
        worker = partial(_extract_one, extractor, list(roots or []))
        extracted = _extract_cache_misses(
            worker, miss_units, jobs, use_process_pool=_l4_use_process_pool()
        )
    extract_elapsed = time.monotonic() - extract_started

    tus, diagnostics = _assemble_replay_results(
        units, results, keys, misses, extracted, cache, digest_memo
    )

    link_started = time.monotonic()
    surface = link_source_abi(tus, exported_symbols=exported_symbols)
    link_elapsed = time.monotonic() - link_started
    _record_replay_coverage(
        surface,
        scope=scope,
        include_map=include_map,
        units=units,
        tus=tus,
        diagnostics=diagnostics,
        roots=roots,
        jobs=jobs,
        n_misses=len(misses),
        cache_elapsed=cache_elapsed,
        extract_elapsed=extract_elapsed,
        link_elapsed=link_elapsed,
        total_started=total_started,
        scope_widened=scope_widened,
    )
    return surface, diagnostics


def _headers_only_scope_widened(
    scope: str,
    include_map: Mapping[str, Iterable[str]] | None,
    units: list[CompileUnit],
    build: BuildEvidence,
) -> bool:
    """Whether a ``headers-only`` scope had nothing to scope by and widened to full."""
    return (
        scope == "headers-only"
        and not _norm_include_map(include_map)
        and len(units) == len(build.compile_units)
        and len(units) > 1
    )


def _replay_cache_lookup(
    units: list[CompileUnit],
    extractor: SourceAbiExtractor,
    roots: list[str],
    cache: SourceAbiCache | None,
    digest_memo: dict[str, str | None],
) -> tuple[list[str | None], list[SourceAbiTu | None], list[int]]:
    """Phase 1 (serial): cache lookups, split hits from misses keeping order."""
    keys: list[str | None] = []
    results: list[SourceAbiTu | None] = [None] * len(units)
    misses: list[int] = []
    for i, cu in enumerate(units):
        # Normally cheap (in-memory hashing / cache-file stat), but a large
        # unit count with a cold cache can still burn real wall-clock before
        # Phase 2 even starts -- check the shrinking scan-wide deadline here
        # too, not only inside the per-TU extractor calls Phase 2 dispatches
        # (PR #641 follow-up: --budget's mid-step preemption gap).
        deadline.check()
        key = None
        if cache is not None:
            key_roots = _cache_public_header_roots(extractor, cu, roots)
            key = compute_tu_cache_key(
                extractor_name=getattr(extractor, "name", "source"),
                extractor_version=_extractor_version(extractor),
                compile_unit=cu,
                public_header_roots=key_roots,
                compiler_override=getattr(extractor, "compiler_binary", None),
            )
        keys.append(key)
        cached = cache.get(key, digest_memo) if cache is not None else None
        if cached is not None:
            # The key folds the unit's source and flags but not its id or
            # target: two units that compile the same TU share one entry. Rebind
            # the identity to this unit, as a fresh extraction would, so the
            # hit cannot attribute the dump to another target.
            results[i] = dataclasses.replace(
                cached, tu_id=cu.id, target_id=cu.target_id
            )
        else:
            misses.append(i)
    return keys, results, misses


def _assemble_replay_results(
    units: list[CompileUnit],
    results: list[SourceAbiTu | None],
    keys: list[str | None],
    misses: list[int],
    extracted: list[tuple[SourceAbiTu | None, str | None]],
    cache: SourceAbiCache | None,
    digest_memo: dict[str, str | None],
) -> tuple[list[SourceAbiTu], list[str]]:
    """Phase 3 (serial): cache puts + assemble in unit order (deterministic)."""
    diags: dict[int, str] = {}
    for i, (extracted_tu, err) in zip(misses, extracted):
        if err is None:
            results[i] = extracted_tu
        else:
            diags[i] = err
    miss_set = set(misses)
    tus: list[SourceAbiTu] = []
    diagnostics: list[str] = []
    for i in range(len(units)):
        tu = results[i]
        if tu is not None:
            if cache is not None and i in miss_set and keys[i] is not None:
                cache.put(keys[i], tu, digest_memo)
            tus.append(tu)
        elif i in diags:
            diagnostics.append(diags[i])
    return tus, diagnostics


def _record_replay_coverage(
    surface: SourceAbiSurface,
    *,
    scope: str,
    include_map: Mapping[str, Iterable[str]] | None,
    units: list[CompileUnit],
    tus: list[SourceAbiTu],
    diagnostics: list[str],
    roots: list[str],
    jobs: int,
    n_misses: int,
    cache_elapsed: float,
    extract_elapsed: float,
    link_elapsed: float,
    total_started: float,
    scope_widened: bool,
) -> None:
    """Record replay coverage counters and phase timings on the linked surface."""
    surface.coverage["replay_scope"] = scope
    surface.coverage["include_graph_used"] = bool(_norm_include_map(include_map))
    surface.coverage["compile_units_selected"] = len(units)
    surface.coverage["compile_units_parsed"] = len(tus)
    surface.coverage["extractor_failures"] = len(diagnostics)
    uncovered = _uncovered_public_headers(roots, units, _norm_include_map(include_map))
    if uncovered:
        surface.coverage["public_headers_uncovered"] = len(uncovered)
        surface.coverage["public_headers_uncovered_examples"] = uncovered[:10]
    surface.coverage["cache_lookup_s"] = round(cache_elapsed, 3)
    surface.coverage["extract_s"] = round(extract_elapsed, 3)
    surface.coverage["link_s"] = round(link_elapsed, 3)
    surface.coverage["elapsed_s"] = round(time.monotonic() - total_started, 3)
    surface.coverage["extractor_jobs"] = jobs
    surface.coverage["cache_misses"] = n_misses
    if scope_widened:
        surface.coverage["scope_widened_to_full"] = True
        surface.coverage["scope_widened_reason"] = (
            "headers-only had no include graph/public-header target ownership; "
            "selected every compile unit"
        )


def _extract_one(
    extractor: SourceAbiExtractor,
    roots: list[str],
    cu: CompileUnit,
) -> tuple[SourceAbiTu | None, str | None]:
    """Extract one compile unit; returns ``(tu, None)`` or ``(None, diagnostic)``.

    Module-level (not a closure) so it pickles for ``ProcessPoolExecutor``. The
    per-TU work is stateless, so it is safe to run in any worker/process.
    """
    try:
        return extractor.extract(cu, public_header_roots=roots), None
    except SourceExtractionError as exc:
        return None, f"{cu.source or cu.id}: {exc}"


def _extractor_version(extractor: SourceAbiExtractor) -> str:
    """Pull a version string off an extractor for the cache key, if it exposes one.

    Also folds in ``cache_identity_extra()`` when the extractor exposes it
    (``ClangSourceExtractor`` does, to fold a resolved ``--compiler``
    compiler override into the D8 key — see its docstring) — mirrors
    ``_cache_public_header_roots()``'s optional-hook pattern below, so a
    per-instance identity detail an extractor cares about can ride the
    cache key without changing this function's signature (Codex review).
    """
    version = str(getattr(extractor, "version", "") or "")
    extra_hook = getattr(extractor, "cache_identity_extra", None)
    if callable(extra_hook):
        extra = extra_hook()
        if extra:
            version = f"{version}+{extra}"
    return version


def _cache_public_header_roots(
    extractor: SourceAbiExtractor,
    compile_unit: CompileUnit,
    public_header_roots: list[str],
) -> list[str]:
    """Let extractors fold probe-dependent public-root expansion into D8 keys."""
    hook = getattr(extractor, "effective_public_header_roots_for_cache", None)
    if not callable(hook):
        return public_header_roots
    return list(hook(compile_unit, public_header_roots))
