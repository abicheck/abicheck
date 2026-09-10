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

"""Compatibility facade for the historical ``abicheck.bundle_facts_store``
import path (ADR-062 A1.4/A1.5).

ADR-061 gap E: the real implementation moved to ``storage.bundle_facts_package``
and is now classified ``storage`` rather than ``workflows`` -- see that
module's own docstring for why the flat placement here was historically
necessary and why it no longer is. This module re-exports the same two
public names unchanged, so every existing ``from abicheck.bundle_facts_store
import ...`` call site -- including the test suite -- is unaffected."""

from __future__ import annotations

from .storage.bundle_facts_package import (
    read_bundle_facts_package,
    write_bundle_facts_package,
)

__all__ = [
    "read_bundle_facts_package",
    "write_bundle_facts_package",
]
