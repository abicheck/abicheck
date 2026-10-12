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

"""What happens to a finding after diff (ADR-067 "record before disposing").

The finding-disposition owner: raw detector findings plus the resolved rules
go in; the kept findings, every disposed-of bucket, and a disposition ledger
that accounts for each input finding come out.
"""

from .suppression import (
    SuppressionDisposition,
    dispose_by_suppression,
    overreach_diagnostic,
)

__all__ = [
    "SuppressionDisposition",
    "dispose_by_suppression",
    "overreach_diagnostic",
]
