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
"""The audited per-generation spelling table (ADR-062 D2) -- re-exports.

The record type and placeholder rule live in :mod:`.generation_spelling_types`,
the rows in :mod:`.generation_spelling_rows`. This module keeps the import path
the generation migrations and tests use.
"""

from __future__ import annotations

from .generation_spelling_rows import SPELLING_MIGRATIONS
from .generation_spelling_types import (
    PLACEHOLDER_REASONS,
    GenerationSpellingChange,
    is_placeholder_reason,
)

__all__ = [
    "PLACEHOLDER_REASONS",
    "SPELLING_MIGRATIONS",
    "GenerationSpellingChange",
    "is_placeholder_reason",
]
