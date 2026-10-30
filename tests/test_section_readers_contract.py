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

"""`compare/section_readers.py` against an AST oracle (ADR-062 D14).

An analysis is blocked when a stored section it reads could not be used, so
the declared table must be exactly what each detector's code reads -- a
missing entry would run a detector on defaults standing in for evidence (a
manufactured removal), an extra one would block a detector needlessly.
"""

from __future__ import annotations

import dataclasses

import pytest

from abicheck.compare.section_readers import (
    DETECTOR_SECTIONS,
    RULE_SECTIONS,
    SURFACE_METRICS_RULE,
    unreadable_sections_read,
)
from abicheck.detector_registry import registry
from abicheck.model.snapshot import AbiSnapshot
from abicheck.storage.dto import SECTION_SCHEMA_VERSIONS
from tests._section_read_scan import section_of, sections_read

registry.ensure_loaded()
_DETECTORS = {e.name: e for e in registry._detectors}


def test_every_registered_detector_declares_its_sections() -> None:
    assert set(DETECTOR_SECTIONS) == set(_DETECTORS)


@pytest.mark.parametrize("name", sorted(DETECTOR_SECTIONS))
def test_declared_sections_match_the_code(name: str) -> None:
    assert DETECTOR_SECTIONS[name] == sections_read(_DETECTORS[name].fn)


def test_surface_metrics_rule_matches_the_code() -> None:
    from abicheck.diff_surface_metrics import diff_surface_metrics

    assert sections_read(diff_surface_metrics) == RULE_SECTIONS[SURFACE_METRICS_RULE]


def test_declared_sections_are_real_section_kinds() -> None:
    for sections in (*DETECTOR_SECTIONS.values(), *RULE_SECTIONS.values()):
        assert sections <= set(SECTION_SCHEMA_VERSIONS)


def test_every_stored_snapshot_field_has_a_section() -> None:
    """The oracle's own partition covers every persisted field, so a new
    field cannot fall outside it and escape blocking."""
    from abicheck.storage.snapshot_runtime_only import RUNTIME_ONLY_SNAPSHOT_FIELDS

    for f in dataclasses.fields(AbiSnapshot):
        if f.name in RUNTIME_ONLY_SNAPSHOT_FIELDS or f.name.startswith("_"):
            continue
        assert section_of(f.name) is not None or f.name == "declarations", f.name


class _Snap:
    def __init__(self, unreadable: tuple[tuple[str, str], ...]) -> None:
        self.unreadable_sections = unreadable


@pytest.mark.parametrize("section", sorted(SECTION_SCHEMA_VERSIONS))
def test_blocking_follows_the_table(section: str) -> None:
    blocked = _Snap(((section, f"{section} unreadable"),))
    for name, reads in DETECTOR_SECTIONS.items():
        hit = unreadable_sections_read(name, _Snap(()), blocked)
        assert bool(hit) == (section in reads), name


def test_an_unregistered_name_is_blocked_by_any_section() -> None:
    assert unreadable_sections_read("no_such_detector", _Snap((("build", "x"),)))
