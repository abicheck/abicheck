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

"""``(gap, notes)`` views of the registered unobserved-dimension providers,
for tests that assert one provider's notes directly. Production reads the
providers through ``policy.analysis_assurance._unobserved``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from abicheck.policy.analysis_assurance._concepts import concept_constraint_findings
from abicheck.policy.analysis_assurance._template_defaults import (
    template_default_findings,
)
from abicheck.policy.analysis_assurance._unobserved_finding import gap_and_notes

if TYPE_CHECKING:
    from abicheck.model import AbiSnapshot


def concept_constraint_gap(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> tuple[bool, list[str]]:
    return gap_and_notes(concept_constraint_findings(old, new))


def template_default_gap(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> tuple[bool, list[str]]:
    return gap_and_notes(template_default_findings(old, new))
