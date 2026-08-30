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

"""Pair-wide C++20 dialect override, shared by every compare front-end.

Split out of :mod:`abicheck.service_scan` purely for that module's own
``no_growth`` line budget: unlike the rest of that file,
:func:`pair_wide_cxx20_std_override` has zero dependency on anything else
there (or on anything ``service_scan``-specific at all), so it moves
cleanly with no import-direction consequence -- :mod:`abicheck.service_scan`
imports it back for re-export, a genuine one-directional edge, not the
mutual-dependency shape a real split of ``service_scan``'s own request/
estimate machinery would create.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def pair_wide_cxx20_std_override(
    lang: str,
    old_headers: Iterable[Path],
    new_headers: Iterable[Path],
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
) -> tuple[str, ...] | None:
    """Pair-wide C++20 dialect decision, shared by every compare front-end
    (P0 fix — CLI ``compare`` via ``cli_helpers_compare._pair_wide_dialect_override``,
    the Python-API/MCP ``run_compare_request`` path).

    ``dumper.py``'s C++20 ``requires``/``concept`` heuristic only ever sees ONE
    side's headers at a time (each side is dumped independently), so an
    old/new pair could silently disagree on the language standard whenever
    neither side pins an explicit one — e.g. only the *new* side picks up a
    ``concept`` and gets auto-upgraded to C++20 while *old* stays on the
    toolchain default. That lets a real dialect-floor change masquerade as an
    ordinary ABI diff (or, the inverse historical bug: a header containing
    ``#error Foo requires Base`` tripped the heuristic on whichever side had
    that text).

    Decides the heuristic once, over the union of both sides' headers, so a
    caller can pin the identical result onto both sides' compile context. An
    explicit ``-std=``/``--std=``/``/std:`` from the user always wins
    (``has_explicit_std`` short-circuits first); returns ``None`` in that case
    and whenever no override is needed — never overriding an explicit choice.
    Lives here (not in a CLI-layer module) so every front-end — CLI, Python
    API, MCP — can share one implementation without a CLI-layer dependency
    reaching into the Tier-2 service layer.
    """
    from ._compiler_options import has_explicit_std
    from .dumper_ast_config_cpp20 import _detect_cpp20_headers

    old_h = list(old_headers)
    new_h = list(new_headers)
    if lang.lower() != "c++" or not (old_h or new_h):
        return None
    if has_explicit_std(gcc_options, gcc_option_tokens):
        return None
    if not _detect_cpp20_headers(old_h + new_h):
        return None
    return ("-std=gnu++20",)


__all__ = ["pair_wide_cxx20_std_override"]
