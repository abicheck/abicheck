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

"""Detector contracts used by checker orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ChangeLike(Protocol):
    kind: object
    symbol: str
    description: str


@dataclass(frozen=True)
class DetectorResult:
    name: str
    changes_count: int
    enabled: bool = True
    coverage_gap: str | None = None
    #: ADR-067 D3: True when this detector did **not run** — its
    #: ``requires_support`` gate refused it — as opposed to running and
    #: finding nothing. ``changes_count`` is ``0`` in both cases, which is
    #: exactly the ambiguity this field removes: "0 changes" and "not
    #: evaluated" are different statements about assurance, and a report may
    #: not present the second as the first. ``coverage_gap`` carries the gate's
    #: own reason (why it did not run); this flag carries the *state*, so a
    #: consumer never has to infer one from the presence of the other.
    not_evaluated: bool = False
