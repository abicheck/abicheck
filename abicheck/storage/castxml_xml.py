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

"""Parse a castxml XML document with its repeated attribute values shared.

castxml output is dominated by reference attributes whose values repeat
across hundreds of thousands of elements (``file="f1"``, ``context="_1"``,
type ids, ``extern="1"``): a 26 MB ``libmkl_rt`` header dump carries 1.5M
attribute values but only 193k distinct ones. ElementTree allocates a fresh
string for each, so the parsed tree was ~170 MiB per side and the single
largest allocation site of a large compare. Sharing equal values cuts it by
about a third; the pool lives only for the parse.

Parsing still goes through defusedxml's parser, so its entity/DTD
protections are unchanged; only the tree builder differs.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast
from xml.etree.ElementTree import Element, TreeBuilder

from defusedxml.ElementTree import DefusedXMLParser, parse as _defused_parse

__all__ = ["parse_castxml_xml"]


class _SharingTreeBuilder(TreeBuilder):
    """A :class:`TreeBuilder` that stores one object per distinct attribute value."""

    def __init__(self) -> None:
        super().__init__()
        self._pool: dict[str, str] = {}

    def start(
        self, tag: str | Callable[..., Element], attrs: dict[str, str]
    ) -> Element:
        pool = self._pool
        return super().start(tag, {k: pool.setdefault(v, v) for k, v in attrs.items()})


def parse_castxml_xml(path: Path | str) -> Element:
    """The root element of the castxml document at *path*.

    Raises whatever the defused parser raises for malformed or unsafe input,
    exactly like ``defusedxml.ElementTree.parse(path).getroot()``.
    """
    parser = DefusedXMLParser(target=_SharingTreeBuilder())
    return cast(Element, _defused_parse(str(path), parser=parser).getroot())
