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

"""Detector execution order must not depend on import order.

Execution order is detector priority order: it is the order findings are
produced in, the order reports list detectors in, and the order
cross-detector dedup resolves a tie in (the first finding wins). It used to be
a side effect of which module a process imported first, so deleting an import
from ``checker.py`` that looked unused reordered every report -- and a process
that imported ``diff_symbols`` before ``checker`` (a test file, a script, the
mutation lane's traced run) rendered different goldens than one that did not.
``detector_registry.DETECTOR_MODULE_ORDER`` now states the order.

Each probe runs in a fresh interpreter: inside the test session, whichever
test imported a detector module first would decide the answer.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from abicheck.detector_registry import DETECTOR_MODULE_ORDER

_PROBE = (
    "import importlib, json, sys\n"
    "for name in sys.argv[1:]:\n"
    "    importlib.import_module(name)\n"
    "import abicheck.checker\n"
    "from abicheck.detector_registry import registry\n"
    "registry.ensure_loaded()\n"
    "print(json.dumps({\n"
    "    'names': list(registry.detector_names),\n"
    "    'modules': sorted({e.fn.__module__ for e in registry._detectors}),\n"
    "}))\n"
)


def _probe(*first: str) -> dict[str, list[str]]:
    return json.loads(
        subprocess.run(
            [sys.executable, "-c", _PROBE, *first],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )


#: The order the comparison ran detectors in before the order was declared
#: (recorded from ``main`` with ``checker`` imported first), as anchor pairs:
#: an oracle independent of ``DETECTOR_MODULE_ORDER`` itself.
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


@pytest.fixture(scope="module")
def canonical() -> dict[str, list[str]]:
    return _probe()


def test_checker_first_order_matches_the_recorded_priority(canonical) -> None:
    position = {name: i for i, name in enumerate(canonical["names"])}
    for earlier, later in _ORDERED_PAIRS:
        assert position[earlier] < position[later], (earlier, later)


def test_every_detector_host_is_listed(canonical) -> None:
    """An unlisted module would silently rank after every listed one."""
    missing = set(canonical["modules"]) - set(DETECTOR_MODULE_ORDER)
    assert not missing, (
        f"add {sorted(missing)} to detector_registry.DETECTOR_MODULE_ORDER "
        "at the priority its detectors should run"
    )
    assert len(set(DETECTOR_MODULE_ORDER)) == len(DETECTOR_MODULE_ORDER)


@pytest.mark.parametrize(
    "first",
    [
        *DETECTOR_MODULE_ORDER,
        # Not detector hosts, but each imports one transitively.
        "abicheck.diff_filtering",
        "abicheck.post_processing",
        "abicheck.compare.finding_dedup",
        "abicheck.policy.finding_disposition",
    ],
)
def test_importing_any_module_first_keeps_the_order(canonical, first: str) -> None:
    assert _probe(first)["names"] == canonical["names"]
