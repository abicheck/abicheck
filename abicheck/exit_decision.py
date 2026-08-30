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

"""Back-compat shim: this module's real implementation moved to
:mod:`abicheck.policy.exit_decision` (ADR-061 physical migration of the
``policy`` responsibility package's `legacy_paths` entries).

Every name this module used to define is re-exported here by value -- see
``abicheck/severity.py``'s own shim docstring for why a plain static
import, not a lazy ``__getattr__``, is the right shape here. New code
should import from ``abicheck.policy.exit_decision`` directly.
"""

from __future__ import annotations

from .policy.exit_decision import (
    ExitDecision,
    ExitReason,
    resolve_compare_exit_decision,
    resolve_exit_decision,
    resolve_release_exit_decision,
    resolve_scan_exit_decision,
)

__all__ = [
    "ExitDecision",
    "ExitReason",
    "resolve_compare_exit_decision",
    "resolve_exit_decision",
    "resolve_release_exit_decision",
    "resolve_scan_exit_decision",
]
