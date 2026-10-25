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

"""Carry :mod:`..preprocessor_branches`' result inside the CastXML document.

Stored in the document (one ``<AbicheckHiddenDecl>`` per guard/name pair
under an ``<AbicheckHiddenScan>`` marker) for the same reason
:mod:`.preprocessed_text` stores its lines there: a cached AST must carry it
too. A document without the marker had no scan (a failed preprocess run or
a cache entry predating it) and reads as ``None`` -- never "nothing hidden".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from xml.etree.ElementTree import Element, SubElement

SCAN_TAG = "AbicheckHiddenScan"
DECL_TAG = "AbicheckHiddenDecl"


def attach_hidden_declarations(
    root: Element, hidden: Mapping[str, tuple[str, ...]]
) -> None:
    SubElement(root, SCAN_TAG)
    for macro, names in sorted(hidden.items()):
        for name in names:
            SubElement(root, DECL_TAG, macro=macro, name=name)


def read_hidden_declarations(root: Element) -> dict[str, tuple[str, ...]] | None:
    if root.find(SCAN_TAG) is None:
        return None
    out: dict[str, set[str]] = {}
    for el in root.findall(DECL_TAG):
        macro, name = el.get("macro", ""), el.get("name", "")
        if macro and name:
            out.setdefault(macro, set()).add(name)
    return {m: tuple(sorted(n)) for m, n in sorted(out.items())}


def parser_preprocessor_hidden(parser: Any) -> dict[str, tuple[str, ...]] | None:
    """The scan a castxml parser's document carried, or ``None`` (no scan:
    any non-castxml backend, or a castxml run whose preprocess step failed)."""
    value = getattr(getattr(parser, "_ctx", None), "preprocessor_hidden", None)
    return dict(value) if isinstance(value, dict) else None


__all__ = [
    "attach_hidden_declarations",
    "parser_preprocessor_hidden",
    "read_hidden_declarations",
]
