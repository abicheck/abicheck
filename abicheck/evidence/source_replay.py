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
import os
from collections.abc import Iterable, Sequence
from pathlib import Path

from .build_evidence import BuildEvidence, CompileUnit, Target
from .source_abi import SOURCE_ABI_VERSION, SourceAbiSurface, SourceAbiTu
from .source_extractors.base import SourceAbiExtractor, SourceExtractionError
from .source_link import link_source_abi

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
    "source-changed": "changed",
    "source-target": "target",
    "graph-summary": "changed",
    "graph-full": "target",
}


def scope_for_ci_mode(mode: str) -> str:
    """Return the ADR-030 replay scope for an ADR-033 CI evidence mode.

    Unknown modes fall back to ``off`` (fail safe: no replay rather than a
    surprise full parse).
    """
    return CI_MODE_TO_SCOPE.get(mode, "off")


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


def select_compile_units(
    build: BuildEvidence,
    *,
    scope: str,
    changed_paths: Iterable[str] = (),
    target_id: str = "",
) -> list[CompileUnit]:
    """Select which compile units to replay for an ADR-030 D7 ``scope``.

    Pure: a function of the build evidence plus the changed-path set / target id.

    - ``off`` — nothing.
    - ``headers-only`` — a representative subset for fast public-API coverage:
      the first compile unit (by id) of each target that declares public
      headers. Falls back to every unit when no target carries public headers
      (no build graph to scope by). This trades completeness for speed; a TU
      that includes the public headers yields their declarations regardless of
      which TU it is.
    - ``changed`` — units whose source is a changed path, unioned with every unit
      of any target that owns a changed header (a header edit can change the ABI
      of every TU that includes it). PR mode (ADR-025 changed-path signal).
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
    if scope == "headers-only":
        return _select_headers_only(build)
    if scope == "target":
        return _select_target(build, target_id)
    return _select_changed(build, frozenset(changed_paths))


def _select_headers_only(build: BuildEvidence) -> list[CompileUnit]:
    by_target: dict[str, list[CompileUnit]] = {}
    for cu in build.compile_units:
        by_target.setdefault(cu.target_id, []).append(cu)
    targets_with_headers = {t.id for t in build.targets if t.public_headers}
    picked: list[CompileUnit] = []
    for tid in sorted(targets_with_headers):
        group = sorted(by_target.get(tid, []), key=lambda c: c.id)
        if group:
            picked.append(group[0])
    # No target declares public headers (or none of them own a compile unit):
    # there is nothing to scope by, so fall back to a full parse rather than
    # silently producing an empty surface.
    return picked or list(build.compile_units)


def _select_target(build: BuildEvidence, target_id: str) -> list[CompileUnit]:
    if target_id:
        return _units_for_target(build, target_id)
    target_ids = {t.id for t in build.targets}
    attached = [cu for cu in build.compile_units if cu.target_id in target_ids]
    return attached or list(build.compile_units)


def _select_changed(
    build: BuildEvidence, changed: frozenset[str]
) -> list[CompileUnit]:
    if not changed:
        return []
    owning_targets = {
        t.id for t in build.targets if _target_owns_changed_header(t, changed)
    }
    picked: list[CompileUnit] = []
    seen: set[str] = set()
    for cu in build.compile_units:
        if cu.id in seen:
            continue
        if _path_matches(cu.source, changed) or cu.target_id in owning_targets:
            picked.append(cu)
            seen.add(cu.id)
    return picked


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
    schema version — exactly the D8 inputs. Returns ``None`` when the source
    content cannot be read, so the driver re-extracts rather than risk a false
    cache hit on stale content (ADR-033 D5).
    """
    source = _resolve_source(compile_unit)
    if source is None:
        return None
    parts = [
        "abicheck-source-abi-cache",
        str(schema_version),
        extractor_name,
        extractor_version,
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
        "roots:" + ",".join(sorted(public_header_roots)),
    ]
    for root in sorted(public_header_roots):
        parts.append(f"hdr:{_norm(root)}:{_digest_path(root)}")
    blob = "\x00".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class SourceAbiCache:
    """A content-addressed on-disk cache of per-TU :class:`SourceAbiTu` dumps (D8).

    Keys come from :func:`compute_tu_cache_key`; values are the normalized dump
    JSON. The cache is intentionally dumb — parsing is the expensive step, so a
    plain keyed file store is enough, and linking is recomputed each run.
    """

    def __init__(self, cache_dir: Path | str) -> None:
        self.cache_dir = Path(cache_dir)

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(self, key: str | None) -> SourceAbiTu | None:
        if not key:
            return None
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt/half-written cache entry is a miss, never a failure.
            return None
        return SourceAbiTu.from_dict(data)

    def put(self, key: str | None, tu: SourceAbiTu) -> None:
        if not key:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(key).with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(tu.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        # Atomic publish so a concurrent reader never sees a partial file.
        tmp.replace(self._path(key))


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
        build, scope=scope, changed_paths=changed_paths, target_id=target_id
    )
    tus: list[SourceAbiTu] = []
    diagnostics: list[str] = []
    for cu in units:
        key = (
            compute_tu_cache_key(
                extractor_name=getattr(extractor, "name", "source"),
                extractor_version=_extractor_version(extractor),
                compile_unit=cu,
                public_header_roots=roots,
            )
            if cache is not None
            else None
        )
        tu = cache.get(key) if cache is not None else None
        if tu is None:
            try:
                tu = extractor.extract(
                    cu, public_header_roots=roots, target_id=target_id
                )
            except SourceExtractionError as exc:
                diagnostics.append(f"{cu.source or cu.id}: {exc}")
                continue
            if cache is not None:
                cache.put(key, tu)
        tus.append(tu)

    surface = link_source_abi(
        tus,
        exported_symbols=exported_symbols,
        library=library,
        target_id=target_id,
        forced_public=forced_public,
    )
    surface.coverage["replay_scope"] = scope
    surface.coverage["compile_units_selected"] = len(units)
    surface.coverage["compile_units_parsed"] = len(tus)
    surface.coverage["extractor_failures"] = len(diagnostics)
    return surface, diagnostics


def _extractor_version(extractor: SourceAbiExtractor) -> str:
    """Pull a version string off an extractor for the cache key, if it exposes one."""
    return str(getattr(extractor, "version", "") or "")
