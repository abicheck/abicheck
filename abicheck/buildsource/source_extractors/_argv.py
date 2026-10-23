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
fiddly parts — unwrapping ``env``/``ccache``/``sccache`` launchers, detecting MSVC mode
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

from ...header_utils import is_msvc_driver_stem
from ._argv_shortopts import (
    is_absolute_path_token as is_absolute_path_token,
    join_path_token as join_path_token,
    normalize_path_token as normalize_path_token,
    resolve_bare_token_with_default_path as resolve_bare_token_with_default_path,
)

#: Languages that make the GNU fallback compiler ``g++`` rather than ``gcc``.
CXX_LANGS = frozenset({"cxx", "c++", "cpp"})
STRUCTURED_TOOLCHAIN_FLAG_PREFIXES = ("--sysroot", "-isysroot", "--target", "-target")


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


def is_msvc_mode(cc_bin: str) -> bool:
    """Whether the compiler basename means MSVC (``cl``/``clang-cl``) mode.

    Checks the exact (possibly ``.exe``-suffixed) name first, then falls
    back to the same check with a trailing version suffix stripped, so a
    packaged ``clang-cl-20``/``clang-cl-20.exe`` is still recognized.
    """
    # The name test itself is header_utils.is_msvc_driver_stem -- the one
    # vocabulary buildsource.adapters.adapter_contract._is_msvc_command now shares, after
    # the two drifted apart (Codex review, PR D). Behaviour here is unchanged:
    # that function is this test, relocated. Only the basename derivation
    # stays local, since the adapter's is backslash-aware and this one is not.
    return is_msvc_driver_stem(basename(cc_bin).lower())
