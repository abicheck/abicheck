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

"""Round 29/30 Codex/CodeRabbit review helpers for ``env``-prefix and
compiler-argv parsing, split into their own leaf module purely to keep
``_argv.py`` (every helper's one caller) under this repo's 2000-line
AI-readiness hard cap. Pure/stdlib-only -- no other dependency, so none of
these carry any of ``_argv.py``'s own documented import-cycle risk with
``header_compile_context.py``.
"""

from __future__ import annotations

import os

#: Single-CHARACTER short flags that may be GROUPED into one clustered
#: token (GNU clustering only ever combines no-operand flags -- excludes
#: every operand-taking short flag: ``-C``/``-u``/``-S``).
CLUSTERABLE_NO_OPERAND_SHORT_FLAGS = frozenset({"i", "v", "0"})


def is_short_flag_cluster(arg: str) -> bool:
    """True when *arg* is a GNU-clustered no-operand short-flag token,
    e.g. ``-iv`` == ``-i -v``."""
    return (
        len(arg) > 1
        and arg[0] == "-"
        and not arg.startswith("--")
        and all(c in CLUSTERABLE_NO_OPERAND_SHORT_FLAGS for c in arg[1:])
    )


def _windows_candidate_names(token: str) -> list[str]:
    """Every basename ``token`` could resolve to on Windows, honoring
    ``PATHEXT`` -- mirrors :func:`shutil.which`'s own extension-appending
    logic for the (deliberately narrower, single-directory) search this
    module does itself below."""
    if os.name != "nt":
        return [token]
    pathext = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    exts = [e for e in pathext.split(os.pathsep) if e]
    _, existing_ext = os.path.splitext(token)
    if existing_ext and existing_ext.lower() in {e.lower() for e in exts}:
        return [token]
    return [token + ext for ext in exts]


def resolve_bare_token_with_default_path(token: str) -> str | None:
    """Resolve against :data:`os.defpath` only, never inherited ``PATH``
    (a real PATH-less ``execvp`` still searches it) -- and, on Windows,
    never the process's own current working directory either (CodeRabbit
    review round 30, fresh evidence).

    ``shutil.which(token, path=...)`` is deliberately NOT used here: on
    Windows, ``os.defpath`` itself starts with ``.`` (the documented
    default is ``".;C:\\bin"``), and even after stripping that,
    ``shutil.which``'s own implementation (confirmed across CPython
    3.10-3.13 source, and the underlying ``NeedCurrentDirectoryForExePathW``
    Windows API behavior it mirrors) can still silently prepend the
    process's CURRENT WORKING DIRECTORY to the search regardless of an
    explicit ``path=`` argument. A real, PATH-less ``env -i``/``env -u
    PATH`` launch does NOT search the cwd either -- only ``os.defpath``'s
    own real directories -- so relying on ``shutil.which`` here could
    resolve a bare driver token against whatever abicheck's own analysis
    process happens to be running from, an unrelated, unintended
    directory. Filters ``.`` and empty components out of ``os.defpath``
    and checks each remaining directory directly (bypassing
    ``shutil.which`` entirely) instead.
    """
    for directory in os.defpath.split(os.pathsep):
        if not directory or directory == ".":
            continue
        for name in _windows_candidate_names(token):
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


#: Source/header suffixes that identify a genuine trailing POSITIONAL
#: compile-unit operand for :func:`~abicheck.buildsource.source_extractors.
#: _argv._fold_chdir_into_operands` (round 30 Finding 4, Codex review, fresh
#: evidence: ``env -C build clang -target armv7-none-eabi -meabi gnu -c
#: x.c`` folded ``gnu`` -- the bare, non-path VALUE of an unrecognized
#: separate-form flag, ``-meabi <value>`` -- into ``build/gnu``, because
#: that function previously treated ANY non-flag-shaped bare token as a
#: foldable positional path, not just a genuine source-file operand).
#: Enumerating every non-path-taking flag one at a time
#: (``_argv.py``'s own ``_CHDIR_FOLD_NON_PATH_VALUE_FLAGS``) is the wrong
#: shape of fix for this class of bug -- it only ever protects the
#: *specific* flags someone has already found and reported, and there are
#: dozens of real compiler flags that take a non-path value this module
#: has no way to enumerate in advance. This set flips the DEFAULT instead:
#: a bare, non-flag-shaped token is folded as a path only when it is
#: independently recognizable as a genuine source/header operand (the
#: actual case ``_fold_chdir_into_operands``'s own motivating example
#: needs -- ``env -C build clang-cl -I../include /c ../src/x.cpp``); every
#: other bare token -- an unrecognized flag's own non-path value included,
#: no matter which flag -- is left exactly as the pre-existing,
#: conservative "unrecognized, don't touch it" fallback that module
#: already uses for everything else. Mirrors
#: ``source_replay._SOURCE_FILE_SUFFIXES`` (kept as an independent
#: literal, not a shared import: ``source_replay.py`` already imports
#: FROM ``_argv.py``, which imports from this module, so a reverse import
#: would be a cycle) -- keep the two lists in sync if either grows.
_SOURCE_FILE_OPERAND_SUFFIXES = (
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".c++",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".h++",
    ".inl",
    ".ipp",
    ".tcc",
    ".cu",
    ".cuh",
    ".m",
    ".mm",
)


def looks_like_source_file_operand(token: str) -> bool:
    """True when *token* -- a bare, non-flag-shaped argv token -- is
    recognizable as a genuine source/header positional operand by its
    suffix, the general replacement for treating every such token as a
    foldable path by default (round 30 Finding 4; see
    :data:`_SOURCE_FILE_OPERAND_SUFFIXES`'s own docstring)."""
    return token.replace("\\", "/").lower().endswith(_SOURCE_FILE_OPERAND_SUFFIXES)
