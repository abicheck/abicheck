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
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .. import deadline
from ..model.graph_facts import CONF_HIGH, GraphEdge, GraphNode
from ..model.source_graph import _header_node_id, _source_node_id

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary
    from .build_evidence import BuildEvidence


#: Flags (with their value argument) that must be stripped before re-driving a
#: recorded compile command as ``clang -MM``: the compile action, the object
#: output, and any existing dependency-generation options.
_DEPFILE_DROP_WITH_VALUE = frozenset({"-o", "--output", "-MF", "-MT", "-MQ", "-MJ"})
_DEPFILE_DROP_FLAG = frozenset(
    {
        "-c",
        "-MD",
        "-MMD",
        "-MM",
        "-M",
        "-MG",
        "-MP",
        "-pipe",
        "-fno-strict-overflow",
    }
)
_DEPFILE_DROP_PREFIXES = (
    "-fdiagnostics-color",
    "-fno-canonical-system-headers",
)
# Clang driver/cc1 escape hatches that can load arbitrary native code (for
# example ``-Xclang -load -Xclang ./evil.so`` or ``-cc1 -load ./evil.so``).
# Compile databases may come from untrusted PR artifacts, so the depfile replay
# must preserve only compile-context flags and must never forward plugin/pass
# loading controls to clang.
_DEPFILE_UNSAFE_WITH_VALUE = frozenset(
    {
        "-Xclang",
        "-load",
        "-plugin",
        "-add-plugin",
        "-fplugin",
        "-fpass-plugin",
        "-mllvm",
    }
)
_DEPFILE_UNSAFE_FLAG = frozenset({"-cc1"})
_DEPFILE_UNSAFE_PREFIXES = (
    "-Xclang=",
    "-load=",
    "-plugin=",
    "-add-plugin=",
    "-fplugin=",
    "-fpass-plugin=",
    "-mllvm=",
    "--config=",
)

# Clang options that can create or overwrite files even during preprocessing.
# Build evidence can be supplied by untrusted PR artifacts, so replay must not
# forward output-producing instrumentation/cache/diagnostic controls.
_DEPFILE_OUTPUT_WITH_VALUE = frozenset(
    {
        "-ftime-trace",
        "-serialize-diagnostic-file",
        "-fmodules-cache-path",
    }
)
_DEPFILE_OUTPUT_FLAG = frozenset(
    {
        "-save-temps",
        "--save-temps",
    }
)
_DEPFILE_OUTPUT_PREFIXES = (
    "-ftime-trace=",
    "-serialize-diagnostic-file=",
    "-fmodules-cache-path=",
    "-save-temps=",
    "--save-temps=",
)


def _expand_argv_response_files(
    args: list[str],
    unwrapped: list[str],
    directory: str | None,
    trusted_root: str | None,
) -> list[str]:
    """Expand any remaining ``@response-file`` token in *args*, or leave it be.

    Only expands when both *directory* (the compile unit's own working
    directory, used to resolve a *relative* ``@file`` token) and *trusted_root*
    (an independently-sourced, verified-real directory the expansion must stay
    under) are given -- see :func:`depfile_args_from_argv`'s docstring for why
    *trusted_root* must not simply be *directory*.
    """
    if (
        directory is None
        or trusted_root is None
        or not any(a.startswith("@") and len(a) > 1 for a in args)
    ):
        return args

    from pathlib import Path

    from ..build_context import (
        _expand_response_files,
        _is_cl_style_driver,
        _safe_resolve,
    )
    from .source_extractors._argv import unredact_home

    # Response-file quoting depends on the driver actually invoked, not the
    # host OS (Codex review) -- unwrapped[0] is still the compiler token here
    # (the flags-only branch in the caller has not stripped it from *unwrapped*).
    cl_style = (
        bool(unwrapped)
        and not unwrapped[0].startswith("-")
        and _is_cl_style_driver(unredact_home(unwrapped[0]))
    )

    # unredact_home() first: a persisted CompileUnit.directory (and any
    # individual @response-file argument, e.g. "@~/build/args.rsp") may carry
    # RedactionPolicy's "~" home-dir placeholder, which bare Path() would treat
    # as a literal, non-existent relative component (Path("~/build") stays
    # under the process cwd and is never is_absolute(), so an absolute redacted
    # @file token would even be wrongly joined onto cu_dir instead of resolved
    # on its own) -- silently failing every response-file expansion for a
    # redacted compile unit otherwise (Codex review, two rounds). Unredacting
    # an already-real (non-redacted) token is a harmless no-op.
    cu_dir = Path(unredact_home(directory))
    root_dir = Path(unredact_home(trusted_root))
    return _expand_response_files(
        [unredact_home(a) for a in args],
        cu_dir,
        _safe_resolve(root_dir) or root_dir,
        cl_style=cl_style,
    )


def _depfile_token_takes_value(tok: str) -> bool:
    """True for a flag whose *next* argv token is its value and must go too."""
    return tok == "--config" or tok in (
        _DEPFILE_DROP_WITH_VALUE | _DEPFILE_UNSAFE_WITH_VALUE | _DEPFILE_OUTPUT_WITH_VALUE
    )


def _depfile_token_dropped(tok: str) -> bool:
    """True for a standalone token that must not reach ``clang -MM``.

    Covers unsafe/output flags and their prefixes, an unexpanded ``@file``, the
    glued ``-oFOO``/``-MFfoo.d`` forms and the GCC long ``--output=foo.o``
    spelling (clang ``-M`` with ``--output=`` writes the depfile to that file
    and leaves stdout empty, losing the include entry -- Codex review), and
    warning/diagnostic flags: those do not affect the include closure, but can
    turn a harmless clang depfile-replay warning into a hard failure when the
    original Bazel/GCC action recorded ``-Werror``.
    """
    if tok.startswith("@"):
        return True
    if tok in _DEPFILE_UNSAFE_FLAG or tok in _DEPFILE_OUTPUT_FLAG:
        return True
    if tok.startswith(_DEPFILE_UNSAFE_PREFIXES) or tok.startswith(
        _DEPFILE_OUTPUT_PREFIXES
    ):
        return True
    if tok.startswith("--output="):
        return True
    if any(tok.startswith(f) and tok != f for f in ("-o", "-MF", "-MT", "-MQ", "-MJ")):
        return True
    if tok in _DEPFILE_DROP_FLAG:
        return True
    return tok.startswith(_DEPFILE_DROP_PREFIXES) or (
        tok.startswith("-W") and not tok.startswith("-Wp,")
    )


def depfile_args_from_argv(
    argv: list[str],
    directory: str | None = None,
    *,
    trusted_root: str | None = None,
) -> list[str]:
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

    A recorded argv may itself still carry an unexpanded GNU
    ``@response-file`` (make-based build systems commonly spell a long
    include-dir list that way) — this is normally already expanded upstream
    by :func:`abicheck.build_context._expand_response_files` when the argv
    came from a ``compile_commands.json``/make-transcript adapter, but a
    build-emitted ``abicheck_inputs/`` pack or another future adapter could
    still hand this function a raw, unexpanded one. When both *directory*
    (the compile unit's own working directory, e.g. ``CompileUnit.directory``,
    used to resolve a *relative* ``@file`` token) and *trusted_root* (an
    independently-sourced, verified-real directory the expansion must stay
    under) are given, such a token is expanded inline before the filtering
    loop below — not merely dropped — so its ``-I``/``-D`` flags are not
    silently lost. Expanding *before* filtering, rather than after, is
    deliberate: it keeps the loop's existing unsafe-flag denylist
    (``-Xclang``, ``-load``, ``-cc1``, ``--config``, …) as the single choke
    point a flag smuggled inside a response file still has to pass, instead
    of opening a second, unfiltered path to reach ``clang -MM``.

    *trusted_root* must NOT simply be *directory* itself: for a ``CompileUnit``
    sourced from an untrusted build pack (e.g. a third-party
    ``abicheck_inputs/`` drop), ``directory`` is attacker-controlled free-form
    content, not a verified path — jailing an expansion to a root the same
    untrusted input also chose is not a jail at all (an attacker could set
    ``directory`` to ``/home/runner`` and reference ``@.ssh/config``). Without
    an independently-trusted *trusted_root*, the ``@file`` token is dropped as
    before rather than expanded against a self-chosen root (Codex review).
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
    args = _expand_argv_response_files(args, unwrapped, directory, trusted_root)
    out: list[str] = []
    skip_next = False
    for tok in args:
        if skip_next:
            skip_next = False
            continue
        if _depfile_token_takes_value(tok):
            skip_next = True
            continue
        if _depfile_token_dropped(tok):
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
        prereqs = line[m.end() :]
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
                graph.add_node(
                    GraphNode(
                        id=node_id,
                        kind="file",
                        label=path,
                        provenance="include_graph",
                        confidence=CONF_HIGH,
                    )
                )
            before = len(graph.edges)
            graph.add_edge(
                GraphEdge(
                    src=cu_id,
                    dst=node_id,
                    kind="COMPILE_UNIT_INCLUDES_FILE",
                    provenance="include_graph",
                    confidence=CONF_HIGH,
                )
            )
            added += len(graph.edges) - before
    return added


def include_map_from_recorded_inputs(build: BuildEvidence) -> dict[str, list[str]]:
    """Build a per-CU include map from recorded compile action inputs.

    Bazel aquery already carries the action input depsets, including headers,
    and those paths are available without a live execroot. Prefer this when
    adapters recorded it; fall back to compiler depfile replay for build systems
    that only expose argv.
    """
    out: dict[str, list[str]] = {}
    for cu in build.compile_units:
        if cu.input_files:
            out[cu.id] = list(cu.input_files)
    return out


@dataclass
class ClangIncludeExtractor:
    """Run ``clang -M`` to recover a TU's included files (integration only).

    Compiler-dependent and side-effecting: a missing ``clang`` or a failure
    records a diagnostic and yields ``{}`` so collection never aborts.
    """

    clang_bin: str = "clang++"
    diagnostics: list[str] = field(default_factory=list)
    diagnostics_limit: int = 20
    max_compile_units: int = 256
    aggregate_timeout_s: float = 30.0
    per_unit_timeout_s: float = 120.0

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
        failures = 0
        attempted = 0
        aggregate_deadline = time.monotonic() + self.aggregate_timeout_s
        for cu in build.compile_units:
            if not cu.source:
                continue
            if attempted >= self.max_compile_units:
                self.diagnostics.append(
                    "clang -M include-map budget exhausted: "
                    f"stopped after {attempted} compile units"
                )
                break
            remaining = aggregate_deadline - time.monotonic()
            if remaining <= 0:
                self.diagnostics.append(
                    "clang -M include-map time budget exhausted: "
                    f"stopped after {attempted} compile units"
                )
                break
            attempted += 1
            argv = (
                depfile_args_from_argv(cu.argv, directory=cu.directory)
                if cu.argv
                else [cu.source]
            )
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
                self.clang_bin,
                "-M",
                *_lang_flag(cu.language),
                *(unredact_home(a) for a in argv),
            ]
            cwd = unredact_home(cu.directory) if cu.directory else None
            per_call_timeout = min(self.per_unit_timeout_s, remaining)
            scan_remaining = deadline.remaining()
            # Whether the OUTER scan --budget (not this extractor's own
            # per-unit/aggregate cap) is what will actually bind the nested
            # scope below — decides how a DeadlineExceeded from it is
            # classified (Codex review, PR #591, round 3).
            bound_by_scan_deadline = (
                scan_remaining is not None and scan_remaining < per_call_timeout
            )
            if scan_remaining is not None:
                # run_bounded() honors an active outer deadline verbatim (not
                # min(timeout, left) — a generous --budget must not get
                # silently re-capped), so a bare `timeout=` here would let a
                # hung call eat the *whole* remaining scan budget instead of
                # this extractor's own per-unit/aggregate ceiling. Nest a
                # narrower scope so this call is bound by whichever is
                # tighter (Codex review, PR #591).
                per_call_timeout = min(per_call_timeout, scan_remaining)
            try:
                # Process-group-safe on timeout, same as the L2/L4/L5 clang calls.
                with deadline.deadline_scope(per_call_timeout):
                    proc = deadline.run_bounded(  # noqa: S603 - fixed argv, never shell=True
                        cmd,
                        cwd=cwd or None,
                        capture_output=True,
                        text=True,
                        timeout=per_call_timeout,
                    )
            except deadline.DeadlineExceeded as exc:
                if not bound_by_scan_deadline:
                    # The entry-time snapshot said this extractor's OWN
                    # per-unit/aggregate cap was binding, not the outer scan
                    # deadline — but run_bounded's own escalation (SIGTERM
                    # -> grace -> SIGKILL, plus a fixed 5s pipe-drain) can
                    # push real elapsed time past that snapshot, so the
                    # outer deadline can still be exhausted by now even
                    # though it wasn't at entry. Re-check it directly
                    # instead of trusting the stale snapshot alone (Codex
                    # review, PR #591, round 3).
                    try:
                        deadline.check()
                    except deadline.DeadlineExceeded:
                        pass
                    else:
                        # Only this extractor's own per-unit/aggregate cap
                        # expired (no active --budget, or one with plenty
                        # left) — an ordinary per-CU timeout, not a
                        # scan-budget overflow. Degrade like any other
                        # single-CU failure instead of discarding include
                        # maps for every remaining compile unit.
                        self.diagnostics.append(
                            f"clang -M timed out for {cu.id}: {exc}"
                        )
                        continue
                self.diagnostics.append(
                    f"scan deadline exceeded during clang -M include-map: {exc}"
                )
                break
            except (OSError, subprocess.SubprocessError) as exc:
                self.diagnostics.append(f"clang -M failed for {cu.id}: {exc}")
                continue
            if proc.stdout.strip():
                out[cu.id] = parse_depfile(proc.stdout)
            elif proc.returncode != 0:
                failures += 1
                if len(self.diagnostics) < self.diagnostics_limit:
                    detail = (proc.stderr or "").strip().splitlines()
                    msg = next(
                        (
                            line
                            for line in detail
                            if "error:" in line or "fatal error:" in line
                        ),
                        detail[0] if detail else f"exit {proc.returncode}",
                    )
                    self.diagnostics.append(f"clang -M failed for {cu.id}: {msg}")
        if failures > self.diagnostics_limit:
            self.diagnostics.append(
                f"clang -M failed for {failures - self.diagnostics_limit} more compile units"
            )
        return out
