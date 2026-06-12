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
from ._argv import (
    is_msvc_mode,
    pick_compiler_binary,
    replay_extra_flags,
    resolve_read_files,
    split_public_roots,
    unredact_home,
)
from .base import SourceExtractionError, assemble_source_tu

#: castxml extractor schema/behaviour version, recorded in the dump provenance.
CASTXML_EXTRACTOR_VERSION = "0.1"

#: Backwards-compatible aliases — the compile-context → argv helpers now live in
#: the shared ``_argv`` module (reused by the clang backend, phase 5) but are
#: re-exported here under their historical private names so existing call sites
#: and tests keep working.
_unredact_home = unredact_home
_compiler_binary = pick_compiler_binary
_replay_extra_flags = replay_extra_flags


def _std_flag(standard: str, cc_id: str) -> list[str]:
    if not standard:
        return []
    return [f"/std:{standard}"] if cc_id == "msvc" else [f"-std={standard}"]


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
    cc_bin = pick_compiler_binary(compile_unit, compiler_binary)
    cc_id = "msvc" if is_msvc_mode(cc_bin) else "gnu"

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
    cmd += _replay_extra_flags(compile_unit, cmd, cc_id)
    cmd += ["-o", str(out_xml), str(source)]
    return cmd


class CastxmlSourceExtractor:
    """Produce a :class:`SourceAbiTu` from one compile unit via castxml (D3)."""

    name = "castxml-source"
    version = CASTXML_EXTRACTOR_VERSION

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
        # The CompileUnit may carry redacted home placeholders (`~`) from the
        # evidence adapters; expand them for the replay only (ADR-032 D7), since
        # subprocess does not (see _unredact_home).
        directory = _unredact_home(compile_unit.directory)
        source = Path(_unredact_home(compile_unit.source))
        if not source.is_absolute() and directory:
            source = Path(directory) / source

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
            # Expand any redacted `~` home prefix the command builder emitted from
            # the (possibly redacted) CompileUnit — includes/system/sysroot/source
            # path operands *and* macro values. A real home path used inside a
            # macro (e.g. `-DCFG=~/build/cfg.h` consumed by `#include CFG`) must be
            # expanded or CastXML parses a different TU / fails to find the header
            # (Codex review #335). `_unredact_home` only rewrites a `~` that stands
            # in for a home directory (whole token or followed by a separator), so
            # a literal `~` mid-token (e.g. a Windows 8.3 short name, or a `~1`
            # in a value) is left intact; the rare user-authored literal `-DDIR=~/x`
            # being expanded is the accepted tradeoff for replaying redacted
            # home-path macros correctly.
            cmd = [_unredact_home(tok) for tok in cmd]
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
                    cwd=directory or None,
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
        from ...model import EnumType, Function, RecordType, ScopeOrigin, Variable
        from ...provenance import build_public_set, is_generated_header, tag_provenance

        # A public root may be a directory (`--headers include/`); split it from
        # file roots so a decl under the directory is classified public, not
        # dropped (Codex review #339, P2).
        file_roots, dir_roots = split_public_roots(public_header_roots)
        parser = _CastxmlParser(
            root,
            set(),
            set(),
            public_header_paths=file_roots,
            public_dir_paths=dir_roots,
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
        header_segs, dir_segs, have_set = build_public_set(file_roots, dir_roots)
        decls: list[Function | RecordType | EnumType | Variable] = [
            *functions,
            *records,
            *enums,
            *variables,
        ]
        for decl in decls:
            tag_provenance(decl, header_segs, dir_segs, have_set)
            if decl.origin == ScopeOrigin.PUBLIC_HEADER and is_generated_header(
                decl.source_header
            ):
                # classify_origin checks the public set before the generated
                # heuristic, so a header that is both public and generated lands
                # as PUBLIC_HEADER. Re-mark it GENERATED (still public) so a
                # generated public type change is reported as
                # generated_header_changed (D6), not silently merged into the
                # plain public surface.
                decl.origin = ScopeOrigin.GENERATED
            elif decl.origin == ScopeOrigin.GENERATED:
                # classify_origin only returns GENERATED for a generated-looking
                # header that did *not* match the public set — i.e. a private
                # generated header (build/generated/internal_config.h). The L4
                # schema treats GENERATED as a public-surface origin, so demote
                # it to PRIVATE_HEADER here to keep private generated decls/types
                # off the linked public surface (no false generated_header_changed
                # / ODR / mapping findings for internal headers).
                decl.origin = ScopeOrigin.PRIVATE_HEADER

        # Constants come from parse_constants() as a bare name->value map; pair it
        # with parse_constant_headers() so a constant declared in a *generated*
        # public header is marked GENERATED (same re-marking as the decls above).
        # Otherwise a constant removed from a generated config header is missed:
        # _diff_generated wouldn't see it as generated and _diff_declarations only
        # diffs common keys (Codex review #335, P2).
        constants = parser.parse_constants()
        constant_headers = parser.parse_constant_headers()
        generated_constants = {
            name
            for name, header in constant_headers.items()
            if is_generated_header(header)
        }

        # Public-header typedefs, provenance-scoped so private/system aliases stay
        # off the surface (ADR-030 #3). The header map feeds ODR keying and the
        # generated-header re-marking, mirroring constants above.
        typedefs = parser.parse_public_typedefs()
        typedef_headers = parser.parse_public_typedef_headers()
        generated_typedefs = {
            name
            for name, header in typedef_headers.items()
            if is_generated_header(header)
        }

        tu = assemble_source_tu(
            compile_unit,
            public_header_roots=public_header_roots,
            target_id=target_id,
            extractor_name=self.name,
            extractor_version=CASTXML_EXTRACTOR_VERSION,
            functions=functions,
            records=records,
            enums=enums,
            variables=variables,
            constants=constants,
            constant_headers=constant_headers,
            generated_constants=generated_constants,
            # Public typedefs with provenance (ADR-030 #3): parse_public_typedefs
            # scopes to the public surface and the header map keeps ODR detection
            # from colliding same-named typedefs across headers.
            typedefs=typedefs,
            typedef_headers=typedef_headers,
            generated_typedefs=generated_typedefs,
        )
        # Record every file castxml parsed (the GCC_XML <File> table) so the
        # per-TU cache (ADR-030 D8) invalidates on an edit to any transitively
        # included header, not just the configured public roots (Codex #339, P1).
        # Resolve to absolute against the build directory so the cache (run in a
        # different CWD) can read a relative castxml <File> path (Codex P2).
        tu.read_files = resolve_read_files(
            {name for el in root.findall(".//File") if (name := el.get("name"))},
            compile_unit.directory,
        )
        return tu
