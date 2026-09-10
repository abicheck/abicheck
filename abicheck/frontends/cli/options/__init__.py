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

"""Reusable Click-only option declarations (ADR-061 Phase 4 item 1)."""

from .secondary_output import (
    reject_incoherent_secondary_output,
    reject_incoherent_secondary_writes,
    secondary_output_options,
)

__all__ = [
    "reject_incoherent_secondary_output",
    "reject_incoherent_secondary_writes",
    "secondary_output_options",
]
