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

"""Suppression-list type a frontend needs to load and type its own input.

ADR-061 Phase 4 item 2's "make workflows the sole operation owners" rule
applies to the ``policy`` ring the same way ``extraction.py``/``storage.py``
apply it to ``extract``/``storage``: once ``suppression.py`` is classified
``policy`` (``architecture/modules.yaml``), ``frontends.may_import`` does not
list ``policy``, so a CLI module that needs ``SuppressionList`` to load a
``--suppress`` file or type a loaded one reaches it through this facade
instead of importing ``suppression.py`` directly. ``service.py`` (itself
``workflows``-classified) already gets this right in
``load_suppression_and_policy`` -- this module gives the flat CLI helpers
(``cli_params.py``, ``cli_scan_baseline.py``) the same route.

Re-export only, deliberately: the point is that there is one owner per
operation and the frontend reaches it through the workflow layer, not that a
new implementation appears here. ``suppression.py`` remains the one module to
read and to change.

``from ..x import y`` **binds** ``y`` here at import time, the same
consequence ``extraction.py``'s own docstring records -- a test that needs to
substitute this must patch it *here*, where the call actually resolves.
"""

from __future__ import annotations

from ..suppression import Suppression, SuppressionList

__all__ = [
    "Suppression",
    "SuppressionList",
]
