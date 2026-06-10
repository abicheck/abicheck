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

"""Make adapter (ADR-029 D7).

Make is too flexible to parse semantically with confidence, so it is supported
as a *reduced-confidence* fallback tier:

1. The preferred Make input is an existing ``compile_commands.json`` (produced
   by Bear / compiledb / intercept-build) — fed through
   :class:`CompileDbAdapter` with ``--build-system make``; this module is not
   needed for that path.
2. This adapter is the next tier: it scrapes a **pre-captured** ``make -n`` /
   ``make --trace`` transcript for compiler invocations.

The adapter never *runs* Make. That is deliberate: ``make -n`` is not actually
side-effect free — GNU Make still executes recipe lines prefixed with ``+`` and
evaluates ``$(shell …)`` at makefile-parse time — so running it would violate
the post-build, non-executing contract (ADR-028 D6). The caller runs the dry
run themselves (an explicit, auditable step) and feeds the transcript via
``--make-dry-run``. Compiler wrapper / interception (Bear-style) is a separate
explicit opt-in (ADR-032 D5) and is not implemented here.

The recovered compile units are always **reduced confidence** (a Make dry run
is not an authoritative target graph), recorded via a diagnostic.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path

from ...build_context import _extract_flags
from ..build_evidence import BuildEvidence, CompileUnit, Generator
from ..redaction import DEFAULT_REDACTION, RedactionPolicy
from .base import (
    compile_unit_id,
    derive_build_options,
    detect_language,
    extract_abi_relevant_flags,
    source_from_argv,
)


class MakeAdapter:
    """Scrape a pre-captured ``make -n``/``--trace`` transcript into units."""

    name = "make"

    def __init__(
        self,
        build_dir: Path | str | None = None,
        *,
        dry_run: str | Path | None = None,
        redaction: RedactionPolicy | None = None,
    ) -> None:
        # ``build_dir`` is only a passive directory hint for resolving relative
        # include paths; it is never used to execute Make.
        self.build_dir = Path(build_dir) if build_dir is not None else None
        self._dry_run = dry_run
        self.redaction = redaction or DEFAULT_REDACTION

    def collect(self) -> BuildEvidence:
        ev = BuildEvidence()
        ev.generators.append(Generator(kind="make"))

        text = self._resolve(ev)
        if text is None:
            return ev

        directory = self.build_dir or Path(".")
        for line in text.splitlines():
            cu = self._compile_unit(line, directory)
            if cu is not None:
                ev.compile_units.append(cu)

        if ev.compile_units:
            ev.build_options = derive_build_options(ev.compile_units)
            ev.diagnostics.append(
                f"make: {len(ev.compile_units)} compile units derived from a make "
                "dry-run transcript — reduced confidence (not an authoritative "
                "target graph); prefer a generated compile_commands.json"
            )
        return ev

    # -- input resolution ---------------------------------------------------

    def _resolve(self, ev: BuildEvidence) -> str | None:
        text = _as_text(self._dry_run)
        if text is not None:
            return text
        if self._dry_run is not None:
            ev.diagnostics.append(
                f"make: dry-run input not found or unreadable: "
                f"{self.redaction.path(str(self._dry_run))}"
            )
            return None
        ev.diagnostics.append(
            "make: no dry-run transcript provided (the adapter never runs make; "
            "capture `make -n`/`--trace` output and pass it via --make-dry-run)"
        )
        return None

    # -- normalization ------------------------------------------------------

    def _compile_unit(self, line: str, directory: Path) -> CompileUnit | None:
        argv = _split_recipe(line)
        # Recursive recipes prefix the compile with `cd sub && …`; the source and
        # `-I` paths are then relative to `sub/`, not the parent build dir.
        argv, directory = _consume_cd_prefix(argv, directory)
        # A translation-unit compile is a `-c` (GNU) / `/c` (MSVC, clang-cl)
        # invocation that names a source; link/info/`Entering directory` lines
        # lack one of those and are skipped.
        if "-c" not in argv and "/c" not in argv:
            return None
        source = source_from_argv(argv)
        if not source:
            return None
        ctx = _extract_flags(argv, directory)
        red_argv = self.redaction.argv(argv)
        red_source = self.redaction.path(source)
        return CompileUnit(
            id=compile_unit_id(red_source, red_argv),
            source=red_source,
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


def _consume_cd_prefix(argv: list[str], directory: Path) -> tuple[list[str], Path]:
    """Strip leading ``cd <dir> &&|;`` segments, advancing *directory* into them.

    Handles chained forms like ``cd a && cd b && cc …``. An absolute ``cd``
    target resets the directory; a relative one is joined onto it.
    """
    while len(argv) >= 3 and argv[0] == "cd" and argv[2] in ("&&", ";"):
        sub = Path(argv[1])
        directory = sub if sub.is_absolute() else directory / sub
        argv = argv[3:]
    return argv, directory


def _split_recipe(line: str) -> list[str]:
    """Tokenize one make recipe line, tolerating the usual ``@``/trace noise."""
    stripped = line.strip().lstrip("@+-")  # make recipe prefixes: @ (silent), + (force), - (ignore)
    if not stripped:
        return []
    try:
        return shlex.split(stripped, posix=os.name != "nt")
    except ValueError:
        return []  # unbalanced quotes / non-command line — skip


def _as_text(value: str | Path | None) -> str | None:
    """Return text content for a value that may be inline text or a file path."""
    if value is None:
        return None
    candidate = value if isinstance(value, Path) else Path(value)
    try:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return None if isinstance(value, Path) else value
