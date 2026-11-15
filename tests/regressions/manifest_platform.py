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

"""Bug classes about a detector that reads one object format and misses another.

A sibling of the ``manifest_*.py`` modules (see ``manifest.py``). Split out of
``manifest_evidence.py`` to keep that module under its test-file cap.
"""

from __future__ import annotations

from .bug_class_schema import BugClass

PLATFORM_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="macho.layout_detector_blind",
        invariant=(
            "A layout detector runs on every object format whose facts it can "
            "read: a Mach-O pair yields the same VTABLE_SLOT_COUNT_CHANGED "
            "findings as the same logical change on ELF, under both fixup "
            "encodings, and a Mach-O side whose sizes were not derived is not "
            "evaluated, never a clean zero. Oracle: literal ELF sizes. Family F8."
        ),
        fixed_by=(2068,),
        seed_tests=(
            "tests/test_family_f8_macho_layout.py",
            "tests/test_macho_layout_detector.py",
        ),
        axes={
            "format": ("elf", "macho"),
            "sizes": ("grow", "shrink", "unchanged"),
            "fixup encoding": ("classic", "chained"),
        },
    ),
)
