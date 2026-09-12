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

"""The `action-cli-mirror` AI-readiness check (ADR-070 D4, plan Phase 4).

A comment in the Action shell layer that justifies itself by naming a CLI
symbol may name a symbol that **exists**. Nothing checked that before, and the
2026-09-12 Action-vs-CLI audit found four such comments in `action/run.sh`
citing `abicheck/cli_scan.py` and `scan_engine` — both deleted with ADR-068's
retirement of `scan`. A reader checking one against its cited source found
nothing and could not tell whether the code or the comment was wrong.

**Convention.** A justification records its referent as

    # cli-mirror: <repo-relative-path>::<symbol>

and this check resolves every one: the file must exist, and the symbol must
appear in it as a real definition or assignment (`def`/`class`/`NAME =`), not
merely as a word in prose.

**What this does NOT prove, stated here because it is the thing most likely to
be forgotten.** Symbol *existence* is a far weaker property than symbol
*behaviour*. Both drifts the audit found in `run.sh`'s release-operand guards
live inside functions that still exist and merely reject less than they used
to — this check would have passed on both, before and after. It is therefore a
guard against *citations rotting*, never evidence that a mirrored restriction
is still accurate. ADR-070's own alternatives section records that limit, and a
gate trusted for more than it proves is worse than no gate.

Deliberately not mandatory: this does not require every guard to carry an
annotation, because a sweep adding ~20 unreviewed annotations would be exactly
the unverified bulk `AGENTS.md` warns against. It checks the ones that exist,
so each annotation added is permanently load-bearing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[1]

#: `# cli-mirror: path::symbol`, tolerating leading comment indentation.
MIRROR_RE = re.compile(
    r"#\s*cli-mirror:\s*(?P<path>[\w./\-]+\.py)::(?P<symbol>[A-Za-z_][\w]*)"
)

#: Shell/YAML files whose comments may carry the annotation.
ANNOTATED_FILES: tuple[str, ...] = (
    "action/run.sh",
    "action/validate-inputs.sh",
    "actions/check-target/action.yml",
    "actions/check-target/validate-inputs.sh",
    "actions/check-target/run.sh",
)


class Findings(Protocol):
    """The sink `check_ai_readiness.py`'s own `Findings` already satisfies."""

    def error(self, check: str, msg: str) -> None: ...


def _defines(text: str, symbol: str) -> bool:
    """Whether *text* defines *symbol* rather than merely mentioning it."""
    patterns = (
        rf"^\s*def\s+{re.escape(symbol)}\s*\(",
        rf"^\s*async\s+def\s+{re.escape(symbol)}\s*\(",
        rf"^\s*class\s+{re.escape(symbol)}\b",
        rf"^\s*{re.escape(symbol)}\s*(:[^=]+)?=",
    )
    return any(re.search(p, text, re.MULTILINE) for p in patterns)


def check_action_cli_mirror(findings: Findings, root: Path | None = None) -> None:
    """Resolve every `cli-mirror:` annotation in the Action shell layer.

    *root* exists for tests that stage a tree; production callers pass nothing
    and get the repository this file lives in.
    """
    root = ROOT if root is None else root
    check = "action-cli-mirror"
    seen = 0
    for rel in ANNOTATED_FILES:
        path = root / rel
        if not path.is_file():
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            match = MIRROR_RE.search(line)
            if match is None:
                continue
            seen += 1
            target = root / match.group("path")
            symbol = match.group("symbol")
            if not target.is_file():
                findings.error(
                    check,
                    f"{rel}:{lineno}: cli-mirror names "
                    f"{match.group('path')}, which does not exist",
                )
                continue
            if not _defines(target.read_text(encoding="utf-8"), symbol):
                findings.error(
                    check,
                    f"{rel}:{lineno}: cli-mirror names {symbol} in "
                    f"{match.group('path')}, which defines no such symbol "
                    "(a renamed or deleted referent, or a typo) — update the "
                    "citation to what actually owns this behaviour now, or "
                    "delete the claim",
                )
    if seen == 0:
        findings.error(
            check,
            "no cli-mirror annotations found in the Action shell layer; "
            "the convention is documented in ADR-070 D4 and this check is "
            "meaningless without at least one — if they were all removed "
            "deliberately, remove this check too rather than leaving it "
            "passing vacuously",
        )
