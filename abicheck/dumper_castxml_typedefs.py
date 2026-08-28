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
plus a couple of small, unrelated pure per-element helpers
(``_extract_contract_attributes``/``_deprecation_marker``) that also moved
here purely to keep :mod:`abicheck.dumper_castxml` under the AI-readiness
file-size hard cap (there being no responsibility-package owner for either
yet is ADR-061's own still-open migration, not a design choice made here —
see that ADR for the target shape; adding a *new* flat ``dumper_`` sibling
module is what ``architecture/modules.yaml``'s ``frozen_root_families``
exists to prevent, so this reuses the one already-allowlisted split-out
module in this family rather than adding another).

Pure functions taking the parser's own bound helper methods (or an
already-extracted XML attribute string/``Element``) as arguments, never
``_CastxmlParser`` methods themselves, so this module has no dependency on
the parser class and cannot form an import cycle back into it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import Any
from xml.etree.ElementTree import (
    Element,  # type annotation only; parsing uses defusedxml
)

_CONTRACT_ATTRIBUTE_BASES = frozenset(
    {
        "noreturn",
        "nonnull",
        "returns_nonnull",
        "malloc",
        "format",
        "format_arg",
        "alloc_size",
        "alloc_align",
        "warn_unused_result",
        "sentinel",
        # calling-convention selections — a flip is an ABI change on the
        # affected targets, reported via the contract-attribute kinds.
        "cdecl",
        "stdcall",
        "fastcall",
        "thiscall",
        "regparm",
        "ms_abi",
        "sysv_abi",
        "vectorcall",
    }
)


def _extract_contract_attributes(attributes: str) -> list[str]:
    """Filter a castxml ``attributes`` string down to contract attributes.

    Returns normalized, sorted tokens with any ``gnu:``/``gnu::`` namespace
    prefix stripped and argument lists preserved (``nonnull(1)``). Tokens not
    in the known contract set (``noexcept``, ``final``, …) are ignored.
    """
    tokens: set[str] = set()
    for raw in attributes.split():
        token = raw
        for prefix in ("gnu::", "gnu:", "__"):
            if token.startswith(prefix):
                token = token[len(prefix) :]
        token = token.strip("_")
        base = token.split("(", 1)[0]
        if base in _CONTRACT_ATTRIBUTE_BASES:
            tokens.add(token)
    return sorted(tokens)


def _deprecation_marker(el: Element) -> str | None:
    """Deprecation message for *el*, or ``None`` if not deprecated.

    castxml's ``GetDeclAttributes`` (``Output.cxx``) always adds a bare
    ``"deprecated"`` token to the compound ``attributes`` string when
    ``DeprecatedAttr`` is present, but only emits the dedicated
    ``deprecation="..."`` XML attribute when the attribute carries a
    non-empty message. A BARE ``[[deprecated]]``/
    ``__attribute__((deprecated))`` (no message) therefore has NO
    ``deprecation`` attribute at all — reading only ``el.get("deprecation")``
    missed every messageless deprecation (Codex review, PR #582, confirmed
    against castxml's own source). Falls back to ``""`` (deprecated, no
    message) when the bare token is present in ``attributes`` instead.
    """
    msg = el.get("deprecation")
    if msg is not None:
        return msg
    if re.search(r"\bdeprecated\b", el.get("attributes", "")):
        return ""
    return None


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
