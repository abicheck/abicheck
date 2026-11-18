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

"""Decode of ``Function.return_by_value_fact`` (schema v70, Fact[bool]).

Absent reads as unset at every version: a producer writes the key only where
it determined the answer, so an absent key never reads as "not by value".
"""

from __future__ import annotations

from typing import Any

from ..model.fact import Fact
from .fact_decode import decode_fact
from .fact_schema_versions import _MIN_SCHEMA_VERSION_FOR_RETURN_BY_VALUE_FACT

__all__ = ["decode_return_by_value"]


def decode_return_by_value(f: dict[str, Any], schema_version: int) -> Fact[bool] | None:
    """The function dict *f*'s ``return_by_value_fact``, or ``None`` (unset)."""
    raw = f.get("return_by_value_fact")
    if raw is None:
        return None
    return decode_fact(
        raw,
        schema_version,
        min_schema_version=_MIN_SCHEMA_VERSION_FOR_RETURN_BY_VALUE_FACT,
    )
