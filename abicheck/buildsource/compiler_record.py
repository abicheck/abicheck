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

"""Compiler-recorded metadata extractor (ADR-029 D8).

Recovers compiler provenance from an *already-built* binary without rebuilding:

- the GCC/Clang ``.GCC.command.line`` ELF section (emitted by
  ``-frecord-gcc-switches`` / ``-frecord-command-line``) → the recorded compiler
  command lines, from which ABI-relevant build options are extracted;
- DWARF ``DW_AT_producer`` → compiler id / version (and language).

These signals are **advisory** (D8): they describe how the shipped artifact was
built, but are only authoritative when cross-checked against build-system
metadata. The extractor never executes the binary; it only reads ELF/DWARF.

The byte/string parsing is split into pure helpers (``parse_gcc_command_line``,
``parse_producer``) so it is testable without a compiled fixture; the ELF
wrapper degrades gracefully (diagnostic, empty evidence) on any read failure.
"""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from ..build_context import _extract_flags
from .adapters.base import (
    compile_unit_id,
    derive_build_options,
    effective_language,
    extract_abi_relevant_flags,
    source_from_argv,
)
from .build_evidence import BuildEvidence, CompileUnit, Toolchain
from .redaction import DEFAULT_REDACTION, RedactionPolicy

#: GCC records command lines here under -frecord-gcc-switches / -frecord-command-line.
_COMMAND_LINE_SECTION = ".GCC.command.line"

_VERSION_RE = re.compile(r"\b(\d+\.\d+(?:\.\d+)?)\b")


def parse_gcc_command_line(data: bytes) -> list[str]:
    """Split a ``.GCC.command.line`` section blob into recorded command strings.

    The section is a sequence of NUL-terminated strings, one per recorded
    compiler invocation. Empty entries are dropped; decoding is lenient.
    """
    return [chunk.decode("utf-8", "replace") for chunk in data.split(b"\x00") if chunk]


def parse_producer(producer: str) -> Toolchain | None:
    """Parse a DWARF ``DW_AT_producer`` string into a :class:`Toolchain`.

    Examples: ``"GNU C++17 13.2.0 -std=c++17 -O2"`` → GNU 13.2.0 CXX;
    ``"clang version 17.0.6"`` → Clang 17.0.6.
    """
    producer = producer.strip()
    if not producer:
        return None
    low = producer.lower()
    if producer.startswith("GNU"):
        compiler_id = "GNU"
    elif "clang" in low:
        compiler_id = "Clang"
    elif "intel" in low or "icpx" in low or "icc" in low:
        compiler_id = "Intel"
    elif "rustc" in low:
        compiler_id = "Rust"
    else:
        compiler_id = producer.split()[0]
    m = _VERSION_RE.search(producer)
    version = m.group(1) if m else ""
    language = "CXX" if "C++" in producer else ("C" if re.search(r"\bC\d", producer) else "")
    tid = f"toolchain://{compiler_id}-{version or 'unknown'}-dwarf".lower()
    return Toolchain(id=tid, compiler_id=compiler_id, version=version, language=language)


def extract_compiler_record(
    binary: Path | str,
    *,
    redaction: RedactionPolicy | None = None,
) -> BuildEvidence:
    """Extract compiler-recorded provenance from *binary* (ELF only).

    Returns build evidence carrying any recovered toolchain, compile units, and
    ABI-relevant build options. Failure to open/parse the binary yields empty
    evidence with a diagnostic rather than raising.
    """
    red = redaction or DEFAULT_REDACTION
    ev = BuildEvidence()
    path = Path(binary)
    try:
        with path.open("rb") as fh:
            elf = ELFFile(fh)
            _collect_command_line(elf, ev, red)
            _collect_producer(elf, ev)
    except (OSError, ELFError) as exc:
        ev.diagnostics.append(f"compiler-record: cannot read {red.path(str(path))} as ELF: {exc}")
        return ev

    if ev.toolchains or ev.compile_units or ev.build_options:
        ev.diagnostics.append(
            "compiler-record: provenance recovered from compiler-recorded "
            "metadata — advisory unless cross-checked against build-system evidence"
        )
    return ev


def _collect_command_line(elf: Any, ev: BuildEvidence, red: RedactionPolicy) -> None:
    section = elf.get_section_by_name(_COMMAND_LINE_SECTION)
    if section is None:
        return
    parsed: list[CompileUnit] = []
    for command in parse_gcc_command_line(section.data()):
        try:
            argv = shlex.split(command, posix=os.name != "nt")
        except ValueError:
            continue
        if not argv:
            continue
        unit = _command_to_unit(argv, red)
        parsed.append(unit)
        # Only emit a compile unit when the record names a translation unit.
        # ``-frecord-gcc-switches`` records switches with no source — those
        # still carry ABI options, so they feed build_options below.
        if unit.source:
            ev.compile_units.append(unit)
    if parsed:
        ev.build_options = derive_build_options(parsed)


def _command_to_unit(argv: list[str], red: RedactionPolicy) -> CompileUnit:
    """Build a CompileUnit from a recorded command; ``source`` is "" if absent."""
    source = source_from_argv(argv)
    ctx = _extract_flags(argv, Path("."))
    red_argv = red.argv(argv)
    red_source = red.path(source) if source else ""
    return CompileUnit(
        id=compile_unit_id(red_source, red_argv),
        source=red_source,
        argv=red_argv,
        language=effective_language(argv, source),
        standard=ctx.language_standard or "",
        defines={k: red.define_value(k, v or "") for k, v in ctx.defines.items()},
        undefines=sorted(ctx.undefines),
        include_paths=[red.path(str(p)) for p in ctx.include_paths],
        system_include_paths=[red.path(str(p)) for p in ctx.system_includes],
        sysroot=red.path(str(ctx.sysroot)) if ctx.sysroot else None,
        target_triple=ctx.target_triple or "",
        abi_relevant_flags=[red.arg(f) for f in extract_abi_relevant_flags(argv)],
    )


def _collect_producer(elf: Any, ev: BuildEvidence) -> None:
    if not elf.has_dwarf_info():
        return
    dwarf = elf.get_dwarf_info()
    seen: set[str] = set()
    for cu in dwarf.iter_CUs():
        try:
            attr = cu.get_top_DIE().attributes.get("DW_AT_producer")
        except (KeyError, AttributeError, ELFError):
            continue
        if attr is None:
            continue
        value = attr.value
        producer = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
        toolchain = parse_producer(producer)
        if toolchain is not None and toolchain.id not in seen:
            ev.toolchains.append(toolchain)
            seen.add(toolchain.id)
