#!/usr/bin/env python3
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

"""ADR-067 D5 ``acknowledgments.breaks: accept``: the Action's notice line.

``python3 -I accepted_breaks_notice.py REPORT.json`` prints one
``::notice::`` naming the acknowledged breaks the run left non-gating
(``disposition_audit.accepted_breaks``, report schema 6.4), or nothing when
the report lists none -- ``breaks: gate``, nothing accepted, an older
report, or an unreadable file. Kept out of ``report_query.py`` (at its size
record) and, like it, stdlib-only so it runs before abicheck is importable.

Every report-sourced value is encoded for a workflow command (``%``, CR,
LF), so a symbol cannot end the line and forge a second command.
"""

from __future__ import annotations

import json
import sys
from typing import Any

#: Symbols named before "and K more".
MAX_SYMBOLS = 5

_VERDICTS = frozenset({"BREAKING", "API_BREAK"})


def command_escape(text: object) -> str:
    """GitHub workflow-command data encoding: ``%``, CR and LF."""
    return str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    audit = report.get("disposition_audit")
    rows = audit.get("accepted_breaks") if isinstance(audit, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def notice(report: object) -> str | None:
    """The notice line for *report*, or ``None`` when nothing was accepted."""
    rows = _rows(report) if isinstance(report, dict) else []
    if not rows:
        return None
    names = [str(r.get("symbol") or r.get("kind") or "?") for r in rows]
    shown = ", ".join(names[:MAX_SYMBOLS])
    if len(names) > MAX_SYMBOLS:
        shown += f" and {len(names) - MAX_SYMBOLS} more"
    verdict = report.get("verdict") if isinstance(report, dict) else None
    still = verdict if verdict in _VERDICTS else "a break"
    message = (
        f"abicheck accepted {len(rows)} acknowledged break(s) under "
        f"acknowledgments.breaks: accept; the verdict is still {still}. "
        f"See disposition_audit in the JSON report. Accepted: {shown}"
    )
    return "::notice::" + command_escape(message)


def main(argv: list[str]) -> int:
    try:
        with open(argv[1], encoding="utf-8") as handle:
            report = json.load(handle)
    except (IndexError, OSError, ValueError):
        return 0
    line = notice(report)
    if line is not None:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
