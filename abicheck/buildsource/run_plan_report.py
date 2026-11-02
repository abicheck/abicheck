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
"""Run-plan generation's report (:class:`RunPlanGenerationReport`), and how a
``checks:`` entry selects its profiles.

Split out of :mod:`abicheck.buildsource.run_plan` (an ``architecture/
debt.yaml`` ``no_growth`` module) so the target and bundle cell generators
there can grow fields without growing that file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .project_check_spec import CheckSpec
from .project_targets import ProjectTargetsConfig

__all__ = ["RunPlanGenerationReport", "_resolve_profile_ids"]


@dataclass
class RunPlanGenerationReport:
    """Result of :func:`generate_run_plan` (mirrors
    :class:`~.build_output.BuildOutputValidationReport`'s shape)."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _resolve_profile_ids(
    check: CheckSpec, config: ProjectTargetsConfig
) -> tuple[list[str], bool]:
    """Returns ``(profile_ids, explicit)``.

    ``explicit`` is ``True`` when *check* named its own ``profiles:``
    selector -- a profile named there that turns out not to build the
    referenced target is a hard error, unlike the implicit "every contract
    profile" sweep, where a non-matching profile is silently skipped
    (that's the whole reason the implicit sweep is safe -- see module
    docstring).
    """
    if check.profiles:
        return list(check.profiles), True
    return [p.id for p in config.profiles.values() if p.contract], False
