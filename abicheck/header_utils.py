# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Pure path helpers for header (``-H``) inputs.

A leaf module (stdlib-only) so both the service layer (``service._dump_elf``)
and the ``dump`` CLI helper (``cli_dump_helpers.perform_elf_dump``) can share the
include-root derivation without an import cycle (``cli`` → ``cli_dump_helpers`` →
``service`` → … → ``cli``).
"""

from __future__ import annotations

from pathlib import Path

#: Conventional include-root directory names. A ``-H`` umbrella that lives
#: *under* such a directory (e.g. ``include/oneapi/tbb.h``) writes its own
#: includes relative to that root (``#include "oneapi/tbb/..."``), so the root —
#: not the file's immediate parent — is what must be on the search path.
_INCLUDE_ROOT_NAMES = frozenset({"include", "inc"})


def _implicit_header_includes(headers: list[Path]) -> list[Path]:
    """Include directories implied by the ``-H`` inputs themselves.

    A ``-H`` *directory* is its own include root; a ``-H`` *file* contributes
    its parent directory **plus** any ancestor conventionally named ``include``/
    ``inc``. Adding these to the compiler search path lets quote/angle includes
    written relative to the public-header root resolve without a separate ``-I``
    (the abicheck P3 finding) — both the umbrella-at-root case (oneDNN's
    ``include/dnnl.hpp``) and the nested-umbrella case (oneTBB's
    ``include/oneapi/tbb.h`` doing ``#include "oneapi/tbb/blocked_range.h"``).
    Returns existing directories, de-duplicated in discovery order; the user's
    ``-I``/``--include`` entries still take precedence (they are listed first).
    """
    dirs: list[Path] = []
    seen: set[str] = set()

    def _add(d: Path) -> None:
        if not d.is_dir():
            return
        key = str(d.resolve())
        if key not in seen:
            seen.add(key)
            dirs.append(d)

    for h in headers:
        # A directory is its own root; a file contributes its parent. Either way
        # also walk up to any conventional include root — a `-H include/oneapi`
        # (dir) or `-H include/oneapi/tbb.h` (file) still writes includes
        # relative to `include/`, so that root must be on the path too.
        _add(h if h.is_dir() else h.parent)
        for ancestor in h.parents:
            if ancestor.name.lower() in _INCLUDE_ROOT_NAMES:
                _add(ancestor)
    return dirs
