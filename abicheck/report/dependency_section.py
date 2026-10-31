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

"""The "Dependency Analysis" section: one side's ``--follow-deps`` walk, as text.

One renderer for every report that shows the walk: a single-pair ``compare``
(Markdown and plain terminal text) and each member of a directory/package
release (Markdown). The content is identical across projections; only the
markup differs, so it is written once and takes a :data:`DepsStyle`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict
from typing import TYPE_CHECKING

from ..model.extraction_contract import DependencyInfo

if TYPE_CHECKING:
    from ..model import AbiSnapshot

__all__ = [
    "MARKDOWN_DEPS_STYLE",
    "PLAIN_DEPS_STYLE",
    "DepsStyle",
    "dependency_info_fields",
    "dependency_section",
    "release_member_dependency_section",
]

#: (section heading prefix, side heading prefix, field format, value format).
DepsStyle = tuple[str, str, str, str]
MARKDOWN_DEPS_STYLE: DepsStyle = ("## ", "### ", "**{}**", "`{}`")
PLAIN_DEPS_STYLE: DepsStyle = ("", "", "{}", "{}")
#: A release member's section sits one level under the release's own headings.
_RELEASE_MEMBER_STYLE: DepsStyle = ("### ", "#### ", "**{}**", "`{}`")


def dependency_info_fields(
    old: AbiSnapshot | None, new: AbiSnapshot | None
) -> dict[str, object]:
    """``old_dependency_info``/``new_dependency_info`` for the sides that walked.

    The keys the single-pair JSON report adds under ``--follow-deps``; a side
    whose snapshot has no walk is absent, never an empty graph.
    """
    out: dict[str, object] = {}
    for side, snap in (("old", old), ("new", new)):
        if snap is not None and snap.dependency_info is not None:
            out[f"{side}_dependency_info"] = asdict(snap.dependency_info)
    return out


def dependency_section(
    sides: Iterable[tuple[str, str, DependencyInfo | None]],
    style: DepsStyle = MARKDOWN_DEPS_STYLE,
    *,
    title: str = "Dependency Analysis",
) -> str:
    """The section for *sides*, each ``(label, name, info)``; a ``None`` info is
    skipped. *name* is shown beside the label (a version, or a library)."""
    section, subsection, field, code_fmt = style

    def code(text: object) -> str:
        return code_fmt.format(text)

    def bold(text: str) -> str:
        return field.format(text)

    lines: list[str] = ["", f"{section}{title}", ""]
    for label, name, info in sides:
        if info is None:
            continue
        lines.append(f"{subsection}{label} ({code(name)})")
        lines.append("")
        if info.nodes:
            lines.append(f"{bold('Dependencies')}: {len(info.nodes)} resolved DSOs")
            for node in info.nodes:
                raw_depth = node.get("depth", 0)
                depth = raw_depth if isinstance(raw_depth, int) else 0
                indent = "  " * depth
                reason = node.get("resolution_reason", "")
                lines.append(f"  {indent}- {code(node.get('soname', '?'))} ({reason})")
            lines.append("")
        if info.bindings_summary:
            lines.append(f"{bold('Bindings')}:")
            for status, count in sorted(info.bindings_summary.items()):
                lines.append(f"  - {code(status)}: {count}")
            lines.append("")
        if info.unresolved:
            lines.append(f"{bold('Unresolved libraries')}:")
            for u in info.unresolved:
                lines.append(
                    f"  - {code(u.get('soname', '?'))} needed by "
                    f"{code(u.get('consumer', '?'))}"
                )
            lines.append("")
        if info.missing_symbols:
            lines.append(f"{bold('Missing symbols')}: {len(info.missing_symbols)}")
            for ms in info.missing_symbols[:10]:
                ver = f"@{ms['version']}" if ms.get("version") else ""
                lines.append(f"  - {code(str(ms['symbol']) + ver)}")
            if len(info.missing_symbols) > 10:
                lines.append(f"  - ... +{len(info.missing_symbols) - 10} more")
            lines.append("")
    return "\n".join(lines)


def release_member_dependency_section(entry: Mapping[str, object]) -> str:
    """One release member's section, read from its JSON entry's
    ``old_dependency_info``/``new_dependency_info``; ``""`` when neither is
    present (the walk was not requested, or no side was ELF)."""
    sides: list[tuple[str, str, DependencyInfo | None]] = []
    for label, key in (("Old", "old_dependency_info"), ("New", "new_dependency_info")):
        raw = entry.get(key)
        if isinstance(raw, Mapping):
            sides.append((label, str(entry.get("library", "?")), DependencyInfo(**raw)))
    if not sides:
        return ""
    return dependency_section(
        sides,
        _RELEASE_MEMBER_STYLE,
        title=f"Dependency Analysis: {entry.get('library', '?')}",
    )
