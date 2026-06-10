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

import os
import re
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
#: Compiler basenames that mean castxml should run in MSVC mode.
_MSVC_BINARIES = frozenset({"cl", "cl.exe", "clang-cl", "clang-cl.exe"})


def _unredact_home(value: str) -> str:
    """Expand a redacted home placeholder (``~``) back to the real home dir.

    The evidence redaction policy (ADR-032 D7) rewrites the user's home prefix to
    ``~`` *wherever it appears* before persisting paths/argv. ``subprocess`` does
    not expand ``~`` (no shell), so replaying a redacted ``CompileUnit`` would
    treat ``~/...`` / ``-I~/...`` as literal paths and fail for any home-directory
    build. Reverse the substitution for the *replay only* (persisted values stay
    redacted) by mirroring how redaction applied it — replacing ``~`` with the
    current home. A no-op when there is no ``~`` (live/unredacted units) or no
    resolvable home.

    Only a ``~`` that stands in for a home *directory* is expanded: the
    placeholder is always either the whole token or immediately followed by a
    path separator (``/`` or ``\\``). A ``~`` followed by anything else is left
    untouched, so Windows 8.3 short names such as ``RUNNER~1`` in a freshly
    created temp path are never mangled into ``RUNNER<home>1``.
    """
    if "~" not in value:
        return value
    home = os.path.expanduser("~")
    if not home or home == "~":
        return value
    # Expand `~` only when it is the redaction placeholder for a home directory:
    # the whole token, or followed by a path separator. `re.sub` with a function
    # replacement avoids backslashes in `home` being read as group references.
    return re.sub(r"~(?=[\\/]|$)", lambda _m: home, value)


def _basename(path: str) -> str:
    """Final path component, splitting on both ``/`` and ``\\``.

    ``Path(path).name`` on a POSIX host does not treat ``\\`` as a separator, so
    a Windows compiler path from a cross/off-Windows compile database
    (``C:\\VS\\bin\\cl.exe``) would otherwise return the whole string and miss
    MSVC-mode detection. Splitting on both separators makes the basename
    host-independent.
    """
    return re.split(r"[\\/]", path)[-1]


#: Compiler-launcher wrappers that prefix the real compiler in a build action
#: (``ccache clang++ -c foo.cpp``). castxml must emulate the real compiler, not
#: the launcher, which would otherwise be invoked without its compiler operand.
_COMPILER_LAUNCHERS = frozenset(
    {"ccache", "sccache", "distcc", "icecc", "icerun", "buildcache"}
)


def _strip_launchers(argv: list[str]) -> list[str]:
    """Drop leading compiler-launcher tokens (``ccache``/``sccache``/…).

    A launcher is recognized by basename (``/usr/bin/ccache`` → ``ccache``,
    ``ccache.exe`` → ``ccache``) so the real compiler that follows it is the one
    castxml emulates.
    """
    i = 0
    while i < len(argv) and _basename(argv[i]).lower().removesuffix(
        ".exe"
    ) in _COMPILER_LAUNCHERS:
        i += 1
    return argv[i:]


def _compiler_binary(compile_unit: CompileUnit, override: str | None) -> str:
    """Pick the compiler binary castxml should emulate for this TU.

    Prefers the compiler actually recorded in the build action (``argv[0]``,
    after unwrapping any ``ccache``/``sccache`` launcher) so a
    clang/clang-cl/cross TU is replayed against its real builtin include paths,
    target defaults, and accepted flags — castxml invokes this binary to
    discover them. Falls back to g++/gcc by language only when no command is
    available (and an explicit ``override`` always wins).
    """
    if override:
        return override
    argv = _strip_launchers(compile_unit.argv)
    if argv and argv[0] and not argv[0].startswith("-"):
        return argv[0]
    return "g++" if compile_unit.language.lower() in _CXX_LANGS else "gcc"


def _std_flag(standard: str, cc_id: str) -> list[str]:
    if not standard:
        return []
    return [f"/std:{standard}"] if cc_id == "msvc" else [f"-std={standard}"]


#: GNU options that take a path operand and change *what* gets parsed (forced
#: includes / macro files). Carried through from argv since they are not
#: normalized into the structured CompileUnit fields. Only ``-include`` and
#: ``-imacros`` also have a joined ``-include<file>`` spelling; ``-include-pch``
#: is separate-operand only (clang ``-include-pch <file>``) and must not be
#: treated as a joined ``-include`` or its operand will be dropped.
_GNU_FORCED_INCLUDE_OPTS = frozenset({"-include", "-imacros"})
_GNU_SEPARATE_INCLUDE_OPTS = frozenset({"-include", "-imacros", "-include-pch"})
#: Value-taking toolchain flags already normalized into the structured
#: ``sysroot``/``target_triple`` fields and re-emitted by
#: :func:`build_castxml_command` as ``--sysroot=``/``--target=``. They must NOT
#: be carried through from ``abi_relevant_flags``: the adapter records only the
#: bare option token for the split spelling (``-isysroot /sdk`` → just
#: ``-isysroot``, operand dropped), so re-appending it yields a dangling option
#: that swallows the following argv token (``--sysroot=/sdk … -isysroot -o``).
#: Mirrors ``_TOOLCHAIN_PATH_FLAG_PREFIXES`` in ``adapters/base.py``.
_STRUCTURED_TOOLCHAIN_FLAG_PREFIXES = ("--sysroot", "-isysroot", "--target", "-target")
#: Preprocessor macro define/undef option prefixes. Their *values* are passed to
#: the compiler verbatim (argv, no shell expansion), so a literal ``~`` in e.g.
#: ``-DDEFAULT_DIR=~/app`` must NOT be home-expanded during replay — unlike the
#: path operands (includes/sysroot/source), which carry redacted home prefixes.
_MACRO_DEFINITION_PREFIXES = ("-D", "-U", "/D", "/U")
#: MSVC/clang-cl forced-include options in their separate-operand spelling
#: (``/FI file`` or ``-FI file``); the joined ``/FIfile`` form is handled by
#: prefix. (https://learn.microsoft.com/cpp/build/reference/fi-name-forced-include-file)
_MSVC_FORCED_INCLUDE_OPTS = frozenset({"/FI", "-FI"})


def _replay_extra_flags(
    compile_unit: CompileUnit, already: list[str], cc_id: str
) -> list[str]:
    """Carry through ABI/parse-relevant options not in the structured fields.

    ``abi_relevant_flags`` (e.g. ``-fms-extensions``, ``-fabi-version``,
    ``-fvisibility``, ``-m32``) and forced-include options from ``argv`` change
    the parsed translation unit; dropping them makes castxml parse a different TU
    than the real build (ADR-030 D2). De-duplicated against the flags already
    emitted from the structured fields. MSVC ``/FI`` forced includes are carried
    only in MSVC mode so a GNU ``-F``-family flag is never mistaken for one.
    """
    seen = set(already)
    out: list[str] = []
    for flag in compile_unit.abi_relevant_flags:
        # Value-taking toolchain flags (sysroot/target) are already normalized
        # into the structured fields and re-emitted by build_castxml_command. The
        # adapter records only the bare option token for the split spelling
        # (operand dropped), so carrying it through would dangle and swallow the
        # next argv token. Skip them here (see prefix-set docstring).
        if flag.startswith(_STRUCTURED_TOOLCHAIN_FLAG_PREFIXES):
            continue
        if flag not in seen:
            out.append(flag)
            seen.add(flag)
    argv = compile_unit.argv
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _GNU_SEPARATE_INCLUDE_OPTS and i + 1 < len(argv):
            out += [tok, argv[i + 1]]  # -include / -imacros / -include-pch <file>
            i += 2
        elif tok not in _GNU_SEPARATE_INCLUDE_OPTS and any(
            tok.startswith(opt) and len(tok) > len(opt)
            for opt in _GNU_FORCED_INCLUDE_OPTS
        ):
            out.append(tok)  # -includefile / -imacrosfile (joined)
            i += 1
        elif cc_id == "msvc" and tok in _MSVC_FORCED_INCLUDE_OPTS and i + 1 < len(argv):
            out += [tok, argv[i + 1]]  # /FI file (separate operand)
            i += 2
        elif (
            cc_id == "msvc"
            and len(tok) > 3
            and (tok.startswith("/FI") or tok.startswith("-FI"))
        ):
            out.append(tok)  # /FIfile (joined)
            i += 1
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
    cc_id = "msvc" if _basename(cc_bin).lower() in _MSVC_BINARIES else "gnu"

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
            # Expand any redacted `~` in the include/system/sysroot/source/argv
            # *path* operands the command builder emitted from the (possibly
            # redacted) CompileUnit. Macro definitions (`-D`/`-U`, `/D`/`/U`) are
            # left verbatim: the compiler receives a `-DDIR=~/app` value literally
            # (argv, no shell expansion), so unredacting a legitimate literal tilde
            # in a macro value would parse a different TU (Codex review #335, P2).
            # Other emitted flags (`-std=`, `--target=`, `-f*`) carry no `~`, so
            # expanding them is a no-op.
            cmd = [
                tok
                if tok.startswith(_MACRO_DEFINITION_PREFIXES)
                else _unredact_home(tok)
                for tok in cmd
            ]
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
            constants=constants,
            constant_headers=constant_headers,
            generated_constants=generated_constants,
            # parse_typedefs() returns a flat name->target map with no source
            # provenance, so — unlike parse_constants(), which scopes itself to
            # public headers — typedefs cannot be marked PUBLIC_HEADER without
            # pulling private/system aliases onto the surface (and risking false
            # odr_source_conflict). Omitted until a provenance-aware typedef
            # extractor exists (Clang backend, phase 5); records/enums still
            # carry full type-change coverage with correct provenance.
            typedefs={},
        )
