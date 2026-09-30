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
"""The check-target envelope's ``profile_build_system`` block (WS-A, report
schema 5.10): which build system/generator produced the profile a check cell
ran under, so a cell's findings are attributed to its own build lane rather
than to whatever build a sibling cell of the same target used.
"""

from __future__ import annotations

from typing import Any


def stamp_profile_build_system(
    report: dict[str, Any], *, name: str, generator: str
) -> None:
    """Set ``report["profile_build_system"]`` when *name* is non-empty.

    Absent (not ``null``, not an empty object) when the profile declared no
    build system: unrecorded is never reported as a value.
    """
    name, generator = name.strip(), generator.strip()
    if name:
        report["profile_build_system"] = {"name": name, "generator": generator}
