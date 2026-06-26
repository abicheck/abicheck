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

import hashlib
import json
import logging
import os
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from functools import partial
from pathlib import Path

from .build_evidence import BuildEvidence, CompileUnit, Target
from .source_abi import SOURCE_ABI_VERSION, SourceAbiSurface, SourceAbiTu
from .source_extractors._argv import (
    is_msvc_mode,
    pick_compiler_binary,
    replay_extra_flags,
)
from .source_extractors.base import SourceAbiExtractor, SourceExtractionError
from .source_link import link_source_abi

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


# -- PR-diff localizer (ADR-033 D3) ------------------------------------------

#: Filenames / suffixes that mark a build-system file. A change here is build
#: context, so it triggers at least Phase-1 ``build`` collection (ADR-033 D3.3).
_BUILD_FILE_NAMES = frozenset(
    {
        "cmakelists.txt",
        "makefile",
        "gnumakefile",
        "build",
        "build.bazel",
        "workspace",
        "workspace.bazel",
        "meson.build",
        "meson_options.txt",
        "configure",
        "configure.ac",
        "configure.in",
        "sconstruct",
        "sconscript",
        "cargo.toml",
        "setup.py",
        "pyproject.toml",
    }
)
_BUILD_FILE_SUFFIXES = (
    ".cmake",
    ".mk",
    ".mak",
    ".bazel",
    ".bzl",
    ".ninja",
    ".pri",
    ".pro",
)
#: Source / header suffixes that warrant a source ABI replay (L4) on change.
_SOURCE_FILE_SUFFIXES = (
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".c++",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".h++",
    ".inl",
    ".ipp",
    ".tcc",
    ".cu",
    ".cuh",
    ".m",
    ".mm",
)


def _is_build_file(path: str) -> bool:
    p = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return p in _BUILD_FILE_NAMES or p.endswith(_BUILD_FILE_SUFFIXES)


def _is_source_file(path: str) -> bool:
    return path.replace("\\", "/").lower().endswith(_SOURCE_FILE_SUFFIXES)


def recommend_collect_mode(changed_paths: Iterable[str]) -> str:
    """Recommend an ADR-033 CI evidence mode from a PR's changed paths (D3).

    The PR-diff localizer: a build-system change alone triggers at least Phase-1
    ``build`` (build-context drift); a source/header change pulls in the L4
    source replay via ``source-changed`` (a superset that also engages build
    context). No build- or source-relevant change ⇒ ``off`` (artifact compare
    remains the authority, ADR-028 D3). This never *replaces* the artifact gate —
    it only scopes which optional evidence to collect.
    """
    paths = list(changed_paths)
    has_source = any(_is_source_file(p) for p in paths)
    has_build = any(_is_build_file(p) for p in paths)
    if has_source:
        return "source-changed"
    if has_build:
        return "build"
    return "off"


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


def _units_for_target(build: BuildEvidence, target_id: str) -> list[CompileUnit]:
    return [cu for cu in build.compile_units if cu.target_id == target_id]


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
    target_id: str = "",
    include_map: Mapping[str, Iterable[str]] | None = None,
) -> list[CompileUnit]:
    """Select which compile units to replay for an ADR-030 D7 ``scope``.

    Pure: a function of the build evidence plus the changed-path set / target id,
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
    - ``target`` — units of ``target_id`` (release-baseline mode). When no target
      is given, every unit attached to some target, falling back to all units.
    - ``full`` — every compile unit (nightly/deep mode).
    """
    if scope not in REPLAY_SCOPES:
        raise ValueError(
            f"unknown replay scope {scope!r}; expected one of {REPLAY_SCOPES}"
        )
    units = build.compile_units
    if scope == "off":
        return []
    if scope == "full":
        return list(units)
    inc = _norm_include_map(include_map)
    if scope == "headers-only":
        return _select_headers_only(build, inc)
    if scope == "target":
        return _select_target(build, target_id)
    return _select_changed(build, frozenset(changed_paths), inc)


def _select_headers_only(
    build: BuildEvidence, include_map: dict[str, list[str]]
) -> list[CompileUnit]:
    if include_map:
        picked = _headers_only_set_cover(build, include_map)
        if picked is not None:
            return picked
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
    build: BuildEvidence, include_map: dict[str, list[str]]
) -> list[CompileUnit] | None:
    """Minimal TU set whose includes cover every public header (greedy set cover).

    Returns ``None`` to defer to the heuristic when there is no public-header set
    to cover, or when the include graph covers none of them (so the surface would
    otherwise be empty). The cover is over the headers the graph *can* reach; any
    public header no recorded TU includes is left to the heuristic by returning
    ``None`` only when the graph covers nothing at all.
    """
    public = public_header_roots_for(build)
    if not public:
        return None
    by_id = {cu.id: cu for cu in build.compile_units}
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
            and cu.target_id in header_owners.get(ph, set())
        }
        if covered:
            coverage[cu_id] = covered
    if not coverage:
        return None
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
    # A *partial* include graph may not reach every public header. Returning the
    # cover for only the reachable ones would silently drop a public header no
    # recorded TU includes — its source-only changes would never be parsed. Defer
    # to the representative-per-target heuristic (which covers every public-header
    # target) whenever the cover cannot satisfy all public headers (Codex review).
    if need:
        return None
    return [by_id[c] for c in chosen]


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
    return public_norm in include_suffixes or bool(public_suffixes & include_norms)


def _select_target(build: BuildEvidence, target_id: str) -> list[CompileUnit]:
    if target_id:
        return _units_for_target(build, target_id)
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


def public_header_roots_for(build: BuildEvidence, target_id: str = "") -> list[str]:
    """Collect the public-header set from the build targets (D5 linker input).

    Restricted to ``target_id`` when given, else the union across all targets.
    De-duplicated and sorted for determinism.
    """
    roots: set[str] = set()
    for target in build.targets:
        if target_id and target.id != target_id:
            continue
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


def _digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


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
        return _digest_file(p)
    if p and p.is_dir():
        h = hashlib.sha256()
        for child in sorted(p.rglob("*")):
            if child.is_file():
                h.update(child.as_posix().encode("utf-8"))
                h.update(_digest_file(child).encode("utf-8"))
        return h.hexdigest()
    return "path:" + _norm(raw)


def compute_tu_cache_key(
    *,
    extractor_name: str,
    extractor_version: str,
    compile_unit: CompileUnit,
    public_header_roots: Sequence[str],
    schema_version: int = SOURCE_ABI_VERSION,
) -> str | None:
    """Compute the D8 per-TU cache key, or ``None`` if the TU is uncacheable.

    Folds the extractor identity/version, the source-file content hash, each
    public-header-root content hash, the normalized compile-context hash, the
    public-header root set, language standard / target / sysroot, and the source
    schema version. Returns ``None`` when the source content cannot be read, so
    the driver re-extracts rather than risk a false cache hit on stale content
    (ADR-033 D5).

    This is the *preliminary* key: it cannot see a TU's transitively included
    private/forced headers before parsing. :class:`SourceAbiCache` closes that gap
    by additionally re-validating the content hashes of every file the extractor
    recorded reading (``SourceAbiTu.read_files``), so the full D8 "transitive
    included … header hashes" requirement is met across key + dependency check.
    """
    source = _resolve_source(compile_unit)
    if source is None:
        return None
    parts = [
        "abicheck-source-abi-cache",
        str(schema_version),
        extractor_name,
        extractor_version,
        # Source *location* (not just content): two distinct TUs with identical
        # contents must not collide — a relative `#include "local.h"` and
        # `__FILE__` depend on the file's path, so a content-only key could reuse
        # another TU's dump and read_files (CodeRabbit review).
        "src_path:" + source.as_posix(),
        "cwd:" + _norm(os.path.expanduser(compile_unit.directory or "")),
        compile_unit.standard,
        compile_unit.target_triple,
        compile_unit.sysroot or "",
        compile_unit.language,
        "src:" + _digest_file(source),
        "defs:" + ",".join(f"{k}={v}" for k, v in sorted(compile_unit.defines.items())),
        "undefs:" + ",".join(sorted(compile_unit.undefines)),
        "inc:" + ",".join(compile_unit.include_paths),
        "sysinc:" + ",".join(compile_unit.system_include_paths),
        "flags:" + ",".join(compile_unit.abi_relevant_flags),
        # The argv-only replay flags the extractor actually carries — forced
        # includes (`-include`/`-imacros`/`/FI`) and unnormalized include-search
        # paths (`-iquote`/`-idirafter`/`/I`). These change *what* clang parses but
        # live in argv, not the structured fields, so without them a compile
        # command that swaps `-include old.h` for `-include new.h` would reuse a
        # stale cached dump (Codex review #339, P2).
        "replay:" + ",".join(_replay_flags_for_key(compile_unit)),
        "roots:" + ",".join(sorted(public_header_roots)),
    ]
    for root in sorted(public_header_roots):
        parts.append(f"hdr:{_norm(root)}:{_digest_path(root)}")
    blob = "\x00".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _replay_flags_for_key(compile_unit: CompileUnit) -> list[str]:
    """The exact replay flags an extractor would carry from argv, for the cache key.

    Mirrors :func:`source_extractors._argv.replay_extra_flags` so the key folds in
    forced-include / include-search options that change the parsed TU but are not
    captured by the structured ``CompileUnit`` fields (Codex review #339, P2).
    """
    cc_bin = pick_compiler_binary(compile_unit, None)
    cc_id = "msvc" if is_msvc_mode(cc_bin) else "gnu"
    return replay_extra_flags(compile_unit, [], cc_id)


class SourceAbiCache:
    """A content-addressed on-disk cache of per-TU :class:`SourceAbiTu` dumps (D8).

    Keys come from :func:`compute_tu_cache_key` (source + roots + compile context).
    Because that key cannot see a TU's *transitive* private/forced includes before
    parsing, each entry also stores a **dependency map** — the content hash of
    every file the extractor actually read (`SourceAbiTu.read_files`). On lookup
    those hashes are re-validated, so an edit to any included header — not just a
    configured public root — is a cache miss and forces re-extraction (Codex
    review #339, P1; ADR-030 D8 "transitive included … header hashes"). A
    missing/unreadable dependency also misses (prefer a false miss over a false
    hit, ADR-033 D5). Parsing is the expensive step, so linking is still
    recomputed each run rather than cached.
    """

    def __init__(self, cache_dir: Path | str) -> None:
        self.cache_dir = Path(cache_dir)
        # ADR-033 D9 — hit/miss instrumentation for the cache_hit_rate metric.
        self.hits = 0
        self.misses = 0

    @property
    def hit_rate(self) -> float | None:
        """Fraction of cacheable lookups served from cache, or ``None`` if none."""
        total = self.hits + self.misses
        return self.hits / total if total else None

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(
        self, key: str | None, memo: dict[str, str | None] | None = None
    ) -> SourceAbiTu | None:
        """Look up a cached TU, re-validating its recorded dependencies.

        ``memo`` (optional) is a per-pass path→digest cache shared across all
        lookups in one ``run_source_replay`` invocation so a header included by
        many TUs is hashed once. It must **not** outlive a pass — a file may
        change between passes — so direct callers that omit it get the safe
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
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt/half-written cache entry is a miss, never a failure.
            return None
        if not isinstance(entry, dict):
            return None
        # A non-dict deps payload is a malformed entry → miss, never a failure
        # (CodeRabbit review): keep the "corrupt entry is a miss" contract.
        deps = entry.get("deps") or {}
        if not isinstance(deps, dict):
            return None
        # Re-validate every recorded dependency: a changed/missing included file
        # invalidates the dump even though the preliminary key still matches.
        for dep_path, dep_hash in deps.items():
            if _dep_digest(dep_path, memo) != dep_hash:
                return None
        tu_data = entry.get("tu")
        if not isinstance(tu_data, dict):
            return None
        try:
            return SourceAbiTu.from_dict(tu_data)
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
        deps: dict[str, str] = {}
        for dep_path in tu.read_files:
            digest = _dep_digest(dep_path, memo)
            if digest is not None:
                deps[dep_path] = digest
        entry = {"tu": tu.to_dict(), "deps": deps}
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(key).with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        # Atomic publish so a concurrent reader never sees a partial file.
        tmp.replace(self._path(key))


def _dep_digest(path: str, memo: dict[str, str | None] | None = None) -> str | None:
    """Content digest of a dependency file, or ``None`` if it cannot be read.

    A ``None`` (missing/unreadable) dependency makes the entry miss on lookup —
    preferring a false miss over a false hit (ADR-033 D5).

    ``memo`` (optional) caches the digest per path **for the duration of one
    replay pass**: a public header included by N TUs is otherwise re-hashed N
    times in the serial cache-validation phase. Files do not change mid-pass, so
    hashing each once is correct; the memo is created per ``run_source_replay``
    call and discarded after, so a long-lived process still re-reads on the next
    run (a file may change between runs)."""
    if memo is not None and path in memo:
        return memo[path]
    try:
        fp = Path(os.path.expanduser(path)) if path else None
        digest = _digest_file(fp) if (fp is not None and fp.is_file()) else None
    except OSError:
        digest = None
    if memo is not None:
        memo[path] = digest
    return digest


# -- replay driver -----------------------------------------------------------


def run_source_replay(
    build: BuildEvidence,
    extractor: SourceAbiExtractor,
    *,
    scope: str = "target",
    changed_paths: Iterable[str] = (),
    target_id: str = "",
    library: str = "",
    exported_symbols: Iterable[str] = (),
    public_header_roots: Sequence[str] | None = None,
    forced_public: Iterable[str] = (),
    cache: SourceAbiCache | None = None,
    include_map: Mapping[str, Iterable[str]] | None = None,
) -> tuple[SourceAbiSurface, list[str]]:
    """Run source ABI replay over a build tree and return the linked surface.

    Drives ``extractor`` over the units selected for ``scope`` (D7), routing each
    through ``cache`` when given (D8), links the per-TU dumps against the library's
    exported symbols and public-header set (D5), and returns
    ``(surface, diagnostics)``. Per-TU extractor failures are recorded as
    diagnostics — partial L4 coverage (ADR-028 D7) — and never abort the run.

    An empty selection (e.g. ``scope='off'`` or a ``changed`` scope with no
    matching paths) yields an empty surface and no diagnostics.
    """
    roots = (
        list(public_header_roots)
        if public_header_roots is not None
        else public_header_roots_for(build, target_id)
    )
    units = select_compile_units(
        build,
        scope=scope,
        changed_paths=changed_paths,
        target_id=target_id,
        include_map=include_map,
    )
    # Fresh per-pass dep-digest memo so a shared header is hashed once across all
    # TUs' cache validation (scoped to this call — a file may change before the
    # next pass, so it must not leak out to a later lookup).
    digest_memo: dict[str, str | None] = {}
    # P06: the per-TU extractor (clang/castxml subprocess) is the L4 bottleneck and
    # is embarrassingly parallel. We fan ONLY extractor.extract() out across a
    # thread pool; cache get/put stay single-threaded and results are reassembled
    # in unit order, so the linked surface and diagnostics are byte-for-byte
    # identical to the serial run regardless of worker count.
    # Phase 1 (serial): cache lookups, split hits from misses keeping order.
    keys: list[str | None] = []
    results: list[SourceAbiTu | None] = [None] * len(units)
    misses: list[int] = []
    for i, cu in enumerate(units):
        key = None
        if cache is not None:
            key_roots = _cache_public_header_roots(extractor, cu, roots)
            key = compute_tu_cache_key(
                extractor_name=getattr(extractor, "name", "source"),
                extractor_version=_extractor_version(extractor),
                compile_unit=cu,
                public_header_roots=key_roots,
            )
        keys.append(key)
        cached = cache.get(key, digest_memo) if cache is not None else None
        if cached is not None:
            results[i] = cached
        else:
            misses.append(i)

    # Phase 2 (parallel): extract the cache misses. Stateless per TU, so the
    # worker is a module-level function (picklable for the process pool) fed the
    # actual unit rather than an index into a closed-over list.
    diags: dict[int, str] = {}

    jobs = _l4_jobs(len(misses))
    miss_units = [units[i] for i in misses]
    worker = partial(_extract_one, extractor, list(roots or []), target_id)
    if jobs > 1 and len(misses) > 1:
        # Process pool parallelizes the GIL-bound AST post-processing too, not
        # just the clang subprocess wait (opt-in; see _l4_use_process_pool).
        executor_cls = (
            ProcessPoolExecutor if _l4_use_process_pool() else ThreadPoolExecutor
        )
        try:
            with executor_cls(max_workers=jobs) as pool:
                extracted = list(pool.map(worker, miss_units))
        except Exception as exc:  # noqa: BLE001
            # A process pool can fail to start (spawn import error, sandbox with
            # no /dev/shm, …) where threads would not. Degrade to a serial pass
            # rather than aborting L4 — the artifact tiers stay authoritative.
            if executor_cls is ProcessPoolExecutor:
                _log.warning(
                    "L4 process pool failed (%s); falling back to serial extraction",
                    exc,
                )
                extracted = [worker(u) for u in miss_units]
            else:
                raise
    else:
        extracted = [worker(u) for u in miss_units]
    for i, (tu, err) in zip(misses, extracted):
        if err is None:
            results[i] = tu
        else:
            diags[i] = err

    # Phase 3 (serial): cache puts + assemble in unit order (deterministic).
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

    surface = link_source_abi(
        tus,
        exported_symbols=exported_symbols,
        library=library,
        target_id=target_id,
        forced_public=forced_public,
    )
    surface.coverage["replay_scope"] = scope
    surface.coverage["include_graph_used"] = bool(_norm_include_map(include_map))
    surface.coverage["compile_units_selected"] = len(units)
    surface.coverage["compile_units_parsed"] = len(tus)
    surface.coverage["extractor_failures"] = len(diagnostics)
    return surface, diagnostics


#: Hard ceiling on the L4 worker count. Each worker drives a heavyweight clang
#: process (one TU, single-threaded); past ~2× the CPU count the processes only
#: contend for cores and the L3/L5/serialization serial fraction dominates anyway
#: (eval/SCALING.md saw jobs=8 on 4 CPUs *regress*). The explicit
#: ``ABICHECK_L4_JOBS`` override is clamped to this so a stray ``=64`` can't
#: thrash the host — a warning is logged when it is.
def _l4_jobs_ceiling() -> int:
    return max(8, 2 * (os.cpu_count() or 1))


#: Rough peak resident memory budget per concurrent L4 worker (GiB). A heavily
#: templated C++ TU's ``clang -ast-dump=json`` output — and its in-Python parse —
#: can reach several GiB, so the *default* worker count is sized to available RAM
#: as well as CPU count: a low-memory host oversubscribing giant ASTs into one
#: process is how the UXL oneTBB/oneDNN ``s5``/``s6`` replay got OOM-killed (the
#: kernel SIGKILLs the whole replay → ``exit -9``, all L4 work lost). Tunable via
#: ``ABICHECK_L4_JOB_MEM_GIB``; the cap is skipped when RAM can't be read.
_L4_JOB_MEM_BUDGET_GIB = 3.0

_KIB = 1024.0
_GIB = 1024.0 * 1024.0 * 1024.0

#: cgroup memory accounting. In a container the process is confined to a cgroup
#: whose limit is usually *below* the host RAM that ``/proc/meminfo`` reports, so
#: the OOM guard must read it too (Codex review on #458): a 4 GiB-limited pod on a
#: 64 GiB host would otherwise keep the host-sized cap and still get SIGKILLed.
#: The *effective* limit lives at the process's own cgroup path (from
#: ``/proc/self/cgroup``), not the controller root — under a nested cgroup
#: (k8s pod / systemd slice / CI runner) the root is often unbounded while a
#: parent slice imposes the real cap — so we walk leaf→root and take the tightest
#: bounded limit. Roots are module constants so tests can repoint them.
_PROC_SELF_CGROUP = "/proc/self/cgroup"
_CGROUP_V2_ROOT = "/sys/fs/cgroup"  # unified-hierarchy mount
_CGROUP_V1_ROOT = "/sys/fs/cgroup/memory"  # v1 memory-controller mount
#: cgroup v1 reports "unlimited" as a near-INT64_MAX sentinel rather than a
#: keyword; anything at/above this is treated as no limit.
_CGROUP_V1_UNLIMITED = 1 << 62


def _read_int_file(path: str) -> int | None:
    """Read a single integer from ``path`` (cgroup files), or ``None``.

    Returns ``None`` for a missing/unreadable file or a non-integer body such as
    cgroup v2's literal ``max`` (= unbounded), which the callers treat as
    "no cgroup limit".
    """
    try:
        with open(path, encoding="ascii") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _cgroup_rel_paths() -> tuple[str | None, str | None]:
    """``(v2_rel, v1_memory_rel)`` cgroup paths from ``/proc/self/cgroup``.

    Each is ``None`` when that hierarchy isn't listed (e.g. a pure-v2 host has no
    v1 memory line). The v2 line is ``0::/rel``; the v1 memory line is
    ``N:…,memory,…:/rel``.
    """
    v2 = v1 = None
    try:
        with open(_PROC_SELF_CGROUP, encoding="ascii") as fh:
            for line in fh:
                parts = line.rstrip("\n").split(":", 2)
                if len(parts) != 3:
                    continue
                hid, controllers, path = parts
                if hid == "0":
                    v2 = path
                elif "memory" in controllers.split(","):
                    v1 = path
    except OSError:
        pass
    return v2, v1


def _cgroup_chain(root: str, rel: str | None) -> list[Path]:
    """Cgroup dirs from the leaf (``root``/``rel``) up to ``root``, leaf first."""
    base = Path(root)
    chain = [base]
    cur = base
    for part in (rel or "").strip("/").split("/"):
        if part:
            cur = cur / part
            chain.append(cur)
    chain.reverse()
    return chain


def _cgroup_headroom_gib(
    root: str, rel: str | None, max_name: str, cur_name: str, unlimited: int | None
) -> float | None:
    """Tightest memory headroom (GiB) along the leaf→root cgroup chain, or ``None``.

    A bounded ancestor can cap a process more tightly than its own leaf cgroup, so
    the effective headroom is the *minimum* across the chain. ``None`` when no
    level is bounded.
    """
    best: float | None = None
    for d in _cgroup_chain(root, rel):
        limit = _read_int_file(str(d / max_name))
        if limit is None or (unlimited is not None and limit >= unlimited):
            continue
        used = _read_int_file(str(d / cur_name)) or 0
        headroom = max(0.0, (limit - used) / _GIB)
        best = headroom if best is None else min(best, headroom)
    return best


def _cgroup_available_mem_gib() -> float | None:
    """Container memory headroom in GiB from cgroup limits, or ``None``.

    Resolves the process's own cgroup (``/proc/self/cgroup``) and walks leaf→root
    for the tightest bounded limit — cgroup v2 (``memory.max`` − ``memory.current``)
    then v1 (``memory.limit_in_bytes`` − ``memory.usage_in_bytes``). ``None`` when
    nothing is bounded (the common bare-metal/host case).
    """
    v2_rel, v1_rel = _cgroup_rel_paths()
    headroom = _cgroup_headroom_gib(
        _CGROUP_V2_ROOT, v2_rel, "memory.max", "memory.current", None
    )
    if headroom is not None:
        return headroom
    return _cgroup_headroom_gib(
        _CGROUP_V1_ROOT,
        v1_rel,
        "memory.limit_in_bytes",
        "memory.usage_in_bytes",
        _CGROUP_V1_UNLIMITED,
    )


def _meminfo_available_gib(path: str = "/proc/meminfo") -> float | None:
    """Host ``MemAvailable`` in GiB (Linux ``/proc/meminfo``), or ``None``."""
    try:
        with open(path, encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * _KIB / _GIB  # kB -> GiB
    except (OSError, ValueError, IndexError):
        pass
    return None


def _l4_available_mem_gib() -> float | None:
    """Best-effort available RAM in GiB, honouring cgroup limits in containers.

    Returns the *smaller* of host ``MemAvailable`` and the cgroup memory headroom
    so a process confined to a small cgroup on a large host still sizes its L4
    worker count to what it is actually allowed to use. ``None`` when neither
    source is readable (non-Linux / sandbox), which skips the memory clamp.
    """
    candidates = [
        v
        for v in (_meminfo_available_gib(), _cgroup_available_mem_gib())
        if v is not None
    ]
    return min(candidates) if candidates else None


def _l4_job_mem_budget_gib() -> float:
    try:
        return max(
            0.25,
            float(os.environ.get("ABICHECK_L4_JOB_MEM_GIB") or _L4_JOB_MEM_BUDGET_GIB),
        )
    except ValueError:
        return _L4_JOB_MEM_BUDGET_GIB


def _l4_mem_cap() -> int | None:
    """Max L4 workers that fit in available RAM, or ``None`` when RAM can't be read."""
    avail = _l4_available_mem_gib()
    if avail is None:
        return None
    return max(1, int(avail / _l4_job_mem_budget_gib()))


def _l4_jobs(n_units: int) -> int:
    """Worker count for parallel L4 extraction (P06).

    ``ABICHECK_L4_JOBS`` overrides (set ``1`` to force serial — used by tests to
    prove determinism). Otherwise auto: one worker per cache-miss TU, capped at
    the CPU count and 8 (clang is heavy; more workers mostly add contention).

    Both the auto default and an explicit override are additionally capped by
    *available memory* (``_l4_mem_cap``): a single template-heavy TU's clang JSON
    AST can be multiple GiB, so N concurrent workers in one process can exhaust a
    low-memory host and get the whole replay OOM-killed. The memory clamp prevents
    that (set ``ABICHECK_L4_JOB_MEM_GIB`` to tune, or seed/scope the scan to fewer
    TUs); like the oversubscription ceiling, a clamp is logged, never silent.
    """
    mem_cap = _l4_mem_cap()
    env = os.environ.get("ABICHECK_L4_JOBS")
    if env:
        try:
            requested = max(1, int(env))
        except ValueError:
            return 1
        ceiling = _l4_jobs_ceiling()
        if requested > ceiling:
            _log.warning(
                "ABICHECK_L4_JOBS=%d exceeds the oversubscription ceiling (%d "
                "for %d CPUs); clamping to %d",
                requested,
                ceiling,
                os.cpu_count() or 1,
                ceiling,
            )
            requested = ceiling
        if mem_cap is not None and requested > mem_cap:
            _log.warning(
                "ABICHECK_L4_JOBS=%d may not fit in available memory (~%.1f GiB at "
                "~%.1f GiB/worker); clamping to %d to avoid an OOM-killed L4 replay. "
                "Tune ABICHECK_L4_JOB_MEM_GIB, or seed/scope the scan to fewer TUs.",
                requested,
                _l4_available_mem_gib() or 0.0,
                _l4_job_mem_budget_gib(),
                mem_cap,
            )
            return mem_cap
        return requested
    auto = max(1, min(n_units, os.cpu_count() or 1, 8))
    if mem_cap is not None and mem_cap < auto:
        _log.info(
            "L4 workers reduced %d -> %d to fit available memory (~%.1f GiB at "
            "~%.1f GiB/worker); set ABICHECK_L4_JOBS / ABICHECK_L4_JOB_MEM_GIB to override.",
            auto,
            mem_cap,
            _l4_available_mem_gib() or 0.0,
            _l4_job_mem_budget_gib(),
        )
        return mem_cap
    return auto


def _l4_use_process_pool() -> bool:
    """Whether to run the L4 extract phase in a process pool rather than threads.

    After clang returns, the extractor parses clang's (large) JSON AST dump and
    builds structural fingerprints — pure-Python, **GIL-bound** work. A thread
    pool therefore parallelizes only the clang subprocess wait, not that
    post-processing, so the AST work serializes on the GIL (part of the Amdahl
    serial fraction in eval/SCALING.md). A *process* pool parallelizes both, at
    the cost of pickling each ``SourceAbiTu`` back and per-process spawn.

    Opt-in via ``ABICHECK_L4_EXECUTOR=process`` (default ``thread``) so the
    measured win can be validated (``eval/scaling.py``) before it becomes the
    default; an unrecognized value falls back to threads.
    """
    return os.environ.get("ABICHECK_L4_EXECUTOR", "thread").strip().lower() == "process"


def _extract_one(
    extractor: SourceAbiExtractor,
    roots: list[str],
    target_id: str,
    cu: CompileUnit,
) -> tuple[SourceAbiTu | None, str | None]:
    """Extract one compile unit; returns ``(tu, None)`` or ``(None, diagnostic)``.

    Module-level (not a closure) so it pickles for ``ProcessPoolExecutor``. The
    per-TU work is stateless, so it is safe to run in any worker/process.
    """
    try:
        return (
            extractor.extract(cu, public_header_roots=roots, target_id=target_id),
            None,
        )
    except SourceExtractionError as exc:
        return None, f"{cu.source or cu.id}: {exc}"


def _extractor_version(extractor: SourceAbiExtractor) -> str:
    """Pull a version string off an extractor for the cache key, if it exposes one."""
    return str(getattr(extractor, "version", "") or "")


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
