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

"""Back-compat facade: ``EntityResolver`` lives in
``abicheck.model.entity_resolver`` now, for the same reason
``graph_facts.py`` moved (see that module's own docstring) —
``abicheck.model.source_graph``'s ``SourceGraphSummary.entity_resolver``
field needs it, and importing it through ``abicheck.buildsource`` would
re-enter this package's eager ``__init__.py`` cascade.
"""

from __future__ import annotations

from ..model.entity_resolver import (
    EntityConflict as EntityConflict,
    EntityResolver as EntityResolver,
)
