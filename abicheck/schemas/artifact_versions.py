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

"""The current-version lookup behind ``abicheck.schemas.current``.

Moved out of ``schemas/__init__.py`` (a ``no_growth`` architecture-debt file
with a long changelog). The lookup is a responsibility of its own: it names
the artifacts and asks each owning module for its current version. The two
version constants that live in ``__init__`` are passed in by the caller, so
this module imports nothing from the package it serves (no import cycle).
"""

from __future__ import annotations

_ARTIFACT_NAMES = frozenset(
    {
        "snapshot",
        "compare",
        "audit",
        "audit-set",
        "aggregate",
        "build-output",
        "run-plan",
        "release",
    }
)


def current_artifact_version(
    name: str, *, report_version: str, release_version: str
) -> str | int:
    """Return the current version abicheck emits for persisted artifact *name*.

    One read-only lookup facade over the version constants each artifact's
    own module already owns (ADR-055 D3) -- current-version discovery only.
    *name* is one of ``"snapshot"``, ``"compare"``, ``"audit"``
    (``compare --no-baseline``), ``"audit-set"`` (``compare --no-baseline
    DIR``), ``"release"``, ``"aggregate"``,
    ``"build-output"``, or ``"run-plan"``. ``"scan"`` was retired with the
    ``scan`` command itself (ADR-068 Phase 6).

    A doc generator can pull every current version number from here instead
    of a human hand-copying one -- the exact failure mode that let
    ``docs/use/python-api.md`` claim snapshots carried ``schema_version 8``
    long after the real value reached 17.
    """
    if name not in _ARTIFACT_NAMES:
        raise ValueError(
            f"Unknown schema artifact {name!r}; expected one of "
            f"{sorted(_ARTIFACT_NAMES)}"
        )
    if name == "audit":
        from ..report.no_baseline_document import AUDIT_REPORT_SCHEMA_VERSION

        return AUDIT_REPORT_SCHEMA_VERSION
    if name == "audit-set":
        from ..report.no_baseline_document import AUDIT_SET_REPORT_SCHEMA_VERSION

        return AUDIT_SET_REPORT_SCHEMA_VERSION
    if name == "snapshot":
        from ..serialization import SCHEMA_VERSION

        return SCHEMA_VERSION
    if name == "compare":
        return report_version
    if name == "release":
        return release_version
    if name == "aggregate":
        from ..workflows.aggregate import AGGREGATE_SCHEMA_VERSION

        return AGGREGATE_SCHEMA_VERSION
    if name == "build-output":
        from ..buildsource.build_output import BUILD_OUTPUT_SCHEMA

        return BUILD_OUTPUT_SCHEMA
    # The *highest* of the three conditional stamps, not the base
    # RUN_PLAN_SCHEMA -- a run-plan.json declares one of
    # RUN_PLAN_SCHEMA/`_GATE`/`_SKIPPED` depending on whether it carries a
    # `gate` block or a `skipped` block, so there is no single fixed "the"
    # version this artifact always emits. The highest version abicheck can
    # produce is the truthful answer a doc generator needs to be prepared to
    # parse, not a lower, conditionally-emitted one (Codex review; raised to
    # v3 with plan slice 7r's `skipped` block, since a registry still saying
    # v2 tells a consumer it need only support v2 against a build that can
    # emit v3).
    from ..buildsource.run_plan_schema import RUN_PLAN_SCHEMA_SKIPPED

    return RUN_PLAN_SCHEMA_SKIPPED
