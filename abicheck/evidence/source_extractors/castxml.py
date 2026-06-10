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

"""castxml source ABI extractor (ADR-030 D3, phase 2).

Parses a translation unit / public headers under their real per-TU build
context (ADR-030 D2, from an ADR-029 :class:`CompileUnit`) and produces a
normalized :class:`SourceAbiTu`. It reuses abicheck's existing castxml XML
parser (``_CastxmlParser``) — the same dependency the L2 header tier already
needs — so no new tool is introduced (ADR-001 lightweight-core constraint).

castxml is good for declarations, types, and public const/constexpr values but
weak for function bodies and macro expansions (ADR-030 D3 table); inline/template
*body* fingerprints are left to the Clang backend (phase 5). The
context→argv builder is pure and unit-testable; only :meth:`extract` shells out.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import cast
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as DefusedET

from ..build_evidence import CompileUnit
from ..source_abi import SourceAbiTu
from .base import SourceExtractionError, assemble_source_tu

#: castxml extractor schema/behaviour version, recorded in the dump provenance.
CASTXML_EXTRACTOR_VERSION = "0.1"

_CXX_LANGS = frozenset({"cxx", "c++", "cpp"})


def _compiler_binary(compile_unit: CompileUnit, override: str | None) -> str:
    """Pick the compiler binary castxml should emulate for this TU."""
    if override:
        return override
    return "g++" if compile_unit.language.lower() in _CXX_LANGS else "gcc"


def _std_flag(standard: str, cc_id: str) -> list[str]:
    if not standard:
        return []
    return [f"/std:{standard}"] if cc_id == "msvc" else [f"-std={standard}"]


#: argv options that take a path operand and change *what* gets parsed (forced
#: includes / macro files). Carried through from argv since they are not
#: normalized into the structured CompileUnit fields.
_FORCED_INCLUDE_OPTS = frozenset({"-include", "-imacros"})


def _replay_extra_flags(compile_unit: CompileUnit, already: list[str]) -> list[str]:
    """Carry through ABI/parse-relevant options not in the structured fields.

    ``abi_relevant_flags`` (e.g. ``-fms-extensions``, ``-fabi-version``,
    ``-fvisibility``, ``-m32``) and forced-include options from ``argv`` change
    the parsed translation unit; dropping them makes castxml parse a different TU
    than the real build (ADR-030 D2). De-duplicated against the flags already
    emitted from the structured fields.
    """
    seen = set(already)
    out: list[str] = []
    for flag in compile_unit.abi_relevant_flags:
        if flag not in seen:
            out.append(flag)
            seen.add(flag)
    argv = compile_unit.argv
    i = 0
    while i < len(argv):
        if argv[i] in _FORCED_INCLUDE_OPTS and i + 1 < len(argv):
            out += [argv[i], argv[i + 1]]
            i += 2
        else:
            i += 1
    return out


def build_castxml_command(
    compile_unit: CompileUnit,
    source: Path,
    out_xml: Path,
    *,
    castxml_bin: str = "castxml",
    compiler_binary: str | None = None,
) -> list[str]:
    """Build the castxml argv for a compile unit's real build context (D2).

    Mirrors the compile unit's language standard, defines/undefines, include and
    system-include paths, sysroot, and target triple, so source replay sees the
    headers the compiler actually saw under the flags it actually used.
    """
    cc_bin = _compiler_binary(compile_unit, compiler_binary)
    cc_id = "msvc" if Path(cc_bin).name.lower() in ("cl", "cl.exe") else "gnu"

    cmd = [castxml_bin, "--castxml-output=1", f"--castxml-cc-{cc_id}", cc_bin]
    cmd += _std_flag(compile_unit.standard, cc_id)
    for key, value in compile_unit.defines.items():
        cmd.append(f"-D{key}={value}" if value else f"-D{key}")
    for undef in compile_unit.undefines:
        cmd.append(f"-U{undef}")
    for inc in compile_unit.include_paths:
        cmd += ["-I", inc]
    for inc in compile_unit.system_include_paths:
        cmd += ["-isystem", inc]
    if compile_unit.sysroot:
        cmd.append(f"--sysroot={compile_unit.sysroot}")
    if compile_unit.target_triple and cc_id != "msvc":
        cmd.append(f"--target={compile_unit.target_triple}")
    cmd += _replay_extra_flags(compile_unit, cmd)
    cmd += ["-o", str(out_xml), str(source)]
    return cmd


class CastxmlSourceExtractor:
    """Produce a :class:`SourceAbiTu` from one compile unit via castxml (D3)."""

    name = "castxml-source"

    def __init__(
        self,
        *,
        castxml_bin: str = "castxml",
        compiler_binary: str | None = None,
        timeout: int = 120,
    ) -> None:
        self.castxml_bin = castxml_bin
        self.compiler_binary = compiler_binary
        self.timeout = timeout

    def available(self) -> bool:
        return shutil.which(self.castxml_bin) is not None

    def extract(
        self,
        compile_unit: CompileUnit,
        *,
        public_header_roots: list[str],
        target_id: str = "",
    ) -> SourceAbiTu:
        """Parse ``compile_unit`` with castxml and normalize the result (D4).

        Raises :class:`SourceExtractionError` when castxml is unavailable or
        fails — callers record the failure as partial L4 coverage (ADR-028 D7)
        rather than aborting the whole comparison.
        """
        if not self.available():
            raise SourceExtractionError(
                f"{self.castxml_bin} not found in PATH; cannot run source ABI replay."
            )
        source = Path(compile_unit.source)
        if not source.is_absolute() and compile_unit.directory:
            source = Path(compile_unit.directory) / source

        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
            out_xml = Path(tmp.name)
        try:
            cmd = build_castxml_command(
                compile_unit,
                source,
                out_xml,
                castxml_bin=self.castxml_bin,
                compiler_binary=self.compiler_binary,
            )
            try:
                # Run in the compile unit's directory so relative -I/-isystem
                # and forced-include paths resolve exactly as the real build did
                # (compile_commands.json `directory`).
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                    cwd=compile_unit.directory or None,
                )
            except subprocess.TimeoutExpired as exc:
                raise SourceExtractionError(
                    f"castxml timed out after {self.timeout}s on {compile_unit.source}"
                ) from exc
            if (
                result.returncode != 0
                or not out_xml.exists()
                or out_xml.stat().st_size == 0
            ):
                raise SourceExtractionError(
                    f"castxml failed on {compile_unit.source} "
                    f"(exit {result.returncode}): {result.stderr[:1000]}"
                )
            root = cast(Element, DefusedET.parse(str(out_xml)).getroot())
        finally:
            out_xml.unlink(missing_ok=True)

        return self._parse_root(root, compile_unit, public_header_roots, target_id)

    def _parse_root(
        self,
        root: Element,
        compile_unit: CompileUnit,
        public_header_roots: list[str],
        target_id: str,
    ) -> SourceAbiTu:
        """Run the shared castxml parser over a parsed XML root and normalize it.

        Split out so tests can exercise the XML→``SourceAbiTu`` path on a fixture
        document without invoking castxml.
        """
        # Imported lazily: the castxml parser pulls in the heavy dumper model
        # graph, which the lightweight evidence layer should not load eagerly.
        from ...dumper_castxml import _CastxmlParser
        from ...provenance import build_public_set, tag_provenance

        parser = _CastxmlParser(
            root,
            set(),
            set(),
            public_header_paths=list(public_header_roots),
            public_dir_paths=[],
        )
        functions = parser.parse_functions()
        records = parser.parse_types()
        enums = parser.parse_enums()
        variables = parser.parse_variables()

        # The parser leaves origin == UNKNOWN; the snapshot path classifies it
        # later via apply_provenance, but this extractor bypasses snapshots, so
        # classify here against the public-header set. Without this every public
        # declaration would map to api_relevant=False and the linker would drop
        # it, leaving L4 with only the self-scoped constants (ADR-024 D1).
        header_segs, dir_segs, have_set = build_public_set(
            list(public_header_roots), []
        )
        for decl in (*functions, *records, *enums, *variables):
            tag_provenance(decl, header_segs, dir_segs, have_set)

        return assemble_source_tu(
            compile_unit,
            public_header_roots=public_header_roots,
            target_id=target_id,
            extractor_name=self.name,
            extractor_version=CASTXML_EXTRACTOR_VERSION,
            functions=functions,
            records=records,
            enums=enums,
            variables=variables,
            constants=parser.parse_constants(),
            typedefs=parser.parse_typedefs(),
        )
