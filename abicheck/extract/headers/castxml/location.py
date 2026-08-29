# Copyright 2026 Nikolay Petrov
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

"""Source-location and built-in-origin resolution for the castxml backend.

Every function here takes a :class:`~.context.CastxmlParserContext`
explicitly rather than reading it off ``self`` — the D9 "entity modules ...
using shared context" shape, applied to the location-resolution
responsibility rather than a specific entity kind, since ``is_builtin_element``/
``source_location`` are read by more than one entity's parsing (functions,
variables, records, enums today; only enums have moved out of
``dumper_castxml.py`` so far).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

from .context import CastxmlParserContext


def is_builtin_element(ctx: CastxmlParserContext, el: Element) -> bool:
    """Return True if element originates from a compiler built-in pseudo-file.

    Real castxml output: elements carry a ``file`` attribute (e.g. ``file="f0"``)
    pointing directly to a ``File`` element in the id-map — NOT via a separate
    ``Location`` element.  The compound ``location`` attribute (``"f0:0"``) is
    informational only and is NOT a map key.

    Known built-in file names emitted by castxml:
    - ``<builtin>``       (clang/castxml built-in declarations)
    - ``<built-in>``      (older castxml / GCC)
    - ``<command-line>``  (preprocessor command-line defines)
    """
    file_id = el.get("file", "")
    if not file_id:
        return False
    file_el = ctx.id_map.get(file_id)
    if file_el is None:
        return False
    fname = file_el.get("name", "")
    return fname in ("<builtin>", "<built-in>", "<command-line>")


def source_location(ctx: CastxmlParserContext, el: Any) -> str | None:
    """Resolve a declaration's ``file:line`` source location.

    Mirrors the function-parsing path: castxml emits the location either
    directly as ``file``/``line`` attributes or as a ``location`` id
    referencing a ``Location`` element. Returns ``None`` when neither is
    present. Used to populate provenance (``source_header``/``origin``)
    on records, variables, and enums (ADR-015 v6).
    """
    file_id = el.get("file", "")
    line = el.get("line", "")
    if not (file_id and line):
        loc_id = el.get("location", "")
        loc_el = ctx.id_map.get(loc_id) if loc_id else None
        if loc_el is not None:
            file_id = loc_el.get("file", "")
            line = loc_el.get("line", "")
    file_el = ctx.id_map.get(file_id) if file_id else None
    fname = file_el.get("name", "") if file_el is not None else ""
    return f"{fname}:{line}" if fname and line else None


def optional_int_attr(el: Any, attr: str) -> int | None:
    raw = el.get(attr)
    return int(raw) if raw and raw.isdigit() else None


def source_line_has_explicit(
    ctx: CastxmlParserContext,
    loc_el: Element | None,
    declaration_el: Element | None = None,
) -> bool | None:
    """Fallback for castxml Converter nodes that omit explicit="1"."""
    if loc_el is not None:
        file_id = loc_el.get("file", "")
        line_raw = loc_el.get("line", "")
    elif declaration_el is not None:
        file_id = declaration_el.get("file", "")
        line_raw = declaration_el.get("line", "")
    else:
        return None
    file_el = ctx.id_map.get(file_id)
    if file_el is None:
        return None
    fname = file_el.get("name", "")
    if not fname or not line_raw:
        return None
    try:
        line_no = int(line_raw)
        lines = ctx.source_lines_cache.get(fname)
        if lines is None:
            lines = Path(fname).read_text(encoding="utf-8").splitlines()
            ctx.source_lines_cache[fname] = lines
    except (OSError, UnicodeDecodeError, ValueError, IndexError):
        return None
    # CastXML can point a split conversion operator at the ``operator``
    # line, while the ``explicit`` keyword is on the preceding line.
    start = max(0, line_no - 4)
    window_parts: list[str] = []
    for line in lines[start : min(len(lines), line_no + 5)]:
        window_parts.append(line.strip())
        if line_no - 1 <= start + len(window_parts) - 1 and (
            ";" in line or "{" in line
        ):
            break
    window = " ".join(window_parts)
    operator_match = re.search(r"\boperator\b", window)
    if operator_match is None:
        return False
    prefix = window[: operator_match.start()]
    declaration_start = max(prefix.rfind(";"), prefix.rfind("{"), prefix.rfind("}"))
    return bool(re.search(r"\bexplicit\b", prefix[declaration_start + 1 :]))
