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

"""TU -> link-unit -> DSO source-evidence attribution (ADR-053, G30 P2).

Given a :class:`~.build_evidence.BuildEvidence`, answers "which output
binary/binaries does this translation unit's compiled code end up in?" —
the piece ADR-047 §9's "safe model now, full model later" split deferred:
a build-wide, unattributed evidence pack may only feed build-wide audits and
per-target header-depth checks; this module is what lets a caller instead
*prove*, per source file, which target(s) it actually belongs to, so a
build-wide pack can be safely and automatically projected onto the correct
subset of targets (ADR-053 D1).

Two independent, non-exclusive signal channels feed one combined result:

- **Target-graph channel** — a real target graph (CMake File API, Bazel's
  target-level facts) already states, per target, which of its own sources
  compile into it (:attr:`~.build_evidence.Target.source_files`). No linker
  command parsing needed; this channel folds ``OBJECT_LIBRARY``/
  ``STATIC_LIBRARY`` dependency sources transitively into every target that
  (transitively) depends on them, since those kinds are absorbed into their
  dependents' own link output rather than shipping their own.
- **Link-unit-graph channel** — real linker-invocation identity
  (:attr:`~.build_evidence.LinkUnit.inputs`/``.output``), the only signal
  available for build systems with no semantic target graph (Make). Walks
  each terminal (``shared_library``/``executable``) link unit's inputs
  backward, matching object-file paths against
  :attr:`~.build_evidence.CompileUnit.output`, resolving nested static-library
  link units transitively.

A source attributed by neither channel is *unknown*, not "unattributed" in
the sense of "belongs to nothing" — callers (:func:`~.build_output.
validate_build_output`, :func:`~.inputs_pack.ingest_inputs_pack`) must treat
an unknown source as unresolved and exclude it from any per-target claim,
never assume it is safe to include.

Pure: reads only the in-memory :class:`BuildEvidence` already collected by an
adapter; never touches the filesystem or a subprocess.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from .build_evidence import TargetKind

if TYPE_CHECKING:
    from .build_evidence import BuildEvidence

#: Target kinds whose own sources are absorbed into a dependent's link
#: output rather than shipping as their own artifact — folded transitively
#: into every target that depends on them (D1's target-graph channel).
_ABSORBED_KINDS = frozenset({TargetKind.OBJECT_LIBRARY, TargetKind.STATIC_LIBRARY})

#: Link-unit kinds that are a real, independently-shipped output binary —
#: the only kinds the link-unit-graph channel treats as an attribution
#: target. A static-library link unit is an intermediate, resolved
#: transitively as another link unit's input, never a target in its own
#: right here (mirrors ``_ABSORBED_KINDS``' target-graph-channel rule).
_TERMINAL_LINK_KINDS = frozenset({"shared_library", "executable"})


def normalize_source_path(path: str) -> str:
    """Normalize a source/object path for cross-evidence matching.

    ``PurePosixPath`` (not ``Path``) so normalization is platform-independent
    — the evidence being compared may have been collected on a different OS
    than this process runs on. Strips a leading ``./`` only; deliberately
    does NOT resolve ``..`` or make the path absolute, since doing so would
    require knowing a real filesystem root this pure function has no access
    to and no need for (every path compared here already went through the
    same adapter-relative convention).
    """
    return PurePosixPath(path.replace("\\", "/")).as_posix().removeprefix("./")


def attribute_sources_to_targets(evidence: BuildEvidence) -> dict[str, frozenset[str]]:
    """Compute ``{normalized_source_path: {attribution_identity, ...}}``.

    Each attribution identity is one of:

    - ``f"target://{target.id_suffix}"`` — a real, named target from the
      target-graph channel or a link unit whose ``target_id`` is set
      (already ``target://``-prefixed by every adapter that sets it, per
      ADR-029 D2's convention — used verbatim here, not re-prefixed).
    - ``f"output://{basename}"`` — the link-unit-graph channel's fallback
      identity when a link unit carries no ``target_id`` at all (Make has no
      semantic target concept to name), keyed by the output file's basename
      rather than its full path: a build-wide evidence pack's recorded
      object paths and a project's own ``build-output.json``-declared binary
      path routinely differ in directory prefix (different working
      directories, staged-vs-build-tree layout) even when they name the same
      real artifact, so basename is the only robust common ground — accepted
      as reduced-confidence by the same posture the Make adapter's own
      compile-unit scraping already carries.

    A source seen by neither channel is simply absent from the returned
    dict — never present with an empty set — so ``dict.get(src, frozenset())``
    and "not in the dict" are equivalent ways to detect "unknown."
    """
    result: dict[str, set[str]] = {}
    _merge_channel(result, _attribute_via_target_graph(evidence))
    _merge_channel(result, _attribute_via_link_units(evidence))
    return {src: frozenset(identities) for src, identities in result.items()}


def _merge_channel(dst: dict[str, set[str]], channel: dict[str, set[str]]) -> None:
    for src, identities in channel.items():
        dst.setdefault(src, set()).update(identities)


def _attribute_via_target_graph(evidence: BuildEvidence) -> dict[str, set[str]]:
    by_id = {t.id: t for t in evidence.targets}
    result: dict[str, set[str]] = {}
    for target in evidence.targets:
        if not target.id:
            continue
        sources = set(target.source_files)
        # Transitively fold every OBJECT_LIBRARY/STATIC_LIBRARY dependency's
        # own sources in — those kinds ship no artifact of their own, their
        # object code is absorbed into whatever depends on them. A
        # SHARED_LIBRARY/EXECUTABLE dependency is a hard stop: its objects
        # live in its own output, never copied into a dependent's.
        visited: set[str] = set()
        stack = list(target.dependencies)
        while stack:
            dep_id = stack.pop()
            if dep_id in visited:
                continue
            visited.add(dep_id)
            dep = by_id.get(dep_id)
            if dep is None or dep.kind not in _ABSORBED_KINDS:
                continue
            sources.update(dep.source_files)
            stack.extend(dep.dependencies)
        identity = target.id if target.id.startswith("target://") else f"target://{target.id}"
        for src in sources:
            if not src:
                continue
            result.setdefault(normalize_source_path(src), set()).add(identity)
    return result


def _attribute_via_link_units(evidence: BuildEvidence) -> dict[str, set[str]]:
    output_to_source = {
        normalize_source_path(cu.output): cu.source for cu in evidence.compile_units if cu.output
    }
    link_by_output = {
        normalize_source_path(lu.output): lu for lu in evidence.link_units if lu.output
    }
    result: dict[str, set[str]] = {}
    for lu in evidence.link_units:
        if lu.kind not in _TERMINAL_LINK_KINDS or not lu.output:
            continue
        identity = (
            lu.target_id
            if lu.target_id
            else f"output://{PurePosixPath(normalize_source_path(lu.output)).name}"
        )
        sources: set[str] = set()
        visited_links: set[str] = set()
        stack = list(lu.inputs)
        while stack:
            raw = stack.pop()
            inp = normalize_source_path(raw)
            source = output_to_source.get(inp)
            if source:
                sources.add(source)
                continue
            nested = link_by_output.get(inp)
            if nested is not None and inp not in visited_links:
                visited_links.add(inp)
                stack.extend(nested.inputs)
        for src in sources:
            result.setdefault(normalize_source_path(src), set()).add(identity)
    return result
