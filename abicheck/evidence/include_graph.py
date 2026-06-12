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
live ``clang -M`` invocation is integration-only and degrades gracefully, like
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


#: Flags (with their value argument) that must be stripped before re-driving a
#: recorded compile command as ``clang -MM``: the compile action, the object
#: output, and any existing dependency-generation options.
_DEPFILE_DROP_WITH_VALUE = frozenset({"-o", "--output", "-MF", "-MT", "-MQ", "-MJ", "-Xclang"})
_DEPFILE_DROP_FLAG = frozenset({"-c", "-MD", "-MMD", "-MM", "-M", "-MG", "-MP", "-pipe"})


def depfile_args_from_argv(argv: list[str]) -> list[str]:
    """Strip a recorded compile argv down to the args usable after ``clang -MM``.

    A compile database stores the full command — possibly launcher-wrapped, like
    ``ccache clang++ -c foo.cpp -o foo.o -I…`` — whose leading tokens are a
    compiler launcher and the *compiler executable*. Re-driving that as
    ``clang++ -MM ccache clang++ -c foo.cpp …`` makes clang treat the leftover
    launcher/compiler tokens as input files and emit no usable depfile (Codex
    review). Strip leading ``ccache``/``sccache``/… launchers and the compiler
    token, drop the ``-c`` compile action, the ``-o``/``-MF``/… outputs and any
    pre-existing dependency flags, keeping the source plus the ABI-relevant
    ``-I``/``-D``/``-std`` context that decides what is included.
    """
    if not argv:
        return []
    # Reuse the source extractors' launcher-stripping so a ccache/sccache-wrapped
    # command leaves only the compiler token to drop next.
    from .source_extractors._argv import strip_launchers

    unwrapped = strip_launchers(list(argv))
    # After the launcher, the first token is the compiler driver (an executable
    # path, not a flag); drop it. An argv that is already only flags keeps them.
    args = (
        unwrapped[1:]
        if unwrapped and not unwrapped[0].startswith("-")
        else list(unwrapped)
    )
    out: list[str] = []
    skip_next = False
    for tok in args:
        if skip_next:
            skip_next = False
            continue
        if tok in _DEPFILE_DROP_WITH_VALUE:
            skip_next = True
            continue
        # `-oFOO` / `-MFfoo.d` glued forms and the GCC long `--output=foo.o`
        # spelling (clang -M with --output=… writes the depfile to that file and
        # leaves stdout empty, losing the include entry — Codex review).
        if tok.startswith("--output="):
            continue
        if any(tok.startswith(f) and tok != f for f in ("-o", "-MF", "-MT", "-MQ")):
            continue
        if tok in _DEPFILE_DROP_FLAG:
            continue
        out.append(tok)
    return out


def _lang_flag(language: str) -> list[str]:
    """``-x <lang>`` forcing a compile unit's language for the depfile pass.

    Preserves the compile command's language so a C TU replayed through the
    ``clang++`` driver is parsed as C, not C++ (Codex review). An unknown
    language adds no flag, leaving the driver/extension to decide.
    """
    lang = language.strip().upper()
    if lang in ("C",):
        return ["-x", "c"]
    if lang in ("CXX", "C++", "CPP", "CC"):
        return ["-x", "c++"]
    return []


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
    """Run ``clang -M`` to recover a TU's included files (integration only).

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
        # The redaction policy (ADR-032 D7) persists argv/cwd with the home dir
        # rewritten to `~`; subprocess does not expand `~`, so a depfile pass over
        # the redacted values would fail and silently degrade replay scoping
        # (Codex review). Un-redact for the run only, exactly as the clang source
        # extractor does.
        from .source_extractors._argv import unredact_home

        out: dict[str, list[str]] = {}
        for cu in build.compile_units:
            if not cu.source:
                continue
            argv = depfile_args_from_argv(cu.argv) if cu.argv else [cu.source]
            if not argv:
                argv = [cu.source]
            # `-M` (not `-MM`) so depfiles include *system*-classified headers: a
            # project whose public headers are reached via `-isystem` (installed
            # / SYSTEM include dirs) would otherwise be omitted, and the `changed`
            # scope, treating a complete graph as authoritative, would select no
            # TU for edits to them (Codex review). `-x <lang>` forces the compile
            # unit's real language so a `.c` TU replayed through the clang++ driver
            # is not parsed as C++ (wrong __cplusplus / language-conditioned
            # includes) (Codex review).
            cmd = [
                self.clang_bin, "-M", *_lang_flag(cu.language),
                *(unredact_home(a) for a in argv),
            ]
            cwd = unredact_home(cu.directory) if cu.directory else None
            try:
                proc = subprocess.run(  # noqa: S603 - fixed argv, never shell=True
                    cmd, cwd=cwd or None, capture_output=True,
                    text=True, timeout=120, check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                self.diagnostics.append(f"clang -M failed for {cu.id}: {exc}")
                continue
            if proc.stdout.strip():
                out[cu.id] = parse_depfile(proc.stdout)
        return out
