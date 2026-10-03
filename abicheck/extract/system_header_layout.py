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

"""Path layouts that mark a directory as a target's system include root.

Split from provenance.py (its no-growth debt baseline) when the
Debian/Ubuntu cross-toolchain sysroot joined the layouts it recognizes: the
target-triple grammar, the OS/libc vocabulary that tells a real multiarch tuple
from a hyphenated project directory, and the usr/<triple>/include root.
provenance imports all three back; the classification rules that use them
stay there.
"""

from __future__ import annotations

import re

__all__ = [
    "_MULTIARCH_OS_ENV_MARKERS",
    "_TARGET_TRIPLE_RE",
    "_cross_sysroot_includes",
    "_looks_like_multiarch_component",
]

#: A real GCC/Clang target triple: 2-4 non-empty ``-``-joined components,
#: each alphanumeric (plus ``_``) -- e.g. ``x86_64-conda-linux-gnu``,
#: ``x86_64-pc-linux-gnu``, ``aarch64-linux-gnu``, ``arm-none-eabi``. A
#: non-leading component may also carry dots, since a real OS component can
#: embed a dotted version (Solaris/AIX-style: ``x86_64-pc-solaris2.11``,
#: matching this same repo's own ``toolchain_probe.py`` recognition of that
#: shape -- Codex review, round 4).

_TARGET_TRIPLE_RE = re.compile(r"^[A-Za-z0-9_]+(-[A-Za-z0-9_][A-Za-z0-9_.]*){1,3}$")


#: A genuine Debian/Ubuntu multiarch tuple always names a real OS or
#: libc/environment family as one of its hyphen-separated words (``linux``,
#: ``gnu``/``gnueabihf``/``musl``/...) -- an ordinary project directory that
#: merely happens to be triple-shaped (``my-lib``) never does.
#: ``_TARGET_TRIPLE_RE`` alone is deliberately loose (built to validate a
#: GCC/Clang *toolchain*-controlled triple, where "any 2-4 alnum components"
#: is a reasonable bar), so reusing it unmodified for a directory reached
#: under a widely-installable prefix like ``/usr/include`` accepted an
#: arbitrary two-word project directory too, silently discarding an
#: explicitly-declared ``-I`` as though it were a system path (Codex
#: review, round 6). Not a full Debian arch-name enumeration -- just enough
#: real OS/environment vocabulary to exclude an ordinary project name.
_MULTIARCH_OS_ENV_MARKERS: frozenset[str] = frozenset(
    {
        "linux",
        "gnu",
        "gnueabi",
        "gnueabihf",
        "gnux32",
        "gnuabi64",
        "gnuabin32",
        "musl",
        "android",
        "bsd",
        "freebsd",
        "netbsd",
        "openbsd",
        "darwin",
        "windows",
        "mingw",
        "mingw32",
        "msvc",
        "eabi",
        "eabihf",
    }
)


def _looks_like_multiarch_component(component: str) -> bool:
    """True when *component* is both target-triple-shaped
    (``_TARGET_TRIPLE_RE``) AND names a real OS/libc-environment family as
    one of its hyphen-separated words (see ``_MULTIARCH_OS_ENV_MARKERS``'s
    own docstring for why the shape check alone isn't enough here)."""
    if not _TARGET_TRIPLE_RE.match(component):
        return False
    return any(
        word in _MULTIARCH_OS_ENV_MARKERS for word in component.lower().split("-")
    )


def _cross_sysroot_includes(segs: tuple[str, ...]) -> list[tuple[str, ...]]:
    """``usr/<triple>/include`` roots in *segs*: Debian/Ubuntu's cross-toolchain
    sysroot (``/usr/aarch64-linux-gnu/include``, ``/usr/x86_64-w64-mingw32/
    include``), treated exactly like ``usr/include`` for the target."""
    return [
        segs[i : i + 3]
        for i in range(len(segs) - 2)
        if segs[i] == "usr"
        and segs[i + 2] == "include"
        and _looks_like_multiarch_component(segs[i + 1])
    ]
