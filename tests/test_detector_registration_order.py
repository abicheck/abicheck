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

"""Detector registration order is decided by ``checker.py``'s imports.

Registration order is detector priority order: it is the order findings are
produced in, the order reports list detectors in, and the order
cross-detector dedup resolves a tie in (the first finding wins). It is a
side effect of which module ``checker.py`` imports first, so deleting an
import that looks unused can reorder it with no other failure. The
finding-disposition refactor did exactly that once (dropping a re-export
import of ``diff_filtering``, which pulled in ``diff_symbols``), and only a
before/after comparison of real reports caught it.

Checked in a fresh interpreter: inside the test session, whichever test
imported a detector module first would decide the order.
"""

from __future__ import annotations

import json
import subprocess
import sys

_PROBE = (
    "import json, abicheck.checker\n"
    "from abicheck.detector_registry import registry\n"
    "registry.ensure_loaded()\n"
    "print(json.dumps(list(registry.detector_names)))\n"
)

#: Pairs (earlier, later) of detector names whose relative order is the
#: priority the comparison has always run with. The symbol-level detectors
#: (`diff_symbols*`) run right after the binary-only ELF layout pass and
#: before every layout/platform detector.
_ORDERED_PAIRS = [
    ("elf_layout", "fingerprint_renames"),
    ("fingerprint_renames", "functions"),
    ("functions", "variables"),
    ("var_access", "kabi"),
    ("variables", "kabi"),
    ("kabi", "layout_descriptor"),
    ("long_double", "template_inner_types"),
    ("functions", "elf"),
]


def test_symbol_detectors_register_between_elf_layout_and_kabi() -> None:
    names = json.loads(
        subprocess.run(
            [sys.executable, "-c", _PROBE],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    position = {name: i for i, name in enumerate(names)}
    for earlier, later in _ORDERED_PAIRS:
        assert position[earlier] < position[later], (earlier, later, names)
