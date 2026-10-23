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

import posixpath
from pathlib import Path, PurePosixPath

from ...build_context import BuildContext, _extract_flags, load_compile_db
from ..build_evidence import BuildEvidence, CompileUnit, LinkUnit
from ..link_attribution import normalize_source_path
from ..redaction import DEFAULT_REDACTION, RedactionPolicy
from ..source_extractors._argv import is_msvc_mode
from ..source_extractors.compile_action import (
    CompileAction,
    GnuOperands,
    SourceOperand,
    parse_gnu_operands,
)
from ..source_extractors.compile_invocation import strip_launchers
from .adapter_shared import (
    compile_unit_id,
    derive_build_options,
    effective_language,
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
            # POSIX separators whatever OS collected the database: the evidence
            # may be compared on another OS, and a native "D:\...\foo.cpp" never
            # matches the "/"-keyed graph paths built from it.
            source = normalize_source_path(str(entry.file))
            directory = normalize_source_path(str(entry.directory))
            abi_flags = extract_abi_relevant_flags(argv)
            gnu = _gnu_operands(argv, directory)
            if gnu is None or not _routed_per_operand(gnu):
                ev.compile_units.append(
                    self._compile_unit(
                        ctx,
                        argv,
                        directory,
                        abi_flags,
                        source,
                        effective_language(argv, source),
                    )
                )
                continue
            # A multi-source entry is one unit per source, each with the language
            # the driver applies at its own position. A link with no source
            # compiles nothing, so its only product is the link unit.
            for operand in gnu.operands:
                ev.compile_units.append(
                    self._compile_unit(
                        ctx,
                        argv,
                        directory,
                        abi_flags,
                        _operand_path(operand.source, directory),
                        _operand_language(operand),
                    )
                )
            if gnu.action is CompileAction.LINK:
                ev.link_units.append(self._link_unit(gnu, argv, directory))
        ev.build_options = derive_build_options(ev.compile_units)
        return ev

    def _compile_unit(
        self,
        ctx: BuildContext,
        argv: list[str],
        directory: str,
        abi_flags: list[str],
        source: str,
        language: str,
    ) -> CompileUnit:
        red_argv = self.redaction.argv(argv)
        return CompileUnit(
            id=compile_unit_id(self.redaction.path(source), red_argv),
            source=self.redaction.path(source),
            directory=self.redaction.path(directory),
            argv=red_argv,
            language=language,
            standard=ctx.language_standard or "",
            defines={
                k: self.redaction.define_value(k, v or "")
                for k, v in ctx.defines.items()
            },
            undefines=sorted(ctx.undefines),
            include_paths=[self.redaction.path(str(p)) for p in ctx.include_paths],
            system_include_paths=[
                self.redaction.path(str(p)) for p in ctx.system_includes
            ],
            include_paths_explicit=list(ctx.include_paths_explicit),
            system_include_paths_explicit=list(ctx.system_includes_explicit),
            sysroot=self.redaction.path(str(ctx.sysroot)) if ctx.sysroot else None,
            target_triple=ctx.target_triple or "",
            abi_relevant_flags=[self.redaction.arg(f) for f in abi_flags],
        )

    def _link_unit(self, gnu: GnuOperands, argv: list[str], directory: str) -> LinkUnit:
        output = self.redaction.path(_operand_path(gnu.link_output, directory))
        shared = "-shared" in argv or "-dynamiclib" in argv
        return LinkUnit(
            id=f"link://{output}",
            output=output,
            kind="shared_library" if shared else "executable",
            inputs=[
                self.redaction.path(_operand_path(i, directory))
                for i in gnu.link_inputs
            ],
            linker_argv=self.redaction.argv(argv),
            directory=self.redaction.path(normalize_source_path(directory)),
        )


def _gnu_operands(argv: list[str], directory: str) -> GnuOperands | None:
    """The per-source operands of a GNU-driver entry, or ``None`` for the CL driver.

    CL-mode operands are a separate slice (plan ``l4-l2-extraction-convergence``
    Phase 3), so an MSVC entry keeps the one-unit-per-entry path.
    """
    stripped = strip_launchers(argv, directory=directory)
    if not stripped or is_msvc_mode(stripped[0]):
        return None
    return parse_gnu_operands(stripped[1:])


def _routed_per_operand(gnu: GnuOperands) -> bool:
    """Whether *gnu* is a multi-source entry, or a link with no source operand.

    A single-source entry keeps its one unit exactly as before, including a
    single-source link (its source is compiled by the link step). Only the
    entries this rule routes change; the others stay byte-identical.
    """
    if len(gnu.operands) > 1:
        return True
    return not gnu.operands and gnu.action is CompileAction.LINK


def _operand_path(spelled: str, directory: str) -> str:
    """The operand's path as the entry's ``file`` spells it: resolved against ``directory``.

    ``load_compile_db`` resolves an entry's ``file`` against its directory, so a
    relative operand must be resolved the same way or one entry's units would
    mix relative and absolute spellings.
    """
    path = normalize_source_path(spelled)
    if PurePosixPath(path).is_absolute():
        return path
    return posixpath.normpath(posixpath.join(directory, path))


def _operand_language(operand: SourceOperand) -> str:
    """The language the driver applies at *operand*'s own ``-x`` position.

    Every source operand has one: a ``-x`` in effect, or a known source
    extension (an extensionless file is a link input, not an operand).
    """
    return effective_language(["-x", operand.language], operand.source)
