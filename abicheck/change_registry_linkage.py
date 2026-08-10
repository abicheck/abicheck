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

"""Symbol-linkage ChangeKind registry entries.

Split out of ``change_registry.py`` to keep that module under the
AI-readiness 2000-line hard cap, following the same pattern as
``change_registry_coverage.py``/``change_registry_suppression.py``. These
entries are spliced into the single ``REGISTRY`` at import time — declaring a
kind here is exactly equivalent to declaring it in ``change_registry.py``.

Covers the kinds whose severity turns on a symbol's *linkage* rather than on
its signature or its presence alone: the same observable event — an export
disappearing — means materially different things for a strong definition and
for a vague-linkage (COMDAT) one.
"""

from __future__ import annotations

from .change_registry_types import ChangeKindMeta, Verdict

_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta

LINKAGE_EXTENSION_ENTRIES: list[ChangeKindMeta] = [
    _E(
        "func_vague_export_dropped",
        _R,
        impact=(
            "An exported symbol the build proved to be vague-linkage (emitted "
            "into a COMDAT group, as the language requires for an inline "
            "function, a template instantiation, an implicit special member or "
            "a vtable with no key function) is no longer exported, while the "
            "new headers still declare the entity. Every translation unit that "
            "used it had to define it for itself, so an already-linked consumer "
            "carries its own copy and keeps resolving: not the same event as a "
            "strong definition disappearing. Reported as a risk rather than "
            "silence because the proof covers already-linked consumers only — "
            "code compiled against the new headers still needs a definition "
            "there, which no header backend can confirm, and an entity whose "
            "address is compared across the library boundary can be affected "
            "either way. Requires L3 build evidence (--sources/--build-info); "
            "without it the removal is reported as a plain break."
        ),
        description_template="Vague-linkage (COMDAT) export dropped: {old}",
    ),
]
