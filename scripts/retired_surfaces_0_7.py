#!/usr/bin/env python3
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

"""Retired CLI spellings removed by the ADR-091 D7 no-transition decision.

Split out of ``retired_surfaces.py`` (its file-size record is grandfathered and
may not grow). ``RETIRED_SURFACES`` in that module appends ``REMOVED_IN_0_7``,
so the sweep reads one tuple.
"""

from __future__ import annotations

REMOVED_IN_0_7: tuple[tuple[str, tuple[str, ...], frozenset[str]], ...] = (
    (
        "compare/dump/project history --version (the per-side version label,"
        " renamed --label by ADR-091 D5 R1 and removed outright by its D7"
        " amendment; the root `abicheck --version` is the tool version and"
        " is unaffected)",
        (
            "compare --version",
            "dump --version",
            "project history --version",
        ),
        frozenset({"start/upgrading-to-0.7.md"}),
    ),
    (
        "compare --abi3 (the stable-ABI floor, renamed --abi3-floor by"
        " ADR-091 D5 R12 and removed outright by its D7 amendment)",
        ("compare --abi3 ",),
        frozenset({"start/upgrading-to-0.7.md"}),
    ),
    (
        "deps compare --old-root/--new-root and deps tree --sysroot (renamed"
        " to --root by ADR-091 D5 R16 and removed outright by its D7"
        " amendment)",
        (
            "deps compare --old-root",
            "deps compare --new-root",
            "deps tree --sysroot",
        ),
        frozenset({"start/upgrading-to-0.7.md"}),
    ),
)
