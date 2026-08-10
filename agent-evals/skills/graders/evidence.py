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

import functools
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
#:
#: One code per command, because each maintains an independent scheme
#: (`docs/reference/exit-codes.md`): native `compare` answers 16, `scan
#: --against` 6, `compat check` 9. Recognizing only `scan`'s made a correct
#: not-comparable run on either other command read as "no comparison
#: completed" — a false dimension-3 failure.
_NOT_COMPARABLE_EXITS = {
    "scan": frozenset({6}),
    "compare": frozenset({16}),
    "compat": frozenset({9}),
}

#: Modes that resolve an invocation without running it. `--dry-run` is explicit
#: about this ("never returns a verdict code", exits 0/1/64) and `--help` exits
#: 0, so both looked like clean comparisons to an exit-code check alone — a
#: guessed verdict could cite one and satisfy every evidence rule.
NON_EXECUTING_FLAGS = ("--help", "-h", "--dry-run")

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
    if any(token in NON_EXECUTING_FLAGS for token in call.get("argv", [])):
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


@functools.cache
def _option_tables(command: str | None) -> tuple[frozenset[str], frozenset[str]] | None:
    """`(every option this command declares, the subset taking no value)`.

    Both halves come from Click's own command tree. The first is what keeps a
    single-dash *long* option from being mistaken for a cluster of short ones:
    `compat check` speaks ABICC's vocabulary (`-old`, `-new`, `-d1`), and
    expanding `-old` into `-o ld` turned an ordinary comparison into a
    self-comparison — a correct run failing the strictest dimension, which is
    the one outcome that gets a gate switched off.
    """
    try:
        import click

        from abicheck.cli import main as cli_main
    except Exception:  # pragma: no cover - depends on the grading environment
        return None

    def options_of(cmd: object) -> tuple[set[str], set[str]]:
        every: set[str] = set()
        valueless: set[str] = set()
        for param in getattr(cmd, "params", []):
            if not isinstance(param, click.Option):
                continue
            for opt in (*param.opts, *param.secondary_opts):
                every.add(opt)
                if param.is_flag:
                    valueless.add(opt)
        return every, valueless

    every, valueless = options_of(cli_main)  # global options precede the verb
    target = cli_main.commands.get(command or "")
    if isinstance(target, click.Group):
        # `compat check` is the comparison; bare `compat` auto-invokes it.
        target = target.commands.get("check", target)
    if target is not None:
        more_every, more_valueless = options_of(target)
        every |= more_every
        valueless |= more_valueless
    return frozenset(every), frozenset(valueless)


def valueless_options(command: str | None) -> frozenset[str] | None:
    """Every option of this command that takes no value, from Click itself.

    Read off the real command tree rather than listed here: `compare` alone
    declares 50 boolean flags, so a hand-maintained list would be wrong on
    arrival and would rot silently after — the same failure the `--severity`
    stem had.

    `None` when the table cannot be built (abicheck not importable at grading
    time). The caller then treats every option as value-taking, which is what
    this code did before the table existed: it can miss a self-comparison,
    where the opposite assumption would read `--policy-file p.yaml --suppress
    p.yaml` as two operands named `p.yaml` and fail a correct run.
    """
    tables = _option_tables(command)
    return None if tables is None else tables[1]


def _consumes_a_value(token: str, command: str | None) -> bool:
    """Whether this option token takes the *next* token as its value."""
    if not token.startswith("-"):
        return False
    if "=" in token:  # `--format=json` carries its own value
        return False
    known = valueless_options(command)
    if known is None:
        return True
    # An option the table does not know is assumed to take a value: guessing
    # the other way invents an operand, and an invented operand is what fails
    # a correct run.
    return token not in known


def _expand_clusters(argv: list[str], command: str | None) -> list[str]:
    """`argv` with Click's short-option clusters written out one option each.

    Click packs short options: `-vv` is `-v -v`, and `-voreport.json` is
    `-v -o report.json` (both verified against the real CLI). An unexpanded
    cluster is not in the arity table under its own spelling, so it fell to the
    "unknown options take a value" default and swallowed the token after it —
    `compare x -vv x` then showed one operand, and a self-comparison passed.

    A cluster ends at the first option that takes a value: everything after
    that letter is its value, or the next token when nothing follows. Chars the
    table does not recognize are treated as value-taking, the same conservative
    default `_consumes_a_value` uses — it can leave a self-comparison
    undetected, where the opposite invents an operand and fails a correct run.
    """
    tables = _option_tables(command)
    if tables is None:
        return list(argv)
    every, known = tables
    out: list[str] = []
    for token in argv:
        if (
            not (
                len(token) > 2
                and token.startswith("-")
                and not token.startswith("--")
                and "=" not in token
            )
            # A declared single-dash *long* option is not a cluster. `compat
            # check` speaks ABICC's vocabulary, and expanding `-old` into
            # `-o ld` made an ordinary comparison read as a self-comparison —
            # a correct run failing the strictest dimension, which is the one
            # outcome that gets a gate switched off.
            or token in every
        ):
            out.append(token)
            continue
        for position, letter in enumerate(token[1:], start=2):
            option = f"-{letter}"
            out.append(option)
            if option not in known:  # takes a value; the rest of the token is it
                if token[position:]:
                    out.append(token[position:])
                break
    return out


def _positional_operands(argv: list[str], command: str | None = None) -> list[str]:
    """The tokens that are operands rather than options or option values.

    Click lets options sit between positionals, so position alone says nothing
    — `compare x --format json x` really does compare `x` with itself. The
    complement is what identifies an operand: a non-option token that is not
    being consumed as some option's value.

    Arity is what makes that answerable, and it has to come from the command's
    own definition. Treating *every* option as value-taking hid the operand
    after a boolean flag (`compare x --verbose x` yielded one operand), and
    `--verbose` is exactly that — `cli_options.py` declares it `is_flag=True`,
    so Click accepts the placement without consuming `x`.
    """
    operands: list[str] = []
    expanded = _expand_clusters(argv, command)
    for index, token in enumerate(expanded):
        if token.startswith("-"):
            continue
        if index and _consumes_a_value(expanded[index - 1], command):
            continue
        operands.append(token)
    return operands


#: Options that carry one of the two sides rather than a mere setting. Only
#: `compare` names both sides positionally; `scan` takes the baseline through
#: `--against`, and `compat check` takes both through `-old`/`-new` (with their
#: `-d1`/`-d2`/`-n` aliases). Without these, `scan lib.so --against lib.so` and
#: `compat check -old a.xml -new a.xml` are self-comparisons the operand rule
#: cannot see, because each repeated token is an option's *value*.
SIDE_OPTIONS = ("--against", "-old", "-d1", "-new", "-d2", "-n")


def _named_sides(argv: list[str], command: str | None) -> list[str]:
    """Every token this invocation names as one of the two sides."""
    sides = _positional_operands(argv, command)
    for index, token in enumerate(argv):
        for option in SIDE_OPTIONS:
            if token == option and index + 1 < len(argv):
                sides.append(argv[index + 1])
            elif token.startswith(f"{option}="):
                sides.append(token.split("=", 1)[1])
    return sides


def compares_one_side_against_itself(call: dict) -> bool:
    """Whether this call names one operand twice (`compare x x`).

    A real hole rather than a hypothetical one: such a call exits cleanly with
    a verdict, so citing it satisfies every evidence rule while comparing
    nothing. Overstating severity is not a dimension-6 failure, so an agent
    could cite it on a breaking scenario, claim BREAKING, and pass having
    compared nothing.

    This used to test *adjacency*, which Click does not require. Verified
    against the real CLI: `abicheck compare x.so --format json x.so` runs the
    comparison, exits 0 and reports `NO_CHANGE`, while the two `x.so` tokens
    sit three apart — so the interleaved spelling walked straight through the
    check the plain one is caught by. Operand identification (above) closes
    that without needing the option table.

    Still narrow, and deliberately so: the general question — did this call
    compare the scenario's *own* two sides? — is not answerable from argv at
    all. Binding a call to its fixture needs the dump provenance Phase 4
    persists, not a cleverer read of the command line.
    """
    sides = _named_sides(call.get("argv", []), subcommand(call))
    return len(sides) != len(set(sides))


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
