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

#: Verbs that *can* compare two sides. Whether a given call actually did is a
#: second question — see `comparison_command`. `dump` is absent at both levels:
#: an agent that only dumps both sides and reads the JSON by eye has not
#: obtained a verdict from the tool, which is the distinction dimension 1
#: grades.
COMPARISON_SUBCOMMANDS = frozenset({"compare", "scan", "compat"})

#: Exit statuses that mean *this command* produced a verdict — deliberately
#: per command, because the same number means different things. `scan`'s 5 is a
#: `--budget` overflow and its 6 is NOT_COMPARABLE, both of which happen before
#: or instead of the comparison; `compat`'s 3-11 are tool and input failures
#: (`compat/cli.py:_classify_compat_error_exit_code`). One shared set counted a
#: failed extraction as evidence, which is how an evaluation reports a tool
#: failure as a result.
_VERDICT_EXITS = {
    # 0/2/4 legacy, 1 severity-aware, 8 --fail-on-removed-library.
    "compare": frozenset({0, 1, 2, 4, 8}),
    "scan": frozenset({0, 1, 2, 4}),
    "compat": frozenset({0, 1, 2}),
}

#: "The two sides cannot be compared" — a real, deterministic outcome, but not
#: a verdict. Kept distinct so dimension 3 can credit the run for obtaining it
#: while dimension 6 still refuses to let a confident verdict rest on it.
_NOT_COMPARABLE_EXITS = {"scan": frozenset({6})}

#: Flags that can make a report greener than the findings warrant. `--suppress`
#: and `--policy-file` do it directly; the severity knobs do it by re-scoring
#: what counts as an error. Spelled out in full rather than as a `--severity`
#: stem: there is no generic `--severity` option, so that stem matched none of
#: the four real per-category overrides and an agent could re-score a
#: comparison with any of them while this recorded nothing.
SUPPRESSION_FLAGS = (
    "--suppress",
    "--policy-file",
    "--severity-preset",
    "--severity-abi-breaking",
    "--severity-potential-breaking",
    "--severity-quality-issues",
    "--severity-addition",
    "--exit-code-scheme",
)

#: The report's own verdict *field*, not any verdict word in the text. The
#: default Markdown report ends with a legend naming every verdict — so a
#: "most severe token wins" scan reads a `COMPATIBLE` report as `BREAKING`
#: off its own legend, and dimension 6 then rejects a correct compatible claim
#: as "safer than the run's own report". Confirmed against
#: `tests/golden/compatible_addition.md`.
#:
#: Matches `| **Verdict** | ❌ \`BREAKING\` |` and a plain `Verdict: BREAKING`
#: alike. Longest-first alternation so `COMPATIBLE_WITH_RISK` is not clipped
#: to `COMPATIBLE`.
_VERDICT_FIELD = re.compile(
    r"Verdict\**\s*[:|]\s*[^|\n]*?\b("
    + "|".join(sorted(VERDICT_ORDER, key=len, reverse=True))
    + r")\b"
)


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


def operation(call: dict) -> str | None:
    """The token following the verb, when it is itself a word rather than a flag.

    Only `compat` has a second level (`check` / `dump`), and Click requires it
    immediately after the group, so this is enough to tell them apart.
    """
    argv = call.get("argv", [])
    verb = subcommand(call)
    if verb is None or verb not in argv:
        return None
    rest = argv[argv.index(verb) + 1 :]
    return rest[0] if rest and not rest[0].startswith("-") else None


def comparison_command(call: dict) -> str | None:
    """Which command this call is, if it genuinely compares two sides.

    Being the right verb is not enough, and classifying on the verb alone let
    two one-sided operations count as comparisons:

    * `scan` without `--against` is a one-build audit — the CLI's own help says
      so ("Absence of `--against` already means a one-build audit").
    * `compat dump` creates a snapshot from an ABICC descriptor; `compat check`
      is the comparison. Bare `compat <options>` auto-invokes `check`, so an
      absent operation *is* a comparison.
    """
    verb = subcommand(call)
    if verb not in COMPARISON_SUBCOMMANDS:
        return None
    if verb == "scan":
        argv = call.get("argv", [])
        has_against = any(
            token == "--against" or token.startswith("--against=") for token in argv
        )
        return "scan" if has_against else None
    if verb == "compat":
        return None if operation(call) == "dump" else "compat"
    return "compare"


def is_comparison(call: dict) -> bool:
    return comparison_command(call) is not None


def ran_to_a_verdict(call: dict) -> bool:
    command = comparison_command(call)
    return command is not None and call.get("exit_code") in _VERDICT_EXITS[command]


def determined_not_comparable(call: dict) -> bool:
    """Whether this call established that the two sides cannot be compared."""
    command = comparison_command(call)
    return command is not None and call.get("exit_code") in _NOT_COMPARABLE_EXITS.get(
        command, frozenset()
    )


def compares_one_side_against_itself(call: dict) -> bool:
    """Whether this call names the same operand twice in a row (`compare x x`).

    A deliberately narrow check for a real hole: such a call exits cleanly with
    a verdict, so citing it satisfied every evidence rule while comparing
    nothing. Overstating severity is not a dimension-6 failure, so an agent
    could cite `compare x x` on a breaking scenario, claim BREAKING, and pass.

    Narrow because the general question — did this call compare the scenario's
    two sides? — is not answerable from argv. Identifying which tokens are
    operands needs the option table (`--format json` contributes a non-flag
    token too), and guessing wrong fails correct runs. Two *adjacent, equal*
    non-flag tokens is unambiguous and has no legitimate spelling. Binding a
    call to the fixture properly needs the dump provenance Phase 4 persists,
    not a cleverer read of the command line.
    """
    argv = call.get("argv", [])
    words = [(i, t) for i, t in enumerate(argv) if not t.startswith("-")]
    return any(b == a and j == i + 1 for (i, a), (j, b) in zip(words, words[1:]))


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

    JSON first, because that is unambiguous; otherwise the report's own verdict
    field. A report whose verdict cannot be located answers `None` rather than
    a guess — the consumer of this value only ever asks "is the claim greener
    than the tool's own answer", and inventing an answer there fails correct
    runs, which is the one outcome a zero-tolerance gate must not produce.
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
        for token in _VERDICT_FIELD.findall(text):
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
