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
its signature or its presence alone: strong versus vague (weak/COMDAT), where
the same observable event — an export disappearing — means materially
different things to a consumer.
"""

from __future__ import annotations

from .change_registry_types import ChangeKindMeta, Verdict

_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta

LINKAGE_EXTENSION_ENTRIES: list[ChangeKindMeta] = [
    _E(
        "func_export_dropped_inline_available",
        _R,
        impact="A weak (vague-linkage/COMDAT) symbol -- an inline function, a "
        "template instantiation, or an implicit special member -- is no "
        "longer exported, but the new headers still define it inline. "
        "The language requires every translation unit that uses such an "
        "entity to define it for itself, so a consumer carries its own "
        "COMDAT copy and keeps resolving: this is not the same event as "
        "a strong definition disappearing. Reported as a risk rather "
        "than silence because the argument rests on the consumer having "
        "been built against a header that defines it -- a consumer that "
        "only ever saw a declaration, or that compares the entity's "
        "address across the library boundary expecting a single shared "
        "instance, can still be affected.",
        description_template="Weak export dropped, still inline in headers: {old}",
    ),
]
