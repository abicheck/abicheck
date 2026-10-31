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

"""Up-front checks of ``CompareRequest``'s ADR-067 acknowledgment fields.

Moved out of ``service_requests.py`` (AI-readiness file-size cap); the checks
and their messages are unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

__all__ = ["acknowledgment_errors"]


class _AcknowledgmentFields(Protocol):
    """The ``CompareRequest`` fields the checks read (structural, no import)."""

    @property
    def acknowledgment_unacknowledged_additions(self) -> str | None: ...

    @property
    def acknowledgment_breaks(self) -> str | None: ...

    @property
    def acknowledgments(self) -> Path | None: ...


def acknowledgment_errors(request: _AcknowledgmentFields) -> list[str]:
    """``CompareRequest``'s ADR-067 D5/D6 fields, checked up front."""
    from ..model.acknowledgment_policy import (
        VALID_ACKNOWLEDGMENT_BREAKS_ACTIONS,
        VALID_UNACKNOWLEDGED_ADDITIONS_ACTIONS,
    )

    errors: list[str] = []
    action = request.acknowledgment_unacknowledged_additions
    if action is not None and action not in VALID_UNACKNOWLEDGED_ADDITIONS_ACTIONS:
        errors.append(
            "acknowledgment_unacknowledged_additions must be one of "
            f"{sorted(VALID_UNACKNOWLEDGED_ADDITIONS_ACTIONS)}; got {action!r}"
        )
    breaks = request.acknowledgment_breaks
    if breaks is not None and breaks not in VALID_ACKNOWLEDGMENT_BREAKS_ACTIONS:
        errors.append(
            "acknowledgment_breaks must be one of "
            f"{sorted(VALID_ACKNOWLEDGMENT_BREAKS_ACTIONS)}; got {breaks!r}"
        )
    path = request.acknowledgments
    if path is not None and not Path(path).is_file():
        errors.append(f"acknowledgments file not found: {path}")
    return errors
