# Copyright 2026 Nikolay Petrov
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

"""castxml XML → ABI model parser, split by parsed entity (ADR-061 D9).

``abicheck.dumper_castxml`` remains the coordinating module (the ADR's
``backend.py`` role: it opens the castxml document, builds the shared id
map, and drives per-entity parsing) until its own migration lands. Entity
modules that have already moved out of it live here, one class of node per
module, importing shared state rather than re-deriving it.
"""

from __future__ import annotations
