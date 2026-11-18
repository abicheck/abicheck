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
"""The castxml header backend's explicit-specialization evidence
(``Function.explicit_instantiation_fact``).

castxml's XML carries no explicit-instantiation marker, but it does give an
explicit specialization its own location: the line of its declarator, with the
``template <>`` that introduces it in front of it. The fact is read from that
source text. An explicit instantiation (``extern template``, ``template`` with
no ``<``) and an implicit instance share the primary template's line in the
XML, so they cannot be told apart here; the fact stays unset for them (no
evidence), which every rule reads as "not proven explicit", never as absent.
Joining the text's instantiation lines to the XML is a separate change.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree.ElementTree import Element

from ....model import ExplicitInstantiation, Fact
from .calling_convention import _code_window
from .context import CastxmlParserContext

#: ``template <>`` (a specialization's introducer; the closing ``>`` may sit
#: on the same line only).
_SPECIALIZATION_RE = re.compile(r"\btemplate\s*<\s*>")


def castxml_explicit_instantiation_fact(
    ctx: CastxmlParserContext, el: Element
) -> Fact[ExplicitInstantiation] | None:
    """The explicit-specialization fact for a castxml function element *el*,
    or ``None`` (no evidence) when the source does not spell one."""
    name = el.get("name", "")
    decl = _raw_declaration_lines(ctx, el)
    if decl is None or not name:
        return None
    lines, line_no = decl
    code, start, end = _code_window(lines, line_no)
    if not _declares_specialization(code, start, end, name):
        return None
    return Fact.present(ExplicitInstantiation.SPECIALIZATION, producer="castxml")


def _raw_declaration_lines(
    ctx: CastxmlParserContext, el: Element
) -> tuple[list[str], int] | None:
    """The original header's lines and the element's 1-based line.

    Read raw, not through the preprocessed table: that table keeps lines only
    for declarations that spell a calling convention, so it cannot answer for
    a specialization that spells none.
    """
    file_el = ctx.id_map.get(el.get("file", ""))
    line_raw = el.get("line", "")
    fname = file_el.get("name", "") if file_el is not None else ""
    if not fname or not line_raw.isdigit():
        return None
    lines = ctx.source_lines_cache.get(fname)
    if lines is None:
        try:
            lines = Path(fname).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return None
        ctx.source_lines_cache[fname] = lines
    line_no = int(line_raw)
    return (lines, line_no) if 1 <= line_no <= len(lines) else None


def _declares_specialization(code: str, start: int, end: int, name: str) -> bool:
    """Whether a ``template <>`` introduces the declarator *name* on the
    reported line: the declarator is the first whole-word *name* on that line,
    and nothing between the introducer and it ends a declaration (``;`` or
    ``}``), so an earlier specialization's closing brace never reaches it."""
    prefix = code[:end]
    declarator = re.search(rf"(?<![\w]){re.escape(name)}(?!\w)", prefix[start:])
    if declarator is None:
        return False
    at = start + declarator.start()
    for introducer in _SPECIALIZATION_RE.finditer(prefix[:at]):
        between = prefix[introducer.end() : at]
        if ";" not in between and "}" not in between:
            return True
    return False
