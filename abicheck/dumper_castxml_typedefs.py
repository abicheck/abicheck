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

"""``_CastxmlParser.parse_typedefs``/``parse_typedefs_qualified`` bodies,
split out of :mod:`abicheck.dumper_castxml` to keep that module under the
AI-readiness file-size hard cap (G31 Phase C, ``AbiSnapshot.typedefs_qualified``
schema v25 -- see that field's own docstring in ``model.py`` for why a
qualified-name-keyed twin of ``typedefs`` exists at all).

Pure functions taking the parser's own bound helper methods as callables,
rather than parser methods themselves, so this module has no dependency on
``_CastxmlParser`` and cannot form an import cycle back into it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from xml.etree.ElementTree import (
    Element,  # type annotation only; parsing uses defusedxml
)


def iter_typedef_entries(
    typedef_els: list[Element],
    is_builtin_element: Callable[[Element], bool],
    underlying_type_name: Callable[[str], str],
) -> Iterator[tuple[Element, str]]:
    """``(el, underlying)`` for every named, non-builtin typedef element."""
    for el in typedef_els:
        name = el.get("name", "")
        if not name or is_builtin_element(el):
            continue
        type_id = el.get("type", "")
        # Flatten typedef chains: alias → alias2 → int  stored as  alias → int
        yield el, underlying_type_name(type_id) if type_id else "?"


def parse_typedefs(
    typedef_els: list[Element],
    is_builtin_element: Callable[[Element], bool],
    underlying_type_name: Callable[[str], str],
) -> dict[str, str]:
    """Bare-name-keyed alias -> underlying-type mapping."""
    return {
        el.get("name", ""): underlying
        for el, underlying in iter_typedef_entries(
            typedef_els, is_builtin_element, underlying_type_name
        )
    }


def parse_typedefs_qualified(
    typedef_els: list[Element],
    is_builtin_element: Callable[[Element], bool],
    underlying_type_name: Callable[[str], str],
    qualified_name: Callable[[Any], str],
) -> dict[str, str]:
    """Same mapping as :func:`parse_typedefs`, keyed by qualified name."""
    return {
        qualified_name(el): underlying
        for el, underlying in iter_typedef_entries(
            typedef_els, is_builtin_element, underlying_type_name
        )
    }
