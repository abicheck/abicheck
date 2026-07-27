# Copyright 2026 Nikolay Petrov
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

"""Unit tests for abicheck.schemas.current (ADR-055 D3)."""

from __future__ import annotations

import pytest

from abicheck import schemas
from abicheck.aggregate import AGGREGATE_SCHEMA_VERSION
from abicheck.buildsource.build_output import BUILD_OUTPUT_SCHEMA
from abicheck.buildsource.run_plan import RUN_PLAN_SCHEMA
from abicheck.serialization import SCHEMA_VERSION


class TestSchemasCurrent:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("snapshot", SCHEMA_VERSION),
            ("compare", schemas.REPORT_SCHEMA_VERSION),
            ("scan", schemas.SCAN_SCHEMA_VERSION),
            ("aggregate", AGGREGATE_SCHEMA_VERSION),
            ("build-output", BUILD_OUTPUT_SCHEMA),
            ("run-plan", RUN_PLAN_SCHEMA),
        ],
    )
    def test_current_matches_the_owning_constant(self, name, expected):
        # This is the whole point of the registry: it must never drift from
        # the constant each artifact's own module actually owns (ADR-055
        # D3) -- a read-only lookup facade, not a second source of truth.
        assert schemas.current(name) == expected

    def test_unknown_artifact_name_is_a_value_error(self):
        with pytest.raises(ValueError, match="Unknown schema artifact"):
            schemas.current("does-not-exist")

    def test_snapshot_version_is_an_int(self):
        # docs/use/python-api.md's stale "schema_version 8" bug happened
        # because a human hand-copied this number instead of reading it
        # from serialization.SCHEMA_VERSION -- guard the type too, not just
        # the value, so a docs generator can format it without guessing.
        assert isinstance(schemas.current("snapshot"), int)

    @pytest.mark.parametrize("name", ["compare", "scan", "build-output", "run-plan"])
    def test_other_versions_are_strings(self, name):
        assert isinstance(schemas.current(name), str)
