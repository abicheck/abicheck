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

"""``run-plan.json``'s schema discriminators, and what each one gates.

Split out of :mod:`abicheck.buildsource.run_plan` (an `architecture/
debt.yaml` ``no_growth`` module) because this is its own question: which
version string a plan declares, which capability each version admits, and
what an older reader is guaranteed to reject. The answers are long-form by
necessity -- each bump exists to stop a specific silent misread -- and they
change on a different schedule from the cell-resolution logic that fills a
plan in.

A leaf: it imports nothing of this package, so both `run_plan.py` and
`run_plan_skip.py` can depend on it without either depending on the other.
`run_plan.py` re-exports every name here.
"""

from __future__ import annotations

#: Schema discriminator stamped into every ``run-plan.json`` (mirrors
#: ``BUILD_OUTPUT_SCHEMA``'s naming convention).
RUN_PLAN_SCHEMA = "abicheck.run-plan/v1"

#: Schema discriminator stamped instead of :data:`RUN_PLAN_SCHEMA` whenever a
#: plan carries a ``gate`` block (CLI cleanup phase two, PR 2 continuation).
#: Mirrors ``AGGREGATE_MANIFEST_VERSION``'s MAJOR-bump reasoning exactly: a
#: plan generated with an explicit gate policy must declare a schema an old,
#: pre-gate reader is guaranteed to reject, rather than one it silently
#: accepts and misreads (Codex review, fresh evidence -- an earlier revision
#: left every plan stamped ``v1`` regardless of whether ``gate`` was
#: present, so an old ``RunPlan.from_dict()`` would ignore the unknown key
#: and project a ``1.0`` aggregate manifest applying the hard-coded default
#: policy instead of what the plan actually asked for, silently). A plan
#: with no gate policy keeps the unchanged ``v1`` spelling -- this bump is
#: additive-only, scoped to the one new capability, not a blanket
#: version-everything policy.
RUN_PLAN_SCHEMA_GATE = "abicheck.run-plan/v2"


#: Highest ``vN`` suffix this reader understands, parsed from the schema
#: constants above.
_RUN_PLAN_SCHEMA_MAX_SUPPORTED = 3


def _run_plan_schema_version(schema: str) -> int | None:
    """Parse the trailing ``vN`` off a ``run-plan.json`` ``schema`` string.

    ``None`` for anything not of the ``"abicheck.run-plan/vN"`` shape --
    callers treat that the same as "no version to check" (an unrecognized
    schema string is a separate, pre-existing problem this function doesn't
    try to diagnose).
    """
    prefix = "abicheck.run-plan/v"
    if not schema.startswith(prefix):
        return None
    suffix = schema[len(prefix) :]
    return int(suffix) if suffix.isdigit() else None


#: Schema discriminator stamped instead of the two above whenever a plan
#: carries a ``skipped`` block (plan slice 7r: a legitimately empty
#: selection is now an *explained skipped plan* rather than an error a
#: ``--allow-empty`` bypass switch had to wave through). Same MAJOR-bump
#: reasoning as ``RUN_PLAN_SCHEMA_GATE``: a consumer that cannot read
#: ``skipped`` sees only ``checks: []`` and has no way to tell a deliberate,
#: explained skip from a plan whose every check failed to resolve -- which
#: is exactly the distinction this slice exists to make, so it must fail
#: loudly rather than read the artifact as the other case. A plan carrying
#: checks keeps its existing ``v1``/``v2`` spelling; the bump is
#: additive-only and scoped to this one capability.
RUN_PLAN_SCHEMA_SKIPPED = "abicheck.run-plan/v3"
