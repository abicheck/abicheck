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

"""Ninja adapter (ADR-029 D5).

Uses Ninja's own ``-t`` tools rather than parsing ``.ninja`` syntax. ``-t
compdb`` (run with no rule arguments and filtered to compiler invocations, per
D5) yields a compile_commands.json-format database that normalizes through the
same path as :class:`CompileDbAdapter`. ``-t graph`` gives an approximate
dependency graph.

Running ``ninja -t`` is a *query* of an existing build tree (allowed by default
under ADR-028 D6), not a build. To stay testable on the fast lane and to
support hermetic CI, the adapter also accepts **pre-captured** tool output:
pass ``compdb`` / ``graph`` as JSON/DOT text or a file path instead of letting
it shell out.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ...build_context import _extract_flags
from ..build_evidence import BuildEvidence, CompileUnit, Generator
from ..redaction import DEFAULT_REDACTION, RedactionPolicy
from .base import (
    compile_unit_id,
    derive_build_options,
    detect_language,
    extract_abi_relevant_flags,
)


class NinjaAdapter:
    """Normalize Ninja ``-t`` tool output into :class:`BuildEvidence`."""

    name = "ninja"

    def __init__(
        self,
        build_dir: Path | str | None = None,
        *,
        compdb: str | Path | None = None,
        graph: str | Path | None = None,
        allow_query: bool = True,
        redaction: RedactionPolicy | None = None,
    ) -> None:
        self.build_dir = Path(build_dir) if build_dir is not None else None
        self._compdb = compdb
        self._graph = graph
        self.allow_query = allow_query
        self.redaction = redaction or DEFAULT_REDACTION

    def collect(self) -> BuildEvidence:
        ev = BuildEvidence()
        ev.generators.append(Generator(kind="ninja"))

        compdb_text = self._resolve_compdb(ev)
        if compdb_text is None:
            return ev
        try:
            entries = json.loads(compdb_text)
        except json.JSONDecodeError as exc:
            ev.diagnostics.append(f"ninja: could not parse compdb output: {exc}")
            return ev
        if not isinstance(entries, list):
            ev.diagnostics.append("ninja: compdb output was not a JSON array")
            return ev

        for raw in entries:
            cu = self._compile_unit(raw)
            if cu is not None:
                ev.compile_units.append(cu)
        # Project per-unit ABI flags into diffable build options (same as the
        # compile-DB adapter) so a Ninja-only pack still reports flag drift.
        ev.build_options = derive_build_options(ev.compile_units)

        graph_text = self._resolve_graph(ev)
        if graph_text:
            ev.diagnostics.append(
                f"ninja: dependency graph captured ({len(graph_text.splitlines())} edges/nodes)"
            )
        return ev

    # -- compdb -------------------------------------------------------------

    def _resolve_compdb(self, ev: BuildEvidence) -> str | None:
        text = _as_text(self._compdb)
        if text is not None:
            return text
        if self.build_dir is not None and self.allow_query:
            return self._run_ninja_tool(["-t", "compdb"], ev)
        ev.diagnostics.append("ninja: no compdb provided and live query disabled")
        return None

    def _resolve_graph(self, ev: BuildEvidence) -> str | None:
        text = _as_text(self._graph)
        if text is not None:
            return text
        if self.build_dir is not None and self.allow_query:
            return self._run_ninja_tool(["-t", "graph"], ev)
        return None

    def _run_ninja_tool(self, tool_args: list[str], ev: BuildEvidence) -> str | None:
        ninja = shutil.which("ninja")
        if ninja is None or self.build_dir is None:
            ev.diagnostics.append("ninja: executable not found on PATH; cannot query")
            return None
        cmd = [ninja, "-C", str(self.build_dir), *tool_args]
        try:
            # A query of an existing build (ADR-028 D6) — never a build action.
            proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                cmd, capture_output=True, text=True, timeout=120, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            ev.diagnostics.append(f"ninja: {' '.join(tool_args)} failed: {exc}")
            return None
        if proc.returncode != 0:
            ev.diagnostics.append(
                f"ninja: {' '.join(tool_args)} exited {proc.returncode}: {proc.stderr.strip()[:200]}"
            )
            return None
        return proc.stdout

    # -- normalization ------------------------------------------------------

    def _compile_unit(self, raw: object) -> CompileUnit | None:
        if not isinstance(raw, dict):
            return None
        directory = Path(str(raw.get("directory", ".")))
        source = str(raw.get("file", ""))
        if not source:
            return None
        argv = _argv_of(raw)
        # D5: -t compdb with no rule args dumps every build statement; keep only
        # entries that look like compiler invocations (have a recognized source).
        if not detect_language(source):
            return None
        ctx = _extract_flags(argv, directory)
        red_argv = self.redaction.argv(argv)
        red_source = self.redaction.path(source)
        return CompileUnit(
            id=compile_unit_id(red_source, red_argv, str(raw.get("output", ""))),
            source=red_source,
            output=self.redaction.path(str(raw.get("output", ""))),
            directory=self.redaction.path(str(directory)),
            argv=red_argv,
            language=detect_language(source),
            standard=ctx.language_standard or "",
            defines={k: self.redaction.define_value(k, v or "") for k, v in ctx.defines.items()},
            undefines=sorted(ctx.undefines),
            include_paths=[self.redaction.path(str(p)) for p in ctx.include_paths],
            system_include_paths=[self.redaction.path(str(p)) for p in ctx.system_includes],
            sysroot=self.redaction.path(str(ctx.sysroot)) if ctx.sysroot else None,
            target_triple=ctx.target_triple or "",
            abi_relevant_flags=[self.redaction.arg(f) for f in extract_abi_relevant_flags(argv)],
        )


def _argv_of(raw: dict[str, object]) -> list[str]:
    import os
    import shlex

    args = raw.get("arguments")
    if isinstance(args, list):
        return [str(a) for a in args]
    command = raw.get("command")
    if isinstance(command, str):
        return shlex.split(command, posix=os.name != "nt")
    return []


def _as_text(value: str | Path | None) -> str | None:
    """Return text content for a value that may be inline text or a file path."""
    if value is None:
        return None
    if isinstance(value, Path):
        return value.read_text(encoding="utf-8") if value.is_file() else None
    # A str: treat as a file path if it points at a real file, else inline text.
    candidate = Path(value)
    try:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    except OSError:
        pass
    return value
