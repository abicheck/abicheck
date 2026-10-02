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

"""Read side of the public-header identifier index (ADR-063 2026-10-01
amendment): the index only when its ``Fact`` is present. A leaf, so the
surface closure can import it without joining the contract modules' cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model.availability import FactStatus

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot


def present_header_identifiers(snap: AbiSnapshot) -> frozenset[str] | None:
    """The raw-header-text identifier index, only when its Fact is present --
    a not-collected, failed or unsupported scan is unknown, never empty."""
    fact = snap.public_header_identifiers_fact
    if fact is None or fact.status is not FactStatus.PRESENT:
        return None
    return frozenset(fact.value or ())


__all__ = ["present_header_identifiers"]
