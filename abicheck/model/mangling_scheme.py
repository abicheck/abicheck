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

"""Which C++ mangling scheme an export-table spelling uses, format-neutrally.

ELF exports Itanium spellings as ``_Z…``; Mach-O readers store the same
spelling once-stripped of the platform underscore, but a hand-built or
foreign snapshot may still carry the decorated ``__Z…``; PE exports MSVC
decorated names, which start with ``?``. Anything else is an undecorated
(C, or ``extern "C"``) name.
"""

from __future__ import annotations


def itanium_encoding(name: str) -> str | None:
    """*name* as a pure Itanium ``_Z…`` spelling (a still-decorated Mach-O
    ``__Z…`` loses its platform underscore), or None when it is not one."""
    if name.startswith("__Z"):
        return name[1:]
    if name.startswith("_Z"):
        return name
    return None


def is_msvc_decorated(name: str) -> bool:
    """Whether *name* is an MSVC C++ decorated name (``?…``)."""
    return name.startswith("?")
