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

"""Whether a castxml class-template specialization is declared by its own
class-head (``RecordType.specialization_declared``).

castxml emits an instantiated specialization (``Box<double>``) with the same
attributes whether the header wrote ``template <> struct Box<double> {...}``
or merely used ``Box<double>`` and let the compiler instantiate the primary
template. The difference is in the location castxml gives it: an instantiation
points at the primary template's class-head (``struct Box {``), an explicit or
partial specialization at its own (``struct Box<double> {``). So the declaring
line answers the question:

* the line spells the class key and the template name followed by ``<``: the
  header declares this specialization itself -> ``PRESENT(True)``;
* the line spells the class key and the template name *not* followed by
  ``<``: the primary template's class-head -> ``PRESENT(False)``;
* the file cannot be read, or a macro expands to the declaration ->
  ``NOT_COLLECTED``, no claim;
* the name is not a template-id -> ``None``: there is no specialization to
  describe.

Only ``PRESENT(False)`` narrows what counts as a removal
(``compare/template_instance_absence.py``), so every doubtful shape reads as
one of the answers that keep the existing removal path.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ....model import Fact
from .context import CastxmlParserContext

__all__ = ["specialization_declared"]

_CLASS_KEY = r"\b(?:struct|class|union)\b"


def _declaring_line(ctx: CastxmlParserContext, el: Any) -> str | None:
    file_id = el.get("file", "")
    line_raw = el.get("line", "")
    if not (file_id and line_raw):
        loc_el = ctx.id_map.get(el.get("location", ""))
        if loc_el is None:
            return None
        file_id, line_raw = loc_el.get("file", ""), loc_el.get("line", "")
    file_el = ctx.id_map.get(file_id)
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
    index = int(line_raw) - 1
    return lines[index] if 0 <= index < len(lines) else None


def specialization_declared(
    ctx: CastxmlParserContext, el: Any, name: str
) -> Fact[bool] | None:
    """See the module docstring. *name* is the record's castxml name
    (``Box<double>``)."""
    if "<" not in name:
        return None
    template = name.split("<", 1)[0].strip()
    if not template.isidentifier():
        return None
    line = _declaring_line(ctx, el)
    if line is None:
        return Fact.not_collected()
    head = re.search(
        rf"{_CLASS_KEY}[^;{{}}]*?(?<![\w:~]){re.escape(template)}\b\s*(<)?", line
    )
    if head is None:
        return Fact.not_collected()
    return Fact.present(head.group(1) is not None, producer="castxml")
