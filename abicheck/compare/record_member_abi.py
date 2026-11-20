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

"""One member's scalar ABI reading, shared by the record-layout checks.

A record member's ``TypeField.type_abi_fact`` is the header backend's reading
of the declared type: its encoding class, byte size and alignment (or vector
lane count). The return-convention and packing checks both read it here, so
neither keeps a spelling table of its own. A member the fact does not decide
(no fact, a vector, a record, a pointer-to-unknown) is ``None``: undecided.
"""

from __future__ import annotations

from ..model.abi_fundamental import AbiFundamental
from ..model.entities import TypeField

__all__ = ["member_abi"]


def member_abi(member: TypeField) -> AbiFundamental | None:
    """The member's ABI fact value, or ``None`` when it is not determined."""
    fact = member.type_abi_fact
    if fact is None or not fact.is_present or fact.value is None:
        return None
    return fact.value
