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

"""The C/C++ mode a snapshot's primary header pass resolved.

``extract.headers.toolchain`` records it as ``ast_toolchain
["resolved_lang_mode"]`` (``"c"``/``"c++"``, or ``"heterogeneous"`` for a
multi-TU merge whose TUs disagree). A hybrid merge
(``workflows.dump.hybrid_merge``) keeps no bare key: each leg's keys are
prefixed ``castxml_``/``clang_``. A later parse of the same headers reads the
decision here, so a hybrid snapshot is not mistaken for one with no decision.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["RESOLVED_LANG_MODE_KEY", "recorded_lang_mode"]

#: ``ast_toolchain`` key holding the resolved mode.
RESOLVED_LANG_MODE_KEY = "resolved_lang_mode"

_HYBRID_LEGS = ("castxml_", "clang_")


def recorded_lang_mode(ast_toolchain: Mapping[str, object] | None) -> str | None:
    """The recorded mode: the bare key, else the hybrid legs' shared value.

    ``None`` when nothing was recorded or the legs disagree -- there is then
    no single decision to follow.
    """
    tc = ast_toolchain or {}
    if RESOLVED_LANG_MODE_KEY in tc:
        bare = tc[RESOLVED_LANG_MODE_KEY]
        return bare if isinstance(bare, str) else None
    legs = {
        value
        for prefix in _HYBRID_LEGS
        if isinstance(value := tc.get(f"{prefix}{RESOLVED_LANG_MODE_KEY}"), str)
    }
    return legs.pop() if len(legs) == 1 else None
