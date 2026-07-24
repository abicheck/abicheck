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

"""Output rendering for a :class:`~abicheck.checker_types.DiffResult`.

Extracted from :mod:`abicheck.service` so that module stays under the
AI-readiness size cap. This is a leaf module: it does not import
``abicheck.service`` and is re-exported there for backward compatibility.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import ValidationError
from .model import AbiSnapshot
from .reporter import to_json, to_markdown, to_stat, to_stat_json

if TYPE_CHECKING:
    from .checker_types import DiffResult
    from .severity import SeverityConfig


def render_output(
    fmt: str,
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot | None = None,
    *,
    follow_deps: bool = False,
    show_only: str | None = None,
    report_mode: str = "full",
    show_impact: bool = False,
    stat: bool = False,
    severity_config: SeverityConfig | None = None,
    show_recommendation: bool = False,
    demangle: bool = False,
) -> str:
    """Render comparison result in the requested output format.

    Supported formats: ``'json'``, ``'markdown'``, ``'sarif'``, ``'html'``,
    ``'junit'``.

    ``demangle`` only affects human-facing formats (markdown, review); machine
    formats (json/sarif/junit) always keep raw mangled symbols so downstream
    tooling can match on them.

    Raises:
        ValidationError: For unrecognised output format.
    """
    if stat and fmt != "junit":
        if fmt == "json":
            return to_stat_json(result, severity_config=severity_config)
        return to_stat(result, severity_config=severity_config)

    if fmt == "json":
        return _render_json_output(
            result,
            old,
            new,
            follow_deps=follow_deps,
            show_only=show_only,
            report_mode=report_mode,
            show_impact=show_impact,
            severity_config=severity_config,
        )

    if fmt == "sarif":
        from .sarif import to_sarif_str

        return to_sarif_str(
            result,
            show_only=show_only,
            report_mode=report_mode,
            severity_config=severity_config,
        )

    if fmt == "html":
        from .html_report import generate_html_report

        return generate_html_report(
            result,
            lib_name=old.library,
            old_version=old.version,
            new_version=new.version if new else "new",
            old_symbol_count=result.old_symbol_count,
            show_only=show_only,
            show_impact=show_impact,
            severity_config=severity_config,
        )

    if fmt == "junit":
        from .junit_report import to_junit_xml

        return to_junit_xml(
            result,
            old,
            show_only=show_only,
            severity_config=severity_config,
        )

    if fmt == "review":
        from .reporter import to_review_digest

        txt = to_review_digest(result, severity_config=severity_config)
        if demangle:
            from .demangle import demangle_text

            txt = demangle_text(txt)
        return txt

    _SUPPORTED_FORMATS = {"json", "sarif", "html", "junit", "markdown", "md", "review"}
    if fmt not in _SUPPORTED_FORMATS:
        raise ValidationError(
            f"Unsupported output format: {fmt!r} (expected one of {sorted(_SUPPORTED_FORMATS)})"
        )

    # Default: markdown
    md = to_markdown(
        result,
        show_only=show_only,
        report_mode=report_mode,
        show_impact=show_impact,
        severity_config=severity_config,
        show_recommendation=show_recommendation,
    )
    if follow_deps and (old.dependency_info or (new and new.dependency_info)):
        md += _render_deps_section_md(old, new)
    if demangle:
        from .demangle import demangle_text

        md = demangle_text(md)
    return md


def _render_json_output(
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot | None,
    *,
    follow_deps: bool,
    show_only: str | None,
    report_mode: str,
    show_impact: bool,
    severity_config: SeverityConfig | None,
) -> str:
    """Render comparison result as JSON, optionally including dependency info."""
    base = to_json(
        result,
        show_only=show_only,
        report_mode=report_mode,
        show_impact=show_impact,
        severity_config=severity_config,
    )
    if follow_deps and (old.dependency_info or (new and new.dependency_info)):
        import json
        from dataclasses import asdict

        d = json.loads(base)
        if old.dependency_info:
            d["old_dependency_info"] = asdict(old.dependency_info)
        if new and new.dependency_info:
            d["new_dependency_info"] = asdict(new.dependency_info)
        return json.dumps(d, indent=2)
    return base


def _render_deps_section_md(old: AbiSnapshot, new: AbiSnapshot | None) -> str:
    """Append dependency summary section to markdown output."""
    lines: list[str] = ["", "## Dependency Analysis", ""]

    for label, snap in [("Old", old), ("New", new)]:
        if snap is None or snap.dependency_info is None:
            continue
        info = snap.dependency_info
        lines.append(f"### {label} version (`{snap.version}`)")
        lines.append("")

        if info.nodes:
            lines.append(f"**Dependencies**: {len(info.nodes)} resolved DSOs")
            for node in info.nodes:
                raw_depth = node.get("depth", 0)
                depth = raw_depth if isinstance(raw_depth, int) else 0
                indent = "  " * depth
                reason = node.get("resolution_reason", "")
                lines.append(f"  {indent}- `{node.get('soname', '?')}` ({reason})")
            lines.append("")

        if info.bindings_summary:
            lines.append("**Bindings**:")
            for status, count in sorted(info.bindings_summary.items()):
                lines.append(f"  - `{status}`: {count}")
            lines.append("")

        if info.unresolved:
            lines.append("**Unresolved libraries**:")
            for u in info.unresolved:
                lines.append(
                    f"  - `{u.get('soname', '?')}` needed by `{u.get('consumer', '?')}`"
                )
            lines.append("")

        if info.missing_symbols:
            lines.append(f"**Missing symbols**: {len(info.missing_symbols)}")
            for ms in info.missing_symbols[:10]:
                ver = f"@{ms['version']}" if ms.get("version") else ""
                lines.append(f"  - `{ms['symbol']}{ver}`")
            if len(info.missing_symbols) > 10:
                lines.append(f"  - ... +{len(info.missing_symbols) - 10} more")
            lines.append("")

    return "\n".join(lines)
