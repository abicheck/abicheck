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

"""One ``compat check`` report file, in the format ``-report-format`` names.

``compat check`` writes up to three reports from one run: the primary one,
and the split binary and source reports ``-bin-report-path`` and
``-src-report-path`` request. Each is a binary or a source report, and
abi-compliance-checker says which in the HTML title and in the ``kind:``
field of the metadata comment its tooling parses. :func:`primary_report_kind`
is the one place that decides it for the primary report; the split reports
are binary and source by construction.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...checker_types import DiffResult

__all__ = ["primary_report_kind", "write_compat_report"]


def primary_report_kind(*, source_only: bool, binary_only: bool) -> str:
    """``"source"`` when the primary report holds source-level findings only.

    ``compat check`` filters it to them for ``-source`` without ``-binary``
    (``compat.cli._apply_result_transforms``); with both flags, or neither,
    it is a binary report, as abi-compliance-checker's is.
    """
    return "source" if source_only and not binary_only else "binary"


def write_compat_report(
    r: DiffResult,
    path: Path,
    *,
    fmt: str,
    lib_name: str,
    old_version: str,
    new_version: str,
    effective_title: str | None,
    compat_html: bool,
    arch: str | None,
    compiler_version: Callable[[], str],
    report_kind: str,
) -> None:
    """Write one report file for *r* in *fmt*.

    *report_kind* (``"binary"`` or ``"source"``) reaches the HTML report,
    the one format that records it. *compiler_version* is called only for
    the XML report, which records it; detecting it runs the compiler.
    """
    from ...html_report import write_html_report
    from ...reporter import to_json, to_markdown

    if fmt == "html":
        write_html_report(
            r,
            output_path=path,
            lib_name=lib_name,
            old_version=old_version,
            new_version=new_version,
            old_symbol_count=r.old_symbol_count,
            title=effective_title,
            compat_html=compat_html,
            report_kind=report_kind,
        )
    elif fmt == "xml":
        from ...compat.xml_report import write_xml_report

        write_xml_report(
            r,
            output_path=path,
            lib_name=lib_name,
            old_version=old_version,
            new_version=new_version,
            old_symbol_count=r.old_symbol_count,
            arch=arch or "",
            compiler=compiler_version(),
        )
    elif fmt == "json":
        # `include_exit_decision=False`: this is ABICC-compat's own
        # `report-format json`, whose real process exit follows the
        # 0/1/2 ABICC scheme (`_classify_compat_error_exit_code`), not the
        # native `legacy_exit_code`/`compute_exit_code` PR G1's `exit` block
        # would compute -- emitting it here would disagree with the actual
        # `compat check` exit code for the same run (Codex review).
        path.write_text(to_json(r, include_exit_decision=False), encoding="utf-8")
    else:
        path.write_text(to_markdown(r, demangle=True), encoding="utf-8")
