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

"""``_CastxmlParser.parse_typedefs``/``parse_typedefs_qualified`` bodies.

``_deprecation_marker`` and ``_extract_contract_attributes`` both moved on
to :mod:`abicheck.extract.headers.castxml.location` (ADR-061 D9, Codex
review on PR #939 and PR #940 respectively) once an ``extract``-owned
entity module needed each — this module, still flat and unmigrated itself,
is exactly the "legacy sibling" ``abicheck/extract/AGENTS.md`` says a new
``extract`` module must not reach into the private helpers of. Both are
re-exported here under their old private names so every existing caller
(including ``dumper_castxml.py``'s own re-export of them, and the direct
``from abicheck.dumper_castxml_typedefs import ...`` test imports) is
unaffected.

Pure functions taking the parser's own bound helper methods (or an
already-extracted XML attribute string/``Element``) as arguments, never
``_CastxmlParser`` methods themselves, so this module has no dependency on
the parser class and cannot form an import cycle back into it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from xml.etree.ElementTree import (
    Element,  # type annotation only; parsing uses defusedxml
)

# Moved to abicheck.extract.headers.castxml.location (ADR-061 D9's "extract
# owns castxml/clang header-AST entity parsing", Codex review on PR #939):
# this module is a still-flat, unmigrated `dumper_castxml.py` sibling, and
# extract/headers/castxml/enums.py needed this exact primitive — reaching
# back into a private helper here would have been the "don't reach into a
# flat legacy module's private helpers" violation abicheck/extract/AGENTS.md
# warns against. Re-exported under its old private name so every existing
# caller here (and `dumper_castxml.py`'s own `as`-aliased re-export of it)
# is unaffected.
from .extract.headers.castxml.location import (
    _CONTRACT_ATTRIBUTE_BASES as _CONTRACT_ATTRIBUTE_BASES,
    contract_attributes,
    deprecation_marker,
)


def _extract_contract_attributes(attributes: str) -> list[str]:
    """Back-compat private alias — see
    :func:`abicheck.extract.headers.castxml.location.contract_attributes`,
    this primitive's real home since ADR-061 D9."""
    return contract_attributes(attributes)


def _deprecation_marker(el: Element) -> str | None:
    """Back-compat private alias — see
    :func:`abicheck.extract.headers.castxml.location.deprecation_marker`,
    this primitive's real home since ADR-061 D9."""
    return deprecation_marker(el)


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
