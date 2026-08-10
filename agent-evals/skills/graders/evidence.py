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

"""Reading what a run actually did, from the shim's record (G37 D3).

Everything here answers a question about the *artifact*, never about the
answer's prose: which calls were comparisons, which of them really ran, what
verdict the tool itself produced, and whether the run reached that verdict by
suppressing findings.

The last one is why this module exists separately from the claim reader. A
false green produced by `--suppress` is indistinguishable from a true green if
you only look at the verdict; the shim sees the flag, so the grader can too.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .claim import VERDICT_ORDER

#: Subcommands that compare two sides. `dump` is deliberately absent: an agent
#: that only dumps both sides and reads the JSON by eye has not obtained a
#: verdict from the tool, which is the distinction dimension 1 grades.
COMPARISON_SUBCOMMANDS = frozenset({"compare", "scan", "compat"})

#: Exit statuses that mean a comparison ran and produced an answer, whatever
#: that answer was. `64` is a usage error and `70` is the shim's own "not
#: configured" — both are runs that never reached a verdict, and counting them
#: as evidence is how an evaluation reports tool failures as results.
_VERDICT_EXITS = frozenset({0, 1, 2, 4, 5, 6, 8})

#: Flags that can make a report greener than the findings warrant. `--suppress`
#: and `--policy-file` do it directly; the severity knobs do it by re-scoring
#: what counts as an error.
SUPPRESSION_FLAGS = (
    "--suppress",
    "--policy-file",
    "--severity-preset",
    "--severity",
    "--exit-code-scheme",
)

_VERDICT_TOKEN = re.compile(r"\b(" + "|".join(VERDICT_ORDER) + r")\b")


def load_calls(run_dir: Path) -> list[dict]:
    """Every recorded call, in the order the shim assigned."""
    path = run_dir / "calls.jsonl"
    if not path.is_file():
        return []
    calls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            calls.append(record)
    return sorted(calls, key=lambda c: c.get("seq", 0))


def subcommand(call: dict) -> str | None:
    """The verb this invocation used, ignoring leading global flags."""
    skip_value = False
    for token in call.get("argv", []):
        if skip_value:
            skip_value = False
            continue
        if token.startswith("-"):
            # A global flag taking a separate value must not have that value
            # mistaken for the verb (`-v` takes none, `--config x` does).
            skip_value = "=" not in token and token in ("--config", "--log-level")
            continue
        return token
    return None


def is_comparison(call: dict) -> bool:
    return subcommand(call) in COMPARISON_SUBCOMMANDS


def ran_to_a_verdict(call: dict) -> bool:
    return is_comparison(call) and call.get("exit_code") in _VERDICT_EXITS


def suppression_flags(call: dict) -> list[str]:
    """Suppression-shaped flags this call passed, if any."""
    used = []
    for token in call.get("argv", []):
        for flag in SUPPRESSION_FLAGS:
            if token == flag or token.startswith(f"{flag}="):
                used.append(flag)
    return sorted(set(used))


def _artifact_texts(run_dir: Path, call: dict) -> list[str]:
    """Everything this call produced that could carry a verdict."""
    texts: list[str] = []
    paths: list[str] = []
    if call.get("stdout_path"):
        paths.append(call["stdout_path"])
    for output in call.get("outputs") or []:
        if output.get("status") == "captured" and output.get("kind") == "file":
            paths.append(output["path"])
    for rel in paths:
        path = run_dir / rel
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
    return texts


def reported_verdict(run_dir: Path, call: dict) -> str | None:
    """The verdict the tool itself produced for this call, if it stated one.

    JSON first, because that is unambiguous. The text fallback takes the *most
    severe* token present rather than the first: a human-readable report names
    a verdict in its summary and may name others while explaining them, and
    under-reading the tool's own answer is the direction that lets a false
    green through.
    """
    severest: int | None = None
    for text in _artifact_texts(run_dir, call):
        try:
            parsed: Any = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("verdict") in VERDICT_ORDER:
            index = VERDICT_ORDER.index(parsed["verdict"])
            severest = index if severest is None else max(severest, index)
            continue
        for token in _VERDICT_TOKEN.findall(text):
            index = VERDICT_ORDER.index(token)
            severest = index if severest is None else max(severest, index)
    return None if severest is None else VERDICT_ORDER[severest]


def strongest_reported_verdict(run_dir: Path, calls: list[dict]) -> str | None:
    """The most severe verdict any real comparison in this run produced."""
    severest: int | None = None
    for call in calls:
        if not ran_to_a_verdict(call):
            continue
        verdict = reported_verdict(run_dir, call)
        if verdict is None:
            continue
        index = VERDICT_ORDER.index(verdict)
        severest = index if severest is None else max(severest, index)
    return None if severest is None else VERDICT_ORDER[severest]
