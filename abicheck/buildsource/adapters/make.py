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
import re
import shlex
import stat as stat_module
from pathlib import Path

from ...build_context import _extract_flags
from ..build_evidence import BuildEvidence, CompileUnit, Generator, LinkUnit
from ..redaction import DEFAULT_REDACTION, RedactionPolicy
from .base import (
    compile_unit_id,
    derive_build_options,
    effective_language,
    extract_abi_relevant_flags,
    source_from_argv,
)

#: Object/archive extensions a link/archive line's inputs are expected to
#: carry — the presence of at least one is what tells a plain "-o X ..." line
#: (which could just as easily be a compile, a codegen step, or an unrelated
#: recipe command) apart from a genuine link/archive invocation (ADR-053 D2).
_LINK_INPUT_EXTENSIONS = (".o", ".obj", ".a", ".lib")

#: Recognized shared-library output extensions (POSIX + Windows + macOS).
_SHARED_LIB_EXTENSIONS = (".so", ".dylib", ".dll")

#: Matches ``ar`` / a cross-toolchain-prefixed ``ar`` (e.g. ``x86_64-linux-
#: gnu-ar``), optionally with a path prefix and/or ``.exe`` suffix.
_AR_PROG_RE = re.compile(r"^(?:[\w.+-]*-)?ar(?:\.exe)?$", re.IGNORECASE)


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
        directory_stack: list[Path] = []
        for line in text.splitlines():
            event, event_dir = _directory_event(line)
            if event == "enter" and event_dir is not None:
                directory_stack.append(directory)
                directory = event_dir
                continue
            if event == "leave":
                directory = directory_stack.pop() if directory_stack else (self.build_dir or Path("."))
                continue
            cu = self._compile_unit(line, directory)
            if cu is not None:
                ev.compile_units.append(cu)
                continue
            lu = self._link_unit(line, directory)
            if lu is not None:
                ev.link_units.append(lu)

        if ev.compile_units:
            ev.build_options = derive_build_options(ev.compile_units)
            existing_sources = _count_existing_sources(ev.compile_units)
            ev.diagnostics.append(
                f"make: {len(ev.compile_units)} compile units derived from a make "
                "dry-run transcript — reduced confidence (not an authoritative "
                "target graph); prefer a generated compile_commands.json"
            )
            ev.diagnostics.append(
                f"make: {existing_sources}/{len(ev.compile_units)} compile-unit "
                "source paths exist relative to their recorded directories"
            )
        if ev.link_units:
            ev.diagnostics.append(
                f"make: {len(ev.link_units)} link/archive units derived from a "
                "make dry-run transcript — reduced confidence (heuristic argv "
                "recognition, not an authoritative target graph); feeds "
                "abicheck.buildsource.link_attribution's TU-to-output "
                "attribution (ADR-053), not build_diff directly."
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
        argv = _truncate_shell_pipeline(argv)
        # A translation-unit compile is a `-c` (GNU) / `/c` (MSVC, clang-cl)
        # invocation that names a source; link/info/`Entering directory` lines
        # lack one of those and are skipped.
        if "-c" not in argv and "/c" not in argv:
            return None
        source = source_from_argv(argv)
        if not source:
            return None
        argv, _expanded_rsp = _expand_response_files(argv, directory, self.build_dir)
        ctx = _extract_flags(argv, directory)
        output = _output_from_argv(argv)
        red_argv = self.redaction.argv(argv)
        red_source = self.redaction.path(source)
        red_output = self.redaction.path(output)
        return CompileUnit(
            id=compile_unit_id(red_source, red_argv, red_output),
            source=red_source,
            output=red_output,
            directory=self.redaction.path(str(directory)),
            argv=red_argv,
            language=effective_language(argv, source),
            standard=ctx.language_standard or "",
            defines={k: self.redaction.define_value(k, v or "") for k, v in ctx.defines.items()},
            undefines=sorted(ctx.undefines),
            include_paths=[self.redaction.path(str(p)) for p in ctx.include_paths],
            system_include_paths=[self.redaction.path(str(p)) for p in ctx.system_includes],
            sysroot=self.redaction.path(str(ctx.sysroot)) if ctx.sysroot else None,
            target_triple=ctx.target_triple or "",
            abi_relevant_flags=[self.redaction.arg(f) for f in extract_abi_relevant_flags(argv)],
        )

    def _link_unit(self, line: str, directory: Path) -> LinkUnit | None:
        """Recognize an ``ar`` archive-creation or compiler-frontend link line.

        ADR-053 D2: Make has no semantic target graph to lean on the way
        CMake/Bazel do, so this is the one build system where TU->DSO
        attribution genuinely needs real linker/archiver argv recognition,
        not just a target-source-list join. Same reduced-confidence,
        heuristic-scraping posture as :meth:`_compile_unit` — a dry-run
        transcript is not an authoritative target graph, and a line this
        doesn't recognize is simply skipped, never an error.
        """
        argv = _split_recipe(line)
        argv, directory = _consume_cd_prefix(argv, directory)
        argv = _truncate_shell_pipeline(argv)
        if not argv:
            return None
        argv, _expanded_rsp = _expand_response_files(argv, directory, self.build_dir)
        prog = Path(argv[0]).name
        if _AR_PROG_RE.match(prog):
            return self._archive_link_unit(argv)
        # A compile line was already ruled out by the caller (it tried
        # _compile_unit first); anything left with an -o/-shared-shaped
        # output and at least one object/archive input is treated as a
        # link line.
        return self._compiler_link_unit(argv)

    def _archive_link_unit(self, argv: list[str]) -> LinkUnit | None:
        # `ar <flags> output.a input1.o input2.o ...` -- ar's traditional
        # (BSD-style) flag syntax has no leading '-' at all ("ar rcs a.a
        # b.o"), unlike every other tool this module recognizes, so a plain
        # "no leading '-'" filter would wrongly keep the flags token itself
        # as an operand. Drop argv[1] too when it looks like a flag-letters
        # token (all-alpha, no '.'/'/': a real archive/object path always
        # has one of those) rather than a real path.
        rest = argv[1:]
        if rest and rest[0].isalpha() and "." not in rest[0] and "/" not in rest[0]:
            rest = rest[1:]
        operands = [a for a in rest if not a.startswith("-")]
        if len(operands) < 2:
            return None
        output, inputs = operands[0], operands[1:]
        if not output.lower().endswith((".a", ".lib")):
            return None
        inputs = [i for i in inputs if i.lower().endswith(_LINK_INPUT_EXTENSIONS)]
        if not inputs:
            return None
        # No directory-joining on output/inputs, matching _compile_unit's own
        # convention for CompileUnit.output (kept exactly as written in the
        # recipe) -- link_attribution.py matches LinkUnit.inputs against
        # CompileUnit.output verbatim, so both sides must use the identical
        # convention or nothing would ever match.
        red_output = self.redaction.path(output)
        return LinkUnit(
            id=f"link://{red_output}",
            output=red_output,
            kind="static_library",
            inputs=[self.redaction.path(i) for i in inputs],
            linker_argv=self.redaction.argv(argv),
        )

    def _compiler_link_unit(self, argv: list[str]) -> LinkUnit | None:
        output = _output_from_argv(argv)
        if not output:
            return None
        inputs = [
            a
            for a in argv[1:]
            if not a.startswith("-")
            and not _is_msvc_flag(a)
            and a.lower().endswith(_LINK_INPUT_EXTENSIONS)
        ]
        if not inputs:
            return None
        lower_output = output.lower()
        if lower_output.endswith(_SHARED_LIB_EXTENSIONS) or "-shared" in argv:
            kind = "shared_library"
        elif lower_output.endswith((".o", ".obj")):
            # A multi-object partial/incremental link ("ld -r"), not a real
            # DSO/executable output -- not a link_attribution terminal kind.
            return None
        else:
            kind = "executable"
        # See _archive_link_unit's comment: no directory-joining, matching
        # _compile_unit's own CompileUnit.output convention.
        red_output = self.redaction.path(output)
        return LinkUnit(
            id=f"link://{red_output}",
            output=red_output,
            kind=kind,
            inputs=[self.redaction.path(i) for i in inputs],
            linker_argv=self.redaction.argv(argv),
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


_MAKE_PROG_RE = r"(?:[^:\s]*[\\/])?(?:g?make|gnumake|mingw32-make)(?:\.exe)?"
_ENTER_RE = re.compile(
    rf"^{_MAKE_PROG_RE}(?:\[\d+\])?: Entering directory ['`](.+)['`]$"
)
_LEAVE_RE = re.compile(
    rf"^{_MAKE_PROG_RE}(?:\[\d+\])?: Leaving directory ['`](.+)['`]$"
)


def _directory_event(line: str) -> tuple[str, Path | None]:
    """Return make directory transitions from ``make -C`` / recursive logs."""
    stripped = line.strip()
    m = _ENTER_RE.match(stripped)
    if m:
        return "enter", Path(m.group(1))
    if _LEAVE_RE.match(stripped):
        return "leave", None
    return "", None


def _truncate_shell_pipeline(argv: list[str]) -> list[str]:
    """Keep only the compiler invocation before shell control operators."""
    for i, arg in enumerate(argv):
        if arg in {"&&", ";", "||", "|"}:
            return argv[:i]
    return argv


_MAX_RESPONSE_FILE_BYTES = 1024 * 1024


def _expand_response_files(
    argv: list[str],
    directory: Path,
    response_root: Path | None,
) -> tuple[list[str], bool]:
    """Inline bounded compiler response files from the trusted build tree."""
    expanded: list[str] = []
    changed = False
    root = _safe_resolve(response_root) if response_root is not None else None
    for arg in argv:
        if not arg.startswith("@") or len(arg) == 1:
            expanded.append(arg)
            continue
        text = _read_response_file(Path(arg[1:]), directory, root)
        if text is None:
            expanded.append(arg)
            continue
        try:
            expanded.extend(shlex.split(text, posix=os.name != "nt"))
            changed = True
        except ValueError:
            expanded.append(arg)
    return expanded, changed


def _read_response_file(path: Path, directory: Path, root: Path | None) -> str | None:
    if root is None:
        return None
    candidate = path if path.is_absolute() else directory / path
    resolved = _safe_resolve(candidate)
    if resolved is None or not _is_relative_to(resolved, root):
        return None
    try:
        st = resolved.stat()
    except OSError:
        return None
    if not stat_module.S_ISREG(st.st_mode) or st.st_size > _MAX_RESPONSE_FILE_BYTES:
        return None
    try:
        data = resolved.read_bytes()
    except OSError:
        return None
    if len(data) > _MAX_RESPONSE_FILE_BYTES:
        return None
    return data.decode("utf-8", errors="replace")


def _safe_resolve(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        return path.resolve()
    except OSError:
        return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_msvc_flag(token: str) -> bool:
    """Whether *token* looks like an MSVC/clang-cl ``/``-flag (``/c``,
    ``/Foapp.obj``, ``/EHsc``) rather than a POSIX absolute path operand
    (``/work/build/main.o``).

    A real absolute path always has a directory separator beyond the leading
    ``/``; an MSVC flag never does (CodeRabbit review, PR #632 -- the prior
    blanket ``startswith("/")`` rejection dropped every absolute-path link
    input on POSIX, not just MSVC flags). Reduced-confidence heuristic,
    matching this module's existing dry-run-transcript-scraping posture.
    """
    return token.startswith("/") and "/" not in token[1:]


def _output_from_argv(argv: list[str]) -> str:
    """Extract the object output path from GNU/MSVC compile argv."""
    out = ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-o", "/Fo") and i + 1 < len(argv):
            out = argv[i + 1]
            i += 2
            continue
        if arg.startswith("-o") and len(arg) > 2:
            out = arg[2:]
        elif arg.startswith("/Fo") and len(arg) > 3:
            out = arg[3:]
        i += 1
    return out


def _count_existing_sources(units: list[CompileUnit]) -> int:
    count = 0
    for cu in units:
        source = Path(os.path.expanduser(cu.source))
        if not source.is_absolute() and cu.directory:
            source = Path(os.path.expanduser(cu.directory)) / source
        if source.is_file():
            count += 1
    return count


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
