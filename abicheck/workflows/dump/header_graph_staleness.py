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

"""When a header-graph prefetch no longer answers the attach.

A leaf: it reads only ``eff_tokens`` and ``resolved_force_cpp`` off the
acquired result, so the attach can ask it without importing the prefetch
module (which imports the attach).
"""

from __future__ import annotations

from typing import Any

from ...extract.headers.clang.abi_target import lacks_binary_abi_target

__all__ = ["prefetch_needs_reacquire"]


def prefetch_needs_reacquire(
    acquired: Any,
    lang: str | None,
    primary_mode: str | None,
    compile: Any,
    exported: frozenset[str],
) -> bool:
    """Whether the attach must parse again instead of using *acquired*.

    A prefetch had the header text only. When the primary pass resolved C++
    from the binary's mangled exports, a prefetch that ran as C is stale; when
    those exports imply a binary-ABI ``--target``, a prefetch parsed without it
    is for the wrong ABI. No prefetch at all (``None``) always acquires.
    """
    if acquired is None:
        return True
    is_c = (lang or "").lower() == "c"
    if lacks_binary_abi_target(acquired.eff_tokens, compile, is_c, exported):
        return True
    return (
        not lang and primary_mode == "c++" and acquired.resolved_force_cpp is not True
    )
