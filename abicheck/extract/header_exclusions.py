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

"""Removing named headers from a resolved ``-H`` operand.

A header *directory* operand is otherwise all-or-nothing, and a real release
tree routinely cannot be parsed whole: Intel MKL's ``include/`` ships FFTW2
and FFTW3 headers that declare conflicting typedefs, so ``-H include/``
fails outright with no flag, config key or descriptor element able to
rescue it.

Filtering happens *after* the directory walk, on the resolved list, which is
what keeps it cache-correct with no cache-key change: both header-parse
cache keys (``extract/cache_header_scan.py``, ``snapshot_cache``) already
hash the resolved header list, and a run with no pattern passes its list
through untouched, so no warm cache entry is invalidated.

Split out of ``header_utils.py``, a ``legacy_generic_modules`` grab-bag
carrying a ``no_growth`` baseline; reading and narrowing header inputs is
``extract``'s job under ADR-061's routing table in any case.
"""

from __future__ import annotations

from fnmatch import fnmatch
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def apply_header_exclusions(
    headers: Sequence[Path], patterns: Sequence[str]
) -> list[Path]:
    """*headers* minus every entry matching one of *patterns*.

    The one shared implementation of ``--exclude-header``. A pattern is
    fnmatch-style and is tried against three spellings of each header, so a
    user does not have to know which one this codebase happens to carry:

    * the bare file name (``fftw3.h``),
    * the full path (``/opt/include/fftw/fftw3.h``), and
    * the path with ``**/`` semantics (``**/fftw/*`` matching any depth).

    Why this exists at all: a header *directory* operand is all-or-nothing
    without it. A library whose public include tree contains two headers that
    cannot be parsed in the same translation unit -- the FFTW2/FFTW3 typedef
    clash in Intel MKL's ``include/`` is the reported case -- makes
    ``-H include/`` fail outright, and before this there was no flag, config
    key, or descriptor element anywhere in abicheck that could exclude one
    header from a parse. The only options were to name every other header
    individually or to give up on header-aware analysis for that library.

    Deliberately a *path* filter and nothing more. It does not know about
    ABI visibility, public/private surface, or include graphs -- excluding a
    header removes it from the parsed translation unit, and anything only it
    declared is then simply not observed, which the surface/evidence layers
    already know how to report as reduced evidence rather than as removal.
    """
    if not patterns:
        return list(headers)
    kept: list[Path] = []
    for h in headers:
        text = str(h)
        name = h.name
        if any(
            fnmatch(name, pat) or fnmatch(text, pat) or fnmatch(text, f"*/{pat}")
            for pat in patterns
        ):
            continue
        kept.append(h)
    return kept
