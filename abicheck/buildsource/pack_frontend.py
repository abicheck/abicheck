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

"""``BuildSourcePack`` persistence, re-exposed for callers that may not
import ``storage`` directly (ADR-061 Phase 5's BuildSourcePack split).

``pack_io.py`` is classified ``storage``; ``frontends`` (every ``cli_*.py``)
is only allowed to import ``model``/``workflows``/``report``, per
``architecture/modules.yaml``'s ``may_import``. This module is registered as
a pure facade (``architecture/modules.yaml``'s ``facades`` list) rather than
classified into any layer -- an unclassified module's imports are never
checked against the ``migrated_source`` layer-direction rules, the same
treatment ``source_graph.py`` already has -- so a CLI module reading a pack
for display -- ``cli_graph.py``'s ``graph explain`` pack loader,
``cli_datasources.py``'s ``--show-data-sources`` diagnostics,
``cli_dump_dry_run_build_query.py``'s dry-run reachability check,
``cli_buildsource_helpers.py``'s post-collect summary,
``cli_buildsource_merge.py``'s merged-pack ref stamping -- has a legal path to
``pack_io.py``'s ``load``/``content_hash``/``to_ref`` without ``frontends``
importing ``storage`` on its own account. A thin re-export, not a
reimplementation: every function here is the identical ``pack_io`` one, so
there is exactly one place the actual I/O logic lives.
"""

from __future__ import annotations

from .pack_io import content_hash as content_hash, load as load, to_ref as to_ref

__all__ = ["content_hash", "load", "to_ref"]
