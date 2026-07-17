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

"""Suppression-diagnostic ChangeKind registry entries (ADR-044).

Split out of ``change_registry.py`` to keep that module under the
AI-readiness 2000-line hard cap, following the same pattern as
``change_registry_coverage.py``/``change_registry_composition.py``. These
entries are spliced into the single ``REGISTRY`` at import time — declaring a
kind here is exactly equivalent to declaring it in ``change_registry.py``.
"""
from __future__ import annotations

from .change_registry_types import ChangeKindMeta, Verdict

_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta

SUPPRESSION_EXTENSION_ENTRIES: list[ChangeKindMeta] = [
    _E("suppression_would_hide_public_break", _R,
       impact="A namespace/source_location suppression rule matched this change, "
              "but it was not applied because the change is reachable from the "
              "public ABI surface (ADR-044) — suppressing it would hide a real "
              "break rather than internal noise. Review the finding; if the "
              "suppression is intentional even though the symbol is "
              "public-reachable, add `allow_public_break: true` to that rule."),
]
