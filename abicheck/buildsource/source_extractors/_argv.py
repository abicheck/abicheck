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

"""Shared compile-context → argv helpers for source ABI extractors (ADR-030 D2).

Every extractor backend must replay a translation unit under the *same* compile
context the real build used — same compiler emulation, language standard,
defines, include paths, forced includes, sysroot/target, and ABI-relevant flags
(ADR-030 D2). castxml (phase 2) and clang (phase 5) need identical logic for the
fiddly parts — unwrapping ``ccache``/``sccache`` launchers, detecting MSVC mode
from a (possibly Windows, possibly cross) compiler path, carrying argv-only
forced includes, and reversing the redaction policy's ``~`` home placeholder for
the replay only (ADR-032 D7). Keeping that here means one tested implementation,
not one per backend.

Pure and tool-independent: nothing here shells out.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from ..build_evidence import CompileUnit

#: Languages that make the GNU fallback compiler ``g++`` rather than ``gcc``.
CXX_LANGS = frozenset({"cxx", "c++", "cpp"})
#: Compiler basenames that mean the extractor should run in MSVC mode.
MSVC_BINARIES = frozenset({"cl", "cl.exe", "clang-cl", "clang-cl.exe"})
#: Compiler-launcher wrappers that prefix the real compiler in a build action
#: (``ccache clang++ -c foo.cpp``). The extractor must emulate the real compiler,
#: not the launcher, which would otherwise run without its compiler operand.
COMPILER_LAUNCHERS = frozenset(
    {"ccache", "sccache", "distcc", "icecc", "icerun", "buildcache"}
)
#: Preprocessor macro define/undef option prefixes. Their *values* reach the
#: compiler verbatim (argv, no shell expansion), so a literal ``~`` in e.g.
#: ``-DDEFAULT_DIR=~/app`` must NOT be home-expanded during replay — unlike the
#: path operands (includes/sysroot/source), which carry redacted home prefixes.
MACRO_DEFINITION_PREFIXES = ("-D", "-U", "/D", "/U")
#: Value-taking toolchain flags already normalized into the structured
#: ``sysroot``/``target_triple`` fields. They must NOT be carried through from
#: ``abi_relevant_flags``: the adapter records only the bare option token for the
#: split spelling (``-isysroot /sdk`` → just ``-isysroot``, operand dropped), so
#: re-appending it dangles and swallows the following argv token.
STRUCTURED_TOOLCHAIN_FLAG_PREFIXES = ("--sysroot", "-isysroot", "--target", "-target")
#: GNU forced-include options. Only ``-include``/``-imacros`` also have a joined
#: ``-include<file>`` spelling; ``-include-pch`` is separate-operand only (clang
#: ``-include-pch <file>``) and must not be read as a joined ``-include``.
_GNU_FORCED_INCLUDE_OPTS = frozenset({"-include", "-imacros"})
_GNU_SEPARATE_INCLUDE_OPTS = frozenset({"-include", "-imacros", "-include-pch"})
#: MSVC/clang-cl forced-include options in their separate-operand spelling
#: (``/FI file`` or ``-FI file``); the joined ``/FIfile`` form is handled by
#: prefix.
_MSVC_FORCED_INCLUDE_OPTS = frozenset({"/FI", "-FI"})
#: GNU include-search options that take a directory operand and are NOT
#: normalized into the structured ``include_paths``/``system_include_paths``
#: buckets (those cover ``-I``/``-isystem`` only). Dropping them makes the
#: extractor search a different set of directories than the real compile (Codex
#: review #335). Both the separate (``-iquote dir``) and joined (``-iquote/dir``)
#: spellings are carried through.
#: (https://gcc.gnu.org/onlinedocs/gcc/Directory-Options.html)
_GNU_INCLUDE_SEARCH_OPTS = frozenset({"-iquote", "-idirafter"})


def unredact_home(value: str) -> str:
    """Expand a redacted home placeholder (``~``) back to the real home dir.

    The evidence redaction policy (ADR-032 D7) rewrites the user's home prefix
    to ``~`` wherever it appears before persisting paths/argv. ``subprocess``
    does not expand ``~`` (no shell), so replaying a redacted ``CompileUnit``
    would treat ``~/...`` / ``-I~/...`` as literal paths and fail. Reverse the
    substitution for the replay only (persisted values stay redacted).

    Only a ``~`` that stands in for a home *directory* is expanded: the
    placeholder is always either the whole token or immediately followed by a
    path separator. A ``~`` followed by anything else is untouched, so Windows
    8.3 short names such as ``RUNNER~1`` are never mangled.
    """
    if "~" not in value:
        return value
    home = os.path.expanduser("~")
    if not home or home == "~":
        return value
    return re.sub(r"~(?=[\\/]|$)", lambda _m: home, value)


def split_public_roots(roots: Sequence[str]) -> tuple[list[str], list[str]]:
    """Partition public-header roots into ``(file_roots, dir_roots)``.

    The CLI accepts a public header *file or directory* (``--headers include/``).
    ``provenance.build_public_set`` needs files and directories in separate
    arguments — a directory passed as a "header" file never suffix-matches a decl
    under it (``include`` vs ``include/api.h``), so the whole public include tree
    would be classified non-public and dropped (Codex review #339, P2). A root is
    a directory when it ends in a path separator or resolves to a directory on
    disk (un-redacting a ``~`` home placeholder first, ADR-032 D7). The original
    (unexpanded) root string is kept for segment matching, which compares against
    the paths the compiler actually reports.
    """
    files: list[str] = []
    dirs: list[str] = []
    for root in roots:
        if not root:
            continue
        expanded = os.path.expanduser(unredact_home(root))
        if root.endswith(("/", "\\")) or os.path.isdir(expanded):
            dirs.append(root)
        else:
            files.append(root)
    return files, dirs


def resolve_read_files(files: set[str], directory: str) -> list[str]:
    """Absolute, de-duplicated read-file paths resolved against ``directory``.

    An extractor records the files it read (``SourceAbiTu.read_files``) for the
    per-TU cache dependency set (ADR-030 D8). A compiler emits *relative* paths
    for headers found via a relative ``-I``, which the cache — running in a
    possibly different CWD — could not otherwise read, silently dropping the
    dependency. Resolve each against the TU's build directory (un-redacting a
    ``~`` home placeholder first, ADR-032 D7) so the path matches the CWD the
    tool actually ran in.
    """
    base = unredact_home(directory) if directory else ""
    out: set[str] = set()
    for f in files:
        path = os.path.expanduser(unredact_home(f))
        if not os.path.isabs(path) and base:
            path = os.path.join(base, path)
        out.add(os.path.normpath(path))
    return sorted(out)


def basename(path: str) -> str:
    """Final path component, splitting on both ``/`` and ``\\`` (host-independent).

    ``Path(path).name`` on POSIX does not treat ``\\`` as a separator, so a
    Windows compiler path from a cross/off-Windows compile database
    (``C:\\VS\\bin\\cl.exe``) would otherwise return the whole string and miss
    MSVC-mode detection.
    """
    return re.split(r"[\\/]", path)[-1]


def strip_launchers(argv: list[str]) -> list[str]:
    """Drop leading compiler-launcher tokens (``ccache``/``sccache``/…)."""
    i = 0
    while i < len(argv) and basename(argv[i]).lower().removesuffix(
        ".exe"
    ) in COMPILER_LAUNCHERS:
        i += 1
    return argv[i:]


def pick_compiler_binary(compile_unit: CompileUnit, override: str | None) -> str:
    """Pick the compiler binary an extractor should emulate for this TU.

    Prefers the compiler actually recorded in the build action (``argv[0]``,
    after unwrapping any launcher) so a clang/clang-cl/cross TU is replayed
    against its real builtin include paths, target defaults, and accepted flags.
    Falls back to g++/gcc by language when no command is available; an explicit
    ``override`` always wins.
    """
    if override:
        return override
    argv = strip_launchers(compile_unit.argv)
    if argv and argv[0] and not argv[0].startswith("-"):
        return argv[0]
    return "g++" if compile_unit.language.lower() in CXX_LANGS else "gcc"


def is_msvc_mode(cc_bin: str) -> bool:
    """Whether the compiler basename means MSVC (``cl``/``clang-cl``) mode."""
    return basename(cc_bin).lower() in MSVC_BINARIES


def replay_extra_flags(
    compile_unit: CompileUnit, already: list[str], cc_id: str
) -> list[str]:
    """Carry through ABI/parse-relevant options not in the structured fields.

    ``abi_relevant_flags`` (e.g. ``-fms-extensions``, ``-fabi-version``,
    ``-fvisibility``, ``-m32``), forced-include options, and unnormalized
    include-search options (GNU ``-iquote``/``-idirafter``, MSVC ``/I``) from
    ``argv`` change the parsed translation unit / header search; dropping them
    makes the extractor parse a different TU than the real build (ADR-030 D2).
    De-duplicated against the flags already emitted from the structured fields.
    MSVC ``/FI`` forced includes and ``/I`` search dirs are carried only in MSVC
    mode so a GNU ``-F``/``-I``-family flag is never mistaken for one.
    """
    seen = set(already)
    out: list[str] = []
    for flag in compile_unit.abi_relevant_flags:
        if flag.startswith(STRUCTURED_TOOLCHAIN_FLAG_PREFIXES):
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
        elif tok in _GNU_INCLUDE_SEARCH_OPTS and i + 1 < len(argv):
            out += [tok, argv[i + 1]]  # -iquote / -idirafter <dir> (separate)
            i += 2
        elif tok not in _GNU_INCLUDE_SEARCH_OPTS and any(
            tok.startswith(opt) and len(tok) > len(opt)
            for opt in _GNU_INCLUDE_SEARCH_OPTS
        ):
            out.append(tok)  # -iquote/dir / -idirafter/dir (joined)
            i += 1
        elif cc_id == "msvc" and tok == "/I" and i + 1 < len(argv):
            out += [tok, argv[i + 1]]  # MSVC /I dir (separate operand)
            i += 2
        elif cc_id == "msvc" and len(tok) > 2 and tok.startswith("/I"):
            out.append(tok)  # MSVC /Idir (joined)
            i += 1
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
