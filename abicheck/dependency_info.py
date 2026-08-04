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

"""Transitive ELF dependency resolution into a snapshot's ``DependencyInfo``.

What ``compare --follow-deps`` (and ``run_compare_request``'s
``follow_dependencies``) actually does. A leaf module on purpose: it depends
only on ``binder``/``model``/``resolver``, and both the CLI
(``cli_resolve._populate_dependency_info``) and the service layer
(``service.run_compare_request``) need it — so neither has to import the
other for it. ADR-055 D1's second slice, which gave the typed request
``--follow-deps`` parity, is what made the second caller exist; before that
it lived in ``cli_resolve`` with only CLI callers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from .api_types import CompareRequest, InputSpec
    from .model import AbiSnapshot


def populate_dependency_info(
    snap: AbiSnapshot,
    so_path: Path,
    search_paths: list[Path],
    sysroot: Path | None,
    ld_library_path: str,
) -> None:
    """Resolve transitive deps and store DependencyInfo in the snapshot.

    Moved here from ``cli_resolve`` for ADR-055 D1's second slice: it is what
    ``--follow-deps`` does, ``run_compare_request`` needed the same thing to
    reach parity with the CLI's own resolution, and it depends on nothing but
    leaf modules — so the service layer is its home and the CLI depends on
    the service rather than the reverse. ``cli_resolve._populate_dependency_info``
    remains as a thin alias for ``cli.py``'s existing re-export.
    """
    from .binder import BindingStatus, compute_bindings
    from .model import DependencyInfo
    from .resolver import resolve_dependencies

    graph = resolve_dependencies(
        so_path,
        search_paths=search_paths or None,
        sysroot=sysroot,
        ld_library_path=ld_library_path,
    )
    bindings = compute_bindings(graph)

    summary: dict[str, int] = {}
    for b in bindings:
        summary[b.status.value] = summary.get(b.status.value, 0) + 1

    missing = [
        {"consumer": b.consumer, "symbol": b.symbol, "version": b.version}
        for b in bindings
        if b.status == BindingStatus.MISSING
    ]

    snap.dependency_info = DependencyInfo(
        nodes=[
            {
                "path": str(node.path),
                "soname": node.soname,
                "needed": node.needed,
                "depth": node.depth,
                "resolution_reason": node.resolution_reason,
            }
            for node in sorted(graph.nodes.values(), key=lambda n: (n.depth, n.soname))
        ],
        edges=[
            {"consumer": consumer, "provider": provider}
            for consumer, provider in graph.edges
        ],
        unresolved=[
            {"consumer": consumer, "soname": soname}
            for consumer, soname in graph.unresolved
        ],
        bindings_summary=summary,
        missing_symbols=missing,
    )


def populate_side_dependency_info(
    snap: AbiSnapshot,
    side: InputSpec,
    fmt: str | None,
    search_paths: list[Path],
    ld_library_path: str,
) -> None:
    """Populate one resolved input's transitive ``DependencyInfo``.

    ELF-only, matching the CLI's own ``--follow-deps`` guard:
    ``resolver.resolve_dependencies`` reads ELF ``DT_NEEDED`` entries, so there
    is nothing for it to do on a PE or Mach-O input (it would not merely be
    redundant, it would not work). A non-ELF input is a silent no-op here, the
    same as it is on the CLI.

    Whether to call this at all is the caller's opt-in
    (``CompareRequest``/``DumpRequest``'s ``follow_dependencies``): populating
    it costs a full dependency-graph resolution.
    """
    elf_path = _dependency_source(side, fmt)
    if elf_path is not None:
        populate_dependency_info(snap, elf_path, search_paths, None, ld_library_path)


def populate_pair_dependency_info(
    request: CompareRequest,
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    old_fmt: str | None,
    new_fmt: str | None,
) -> None:
    """Apply ``CompareRequest.follow_dependencies`` to both resolved sides.

    A no-op unless the request opts in — populating this costs a full
    dependency-graph resolution per side, so it stays opt-in for a typed
    caller exactly as ``--follow-deps`` is for the CLI.
    """
    if not request.follow_dependencies:
        return
    search_paths = list(request.dependency_search_paths)
    for snap, side, fmt in ((old, request.old, old_fmt), (new, request.new, new_fmt)):
        populate_side_dependency_info(
            snap, side, fmt, search_paths, request.ld_library_path
        )


def _dependency_source(side: InputSpec, fmt: str | None) -> Path | None:
    """The ELF this side's dependency graph should be read from, or ``None``.

    Not simply ``side.path if fmt == "elf"``. A distribution's ``libfoo.so`` is
    routinely a GNU ld *linker script* pointing at the real ``libfoo.so.1``:
    ``detect_binary_format`` sees a text file and answers ``None``, so a plain
    format check would silently skip the dependency resolution the caller
    explicitly asked for — while ``resolve_input`` had already followed the
    script and produced a perfectly good ELF snapshot (Codex review). Reading
    the *original* path would be no better: ``resolve_dependencies`` needs the
    ELF, not the script that names it.

    So this follows the script the same way ``resolve_input`` does — including
    a *chain* of them, since that function recurses (it calls itself on the
    resolved target with ``follow_linker_scripts`` still set). Following only
    one hop meant a script naming another script produced a snapshot fine
    while the dependency scan silently skipped that side, which is exactly the
    asymmetry this helper exists to remove (Codex review, second round; the
    first round's fix handled a single hop and called it conservative — it was
    just inconsistent).

    Following is skipped entirely for a side that opted out
    (``InputSpec.follow_linker_scripts``) — an MCP request that pinned it off
    to keep its size guard authoritative must not get scripts followed here by
    a side door.

    Cycle-protected: a script naming itself, or a pair naming each other,
    terminates and yields ``None`` rather than looping. (``resolve_input``'s
    own recursion guards only the self-reference case, so a two-script cycle
    would recurse there until Python's stack gives out — real, pre-existing,
    and not this function's to fix.)
    """
    from .binary_utils import detect_binary_format, resolve_linker_script

    if fmt == "elf":
        return side.path
    if not side.follow_linker_scripts:
        return None

    current = side.path
    seen = {current.resolve()}
    while True:
        target, is_script = resolve_linker_script(current)
        if not is_script or target is None:
            return None
        resolved = target.resolve()
        if resolved in seen:
            return None
        seen.add(resolved)
        if detect_binary_format(target) == "elf":
            return target
        current = target
