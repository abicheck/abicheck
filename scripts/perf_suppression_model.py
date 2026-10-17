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

"""Suppression rule model for the perf harness, across both installed layouts.

The PR-vs-base regression gate runs HEAD's ``benchmark_scaling.py`` against the
installed base package, which may predate the ``policy/suppression/`` move and
still name the model ``abicheck.suppression``. Prefer the current path and fall
back only when the installed copy is the older layout.
"""

from __future__ import annotations

try:
    from abicheck.policy.suppression.rules import Suppression, SuppressionList
except ImportError:
    from abicheck.suppression import Suppression, SuppressionList

__all__ = ["Suppression", "SuppressionList"]
