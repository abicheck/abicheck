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

"""The ``OptionRuling`` shape shared by every per-command ruling table.

A leaf so ``rulings.py`` (compare/dump) and ``rulings_integration.py``
(aggregate/project/deps) can both build tables without importing each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: ``per_run_operand`` — clears all three guards; stays on the CLI.
#: ``deferred`` — ruled demotable/removable, blocked by a named prerequisite.
Disposition = Literal["per_run_operand", "deferred"]


@dataclass(frozen=True)
class OptionRuling:
    """One option's ADR-068 D5 ruling.

    *rationale* states which guard lets the option stay (or, for a
    ``deferred`` ruling, what it would become). *blocker* names the
    unlanded prerequisite — required for ``deferred``, rejected otherwise,
    so a keep can never be written as if it were pending someone else's
    work and a deferral can never lose its owner.
    """

    disposition: Disposition
    rationale: str
    blocker: str | None = None

    def __post_init__(self) -> None:
        if self.disposition == "deferred" and not self.blocker:
            raise ValueError("a deferred ruling must name the prerequisite blocking it")
        if self.disposition != "deferred" and self.blocker:
            raise ValueError(
                f"only a deferred ruling may name a blocker (got {self.blocker!r})"
            )


def _keep(rationale: str) -> OptionRuling:
    return OptionRuling("per_run_operand", rationale)


def _deferred(rationale: str, *, blocker: str) -> OptionRuling:
    return OptionRuling("deferred", rationale, blocker)
