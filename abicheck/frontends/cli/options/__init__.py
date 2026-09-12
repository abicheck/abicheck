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

from .export import (
    EXPORT_METAVAR,
    STDOUT_DESTINATION,
    ExportSet,
    ExportTarget,
    build_export_set,
    export_options,
    parse_export_operand,
    reject_dry_run_with_exports,
)

__all__ = [
    "EXPORT_METAVAR",
    "STDOUT_DESTINATION",
    "ExportSet",
    "ExportTarget",
    "build_export_set",
    "export_options",
    "parse_export_operand",
    "reject_dry_run_with_exports",
]
