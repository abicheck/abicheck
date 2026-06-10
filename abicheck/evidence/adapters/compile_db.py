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
from ..build_evidence import BuildEvidence, CompileUnit
from ..redaction import DEFAULT_REDACTION, RedactionPolicy
from .base import (
    compile_unit_id,
    derive_build_options,
    detect_language,
    extract_abi_relevant_flags,
)


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
                defines={k: self.redaction.define_value(k, v or "") for k, v in ctx.defines.items()},
                undefines=sorted(ctx.undefines),
                include_paths=[self.redaction.path(str(p)) for p in ctx.include_paths],
                system_include_paths=[self.redaction.path(str(p)) for p in ctx.system_includes],
                sysroot=self.redaction.path(str(ctx.sysroot)) if ctx.sysroot else None,
                target_triple=ctx.target_triple or "",
                abi_relevant_flags=[self.redaction.arg(f) for f in abi_flags],
            )
            ev.compile_units.append(cu)
        ev.build_options = derive_build_options(ev.compile_units)
        return ev
