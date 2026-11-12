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

"""The closed vocabularies of ``AnalysisAssurance``'s sub-status fields.

Each was a bare ``str``: a new spelling type-checked anywhere, and the
overall-status ladder (``analysis_assurance._status_ladder``) silently read
any spelling it did not list as ``complete``. A leaf module so the producers
outside the ``analysis_assurance`` package can import it without a cycle.
"""

from __future__ import annotations

from typing import Literal

#: Presence of one evidence layer on the two sides (L0, DWARF, L3).
ContextStatus = Literal["clean", "asymmetric", "not_evaluated"]
#: Header evidence adds the drift finding to :data:`ContextStatus`.
HeaderContextStatus = Literal["clean", "drift_detected", "asymmetric", "not_evaluated"]
FactSetComparability = Literal[
    "comparable", "inconsistent", "unknown", "not_applicable"
]
GraphCompleteness = Literal[
    "complete", "degraded", "narrowed", "unknown", "not_collected"
]
#: ``not_evaluated`` when there is no old snapshot to compare against.
SchemaStalenessStatus = Literal["clean", "degraded", "not_evaluated"]
