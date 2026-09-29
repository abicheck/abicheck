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

"""Which header files/directories a compiler-option list makes the parser read.

The whole-snapshot cache keys a ``CompileContext`` by its spelling, but the
header *content* those options point at (``--sysroot``, ``-I``, ``-isystem``,
``-include``, ...) is what the parse actually consumes. This module reads the
option list and returns those paths so the cache can content-hash them with
the same include-tree walk (:mod:`abicheck.extract.cache_header_scan`) it
already applies to ``-I`` directories. An option whose header reach that walk
cannot follow makes the answer ``None`` (do not cache) rather than a guess.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

__all__ = ["compile_option_header_inputs"]

#: Compiler options whose value is a directory searched for headers. Hashed
#: through the same include-tree walk ``-I`` directories already get.
_DIR_OPTIONS: tuple[str, ...] = (
    "--include-directory",
    "--sysroot",
    "-cxx-isystem",
    "-iframework",
    "-idirafter",
    "-isysroot",
    "-isystem",
    "-iquote",
    "-F",
    "-I",
)
#: Compiler options whose value is one file the preprocessor reads.
_FILE_OPTIONS: tuple[str, ...] = ("--include", "-imacros", "-include")
_PATH_OPTIONS: tuple[str, ...] = tuple(
    sorted((*_DIR_OPTIONS, *_FILE_OPTIONS), key=len, reverse=True)
)
#: Options that pull header content from somewhere the include-tree walk
#: cannot follow (a prefix applied to later options, a precompiled header, a
#: VFS overlay, a module map, a response file). Their presence makes the
#: context uncacheable rather than keyed on a path whose reach is unknown.
_UNTRACKED_PREFIXES: tuple[str, ...] = (
    "-iwithprefixbefore",
    "-iwithprefix",
    "-iprefix",
    "-include-pch",
    "-ivfsoverlay",
    "-fmodule-map-file",
    "-fmodules-cache-path",
    "-fprebuilt-module-path",
    "--config",
    "@",
)


def compile_option_header_inputs(
    tokens: Sequence[str], *, sysroot: Path | None = None
) -> tuple[list[Path], list[Path]] | None:
    """``(files, dirs)`` of header inputs named by *tokens* (and *sysroot*).

    ``files`` are forced-include files (``-include``/``-imacros``), ``dirs``
    header search roots, both in command-line order. Each option is accepted
    as a separate operand, joined (``-Idir``) or ``=``-joined
    (``--sysroot=dir``). ``None`` when a token names an untrackable input
    (:data:`_UNTRACKED_PREFIXES`) or a path option has no operand.
    """
    files: list[Path] = []
    dirs: list[Path] = [Path(sysroot)] if sysroot is not None else []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if tok.startswith(_UNTRACKED_PREFIXES):
            return None
        # Longest spelling first, across both tables: ``--include`` is a
        # prefix of ``--include-directory``.
        opt = next((o for o in _PATH_OPTIONS if tok.startswith(o)), None)
        if opt is None:
            continue
        value = tok[len(opt) :]
        if value.startswith("="):
            value = value[1:]
        if not value:
            if i >= len(tokens):
                return None  # dangling option: its operand is unknown
            value = tokens[i]
            i += 1
        (files if opt in _FILE_OPTIONS else dirs).append(Path(value))
    return files, dirs
