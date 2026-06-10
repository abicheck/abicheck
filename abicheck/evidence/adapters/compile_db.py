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

"""compile_commands.json adapter (ADR-029 D3).

The universal low-friction L3 input. Reuses the ADR-020a parser in
``build_context.py`` (which already handles ``arguments`` vs ``command``,
``directory``-relative resolution, and ABI-flag extraction) and projects each
entry into a normalized :class:`CompileUnit`.
"""
from __future__ import annotations

from pathlib import Path

from ...build_context import _extract_flags, load_compile_db
from ..build_evidence import BuildEvidence, BuildOption, CompileUnit
from ..redaction import DEFAULT_REDACTION, RedactionPolicy
from .base import compile_unit_id, detect_language, extract_abi_relevant_flags


class CompileDbAdapter:
    """Normalize a ``compile_commands.json`` into :class:`BuildEvidence`."""

    name = "compile_commands"

    def __init__(
        self,
        compile_db: Path | str,
        *,
        build_system: str = "generic",
        redaction: RedactionPolicy | None = None,
    ) -> None:
        self.compile_db = Path(compile_db)
        self.build_system = build_system
        self.redaction = redaction or DEFAULT_REDACTION

    def collect(self) -> BuildEvidence:
        entries = load_compile_db(self.compile_db)
        ev = BuildEvidence()
        seen_options: set[tuple[str, str]] = set()
        for entry in entries:
            argv = list(entry.arguments)
            ctx = _extract_flags(argv, entry.directory)
            source = str(entry.file)
            abi_flags = extract_abi_relevant_flags(argv)
            red_argv = self.redaction.argv(argv)
            cu = CompileUnit(
                id=compile_unit_id(self.redaction.path(source), red_argv),
                source=self.redaction.path(source),
                directory=self.redaction.path(str(entry.directory)),
                argv=red_argv,
                language=detect_language(source),
                standard=ctx.language_standard or "",
                defines={k: (v or "") for k, v in ctx.defines.items()},
                undefines=sorted(ctx.undefines),
                include_paths=[self.redaction.path(str(p)) for p in ctx.include_paths],
                system_include_paths=[self.redaction.path(str(p)) for p in ctx.system_includes],
                sysroot=self.redaction.path(str(ctx.sysroot)) if ctx.sysroot else None,
                target_triple=ctx.target_triple or "",
                abi_relevant_flags=[self.redaction.arg(f) for f in abi_flags],
            )
            ev.compile_units.append(cu)
            _collect_options(cu, seen_options, ev.build_options, self.redaction)
        return ev


def _collect_options(
    cu: CompileUnit,
    seen: set[tuple[str, str]],
    out: list[BuildOption],
    redaction: RedactionPolicy,
) -> None:
    """Project a compile unit's ABI-relevant flags into global BuildOptions.

    De-duplicated across units so a flag shared by 100 TUs records once. The
    build-evidence diff compares these option records between packs (ADR-029 D9).
    """
    if cu.standard:
        # Key the language standard per-language so a mixed C/C++ project keeps
        # std:C and std:CXX distinct (otherwise one masks the other in the diff).
        std_key = f"std:{cu.language}" if cu.language else "std"
        _add_option(out, seen, std_key, cu.standard, abi_relevant=True, raw=f"-std={cu.standard}")
    if cu.target_triple:
        _add_option(out, seen, "target", cu.target_triple, abi_relevant=True, raw=cu.target_triple)
    if cu.sysroot:
        _add_option(out, seen, "sysroot", cu.sysroot, abi_relevant=True, raw=cu.sysroot)
    for flag in cu.abi_relevant_flags:
        if flag.startswith(("-D", "/D")):
            key, _, value = flag[2:].partition("=")
            _add_option(out, seen, f"define:{key}", value, abi_relevant=True, raw=flag)
        elif not flag.startswith("-std="):
            _add_option(out, seen, flag.split("=", 1)[0], flag, abi_relevant=True, raw=flag)


def _add_option(
    out: list[BuildOption],
    seen: set[tuple[str, str]],
    key: str,
    value: str,
    *,
    abi_relevant: bool,
    raw: str,
) -> None:
    sig = (key, value)
    if sig in seen:
        return
    seen.add(sig)
    out.append(BuildOption(key=key, value=value, abi_relevant=abi_relevant, raw=raw))
