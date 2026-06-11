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

"""Compile-unit include graph for the L5 graph (ADR-031 D3, phase 7).

Adds ``COMPILE_UNIT_INCLUDES_FILE`` edges from compiler depfiles (``-M``/``-MM``
output) — the ADR-029 D3 / ADR-031 D3 source for "compile unit → include
edges". The depfile *parser* is a pure function exercised by unit tests; the
live ``clang -MM`` invocation is integration-only and degrades gracefully, like
the L4 source extractors and the call-graph extractor.
"""
from __future__ import annotations

import re
import shutil
import subprocess  # noqa: S404 - include extraction shells out to clang (never shell=True)
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .source_graph import (
    CONF_HIGH,
    GraphEdge,
    GraphNode,
    _header_node_id,
    _source_node_id,
)

if TYPE_CHECKING:
    from .build_evidence import BuildEvidence
    from .source_graph import SourceGraphSummary


def parse_depfile(text: str) -> list[str]:
    """Parse a make-style depfile (``clang -MM`` output) into prerequisite paths.

    A depfile looks like ``foo.o: foo.cpp a.h \\<newline>  b.h``. The target
    (everything up to the first unescaped ``:``) is dropped; the remaining
    whitespace-separated tokens — with line-continuation backslashes removed —
    are the included files. Returns a de-duplicated, order-preserving list.
    """
    # Join line continuations, then split off the make target before the ':'.
    joined = text.replace("\\\n", " ").replace("\\\r\n", " ")
    out: list[str] = []
    seen: set[str] = set()
    for line in joined.splitlines():
        # Split on the rule colon — the first ':' followed by whitespace or
        # end-of-string — so a Windows drive-letter prefix (``C:\foo.o:``) is
        # not mistaken for the target separator.
        m = re.search(r":(?=\s|$)", line)
        if m is None:
            continue
        prereqs = line[m.end():]
        for tok in prereqs.split():
            tok = tok.strip()
            if tok and tok != "\\" and tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out


def augment_graph_with_includes(
    graph: SourceGraphSummary, includes: dict[str, list[str]]
) -> int:
    """Fold ``{compile_unit_id: [included_path, ...]}`` into *graph* (D3).

    Each included path reuses an existing ``header://``/``source://`` node when
    one matches (so a public header included by a TU links to the very node a
    target exposes), else a generic ``file`` node is created. Returns the number
    of ``COMPILE_UNIT_INCLUDES_FILE`` edges added.
    """
    added = 0
    for cu_id, paths in includes.items():
        for path in paths:
            if not path:
                continue
            # Prefer linking to a header/source node the rest of the graph
            # already knows about so include-graph drift lines up with the
            # public-header set; otherwise materialize a plain file node.
            for candidate in (_header_node_id(path), _source_node_id(path)):
                if graph.has_node(candidate):
                    node_id = candidate
                    break
            else:
                node_id = f"file://{path}"
                graph.add_node(GraphNode(
                    id=node_id, kind="file", label=path,
                    provenance="include_graph", confidence=CONF_HIGH,
                ))
            before = len(graph.edges)
            graph.add_edge(GraphEdge(
                src=cu_id, dst=node_id, kind="COMPILE_UNIT_INCLUDES_FILE",
                provenance="include_graph", confidence=CONF_HIGH,
            ))
            added += len(graph.edges) - before
    return added


@dataclass
class ClangIncludeExtractor:
    """Run ``clang -MM`` to recover a TU's included files (integration only).

    Compiler-dependent and side-effecting: a missing ``clang`` or a failure
    records a diagnostic and yields ``{}`` so collection never aborts.
    """

    clang_bin: str = "clang++"
    diagnostics: list[str] = field(default_factory=list)

    def available(self) -> bool:
        return shutil.which(self.clang_bin) is not None

    def extract_from_build(self, build: BuildEvidence) -> dict[str, list[str]]:
        """Return ``{compile_unit_id: [included path, ...]}`` for every TU."""
        if not self.available():
            self.diagnostics.append(f"{self.clang_bin} not found in PATH")
            return {}
        out: dict[str, list[str]] = {}
        for cu in build.compile_units:
            if not cu.source:
                continue
            argv = list(cu.argv) if cu.argv else [cu.source]
            cmd = [self.clang_bin, "-MM", *argv]
            try:
                proc = subprocess.run(  # noqa: S603 - fixed argv, never shell=True
                    cmd, cwd=cu.directory or None, capture_output=True,
                    text=True, timeout=120, check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                self.diagnostics.append(f"clang -MM failed for {cu.id}: {exc}")
                continue
            if proc.stdout.strip():
                out[cu.id] = parse_depfile(proc.stdout)
        return out
