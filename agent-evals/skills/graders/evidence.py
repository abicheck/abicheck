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
from collections.abc import Iterable
from pathlib import Path
from typing import Any, NamedTuple

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
#: about this ("never returns a verdict code", exits 0/1/64) and the help modes
#: exit 0, so all of them looked like clean comparisons to an exit-code check
#: alone — a guessed verdict could cite one and satisfy every evidence rule.
#:
#: `--help-all` ("Show every option") is a second help mode, not a variant of
#: the first, and it is the one that was missing: verified that `compare old
#: new --help-all` exits 0 having printed help rather than a report. Worse than
#: an ordinary miss, since that help text names `verdict` many times over,
#: which is exactly what the artifact readers scan.
NON_EXECUTING_FLAGS = ("--help", "-h", "--help-all", "--dry-run")

#: Eager *global* options that print and exit before the verb runs at all.
#: Position is the whole distinction and cannot be skipped: `compare` declares
#: its own value-taking `--version` ("Version label used when an input is a
#: bare .so file"), so `compare a.so b.so --version 1.2` is an ordinary
#: comparison, while `abicheck --version compare a.so b.so` prints the version
#: and exits 0 having compared nothing. Matching the token anywhere would have
#: failed the correct run to catch the empty one.
EAGER_GLOBAL_FLAGS = ("--version",)


def exits_before_the_verb(call: dict) -> bool:
    """Whether an eager global option resolved this call before it ran."""
    for token in call.get("argv", []):
        if token in EAGER_GLOBAL_FLAGS:
            return True
        if not token.startswith("-"):
            return False  # the verb; anything after it belongs to the command
    return False


#: Flags that can make a report greener than the findings warrant. `--suppress`
#: and `--policy` do it directly; the severity knobs do it by re-scoring what
#: counts as an error. The four per-category `--severity-*` overrides were
#: removed from the CLI along with `--policy-file` (one flag, `--policy`,
#: now takes a profile name or a document path), so `--severity-preset` is the
#: only severity re-scoring an agent can reach from a command line -- the
#: rest is `.abicheck.yml`, which this grader reads a *call*'s argv for and
#: therefore never sees. Still spelled out in full rather than as a
#: `--severity` stem: there is no generic `--severity` option for a stem to
#: match, and a stem would silently start matching a future per-category flag
#: nobody reviewed against this list.
SUPPRESSION_FLAGS = (
    "--suppress",
    "--policy",
    "--severity-preset",
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
    if exits_before_the_verb(call):
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


#: The dials `shared/consumer-scoping.md` documents. A call carrying any of
#: these answers a *different* question from an unscoped comparison of the
#: same pair — "does it break this consumer", not "does it break someone" —
#: and per that doc the two verdicts "legitimately diverge in both
#: directions", so neither is a filtered view of the other.
CONSUMER_SCOPE_FLAGS = ("--used-by", "--required-symbol", "--required-symbols")


def is_consumer_scoped(call: dict) -> bool:
    """Whether this call named a consumer/host, narrowing the question asked."""
    for token in call.get("argv", []):
        for flag in CONSUMER_SCOPE_FLAGS:
            if token == flag or token.startswith(f"{flag}="):
                return True
    return False


#: Compiled-artifact suffixes stripped from a `--used-by` operand's basename,
#: on top of the plain basename match — a versioned SONAME (`renderer.so.2`)
#: strips every trailing `.N` segment along with the leading `.so`.
_BINARY_SUFFIX_RE = re.compile(r"\.(so(\.\d+)*|dll|dylib|exe)$", re.IGNORECASE)


def _stripped_binary_names(name: str) -> set[str]:
    """`name` with a trailing compiled-artifact suffix and/or a leading
    conventional `lib` prefix removed, every combination that applies.

    `--used-by` names a real path to the *built* consumer
    (`references/abicheck-adapter.md`'s own worked example: `--used-by
    path/to/consumer-binary`), and a real Unix shared-library build
    conventionally gives that path both a platform suffix (`renderer.so`,
    `renderer.so.2`, `renderer.dll`) and, for the plain-`.so` case
    specifically, a leading `lib` prefix (`librenderer.so`) — while a
    scenario's `invocation.used_by` declares the bare logical name
    (`renderer`) the fixture calls it. The plain-basename match already
    handles a literal, suffix-free operand; this closes the far more common
    case of an actual compiled artifact, in either shape. `lib` is stripped
    only when a recognized suffix was also present — `libwidget` (a real,
    unrelated consumer literally named that) must not lose its `lib` on the
    strength of a prefix alone. Returns an empty set when nothing matched,
    so a caller adds only genuinely new candidates.
    """
    match = _BINARY_SUFFIX_RE.search(name)
    if not match:
        return set()
    suffix_stripped = name[: match.start()]
    if not suffix_stripped:
        return set()
    candidates = {suffix_stripped}
    if suffix_stripped.startswith("lib") and len(suffix_stripped) > len("lib"):
        candidates.add(suffix_stripped[len("lib") :])
    return candidates


def _required_symbols_file_targets(call: dict, value: str) -> list[str]:
    """The symbol names listed in a `--required-symbols FILE` argument.

    Best-effort, silently empty on any failure — matching the
    false-negative-over-false-positive default this module uses throughout.
    `value` is resolved against the call's own recorded `cwd` (the shim
    stamps this from `Path.cwd()` at call time — the workspace directory
    under the run directory, which persists on disk after the run, unlike
    the process's own transient cwd), not against the run directory itself:
    a relative `--required-symbols` operand is relative to where the agent
    ran the command, which need not be the workspace root.
    """
    cwd = call.get("cwd")
    if not cwd:
        return []
    path = (Path(cwd) / value).resolve()
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


class ScopeTargets(NamedTuple):
    """Targets this call declared, kept apart by which dial produced them.

    `--used-by` and `--required-symbol(s)` are two distinct scoping
    mechanisms with different meanings (`abicheck compare --help`:
    "narrow the compatibility question to a specific consumer" vs. "...to
    a specific required symbol") — a call scoped to a *consumer* answers a
    different question from one scoped to a *symbol*, even when the two
    happen to share a spelling. Merging them into one set let a
    `--required-symbol analytics-daemon` call (a coincidental name
    collision, not an actual consumer analysis) satisfy a scenario's
    declared `used_by: [analytics-daemon]` requirement (Codex review,
    PR #808).
    """

    used_by: frozenset[str]
    required_symbols: frozenset[str]


def consumer_scope_targets_by_kind(call: dict) -> ScopeTargets:
    """The consumer/symbol identities this call passed, split by dial.

    `--used-by`/`--required-symbol`, each repeatable and each carrying its
    value as a plain string operand, are recovered directly from argv.
    `--required-symbols FILE` names a *file*, not the symbols themselves —
    its contents are read (see `_required_symbols_file_targets`) and each
    listed symbol contributes as its own `required_symbols` target; a file
    that can't be read (workspace already gone, unreadable path)
    contributes nothing rather than fabricating a match.

    `--used-by` takes a real path to the consumer binary, not a bare logical
    name — while a scenario's `invocation.used_by` declares the logical name
    (`renderer`) the fixture calls it. Matching the literal operand alone
    would hard-fail every correctly-scoped run, so each raw value
    contributes itself, its path basename, and (see `_stripped_binary_names`)
    that basename with its compiled-artifact suffix and/or conventional
    `lib` prefix stripped — `--required-symbol`'s operand is already a bare
    symbol name with no path structure, suffix, or prefix, so all of these
    transforms are no-ops there.
    """
    argv = call.get("argv", [])
    used_by: set[str] = set()
    required_symbols: set[str] = set()

    def _add(bucket: set[str], value: str) -> None:
        bucket.add(value)
        basename = Path(value).name
        bucket.add(basename)
        bucket.update(_stripped_binary_names(basename))

    def _add_required_symbols_file(value: str) -> None:
        for symbol in _required_symbols_file_targets(call, value):
            _add(required_symbols, symbol)

    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--used-by" and i + 1 < len(argv):
            _add(used_by, argv[i + 1])
            i += 2
            continue
        if token == "--required-symbol" and i + 1 < len(argv):
            _add(required_symbols, argv[i + 1])
            i += 2
            continue
        if token == "--required-symbols" and i + 1 < len(argv):
            _add_required_symbols_file(argv[i + 1])
            i += 2
            continue
        if token.startswith("--used-by="):
            _add(used_by, token[len("--used-by=") :])
        elif token.startswith("--required-symbol="):
            _add(required_symbols, token[len("--required-symbol=") :])
        elif token.startswith("--required-symbols="):
            _add_required_symbols_file(token[len("--required-symbols=") :])
        i += 1
    return ScopeTargets(frozenset(used_by), frozenset(required_symbols))


def consumer_scope_targets(call: dict) -> frozenset[str]:
    """The union of `consumer_scope_targets_by_kind(call)`'s two buckets.

    Kept for callers that only need "was any declared target touched at
    all" and don't need to distinguish which scoping mechanism produced
    it — `is_consumer_scoped`-adjacent checks, and this module's own
    tests. A check that must honor a *specific* declared `used_by` or
    `required_symbols` target should call `consumer_scope_targets_by_kind`
    directly instead, the way `dimensions.py`'s declared-target check
    does — merging back into one set here is exactly the collapse that
    let a same-spelled symbol satisfy an unrelated consumer requirement.
    """
    by_kind = consumer_scope_targets_by_kind(call)
    return by_kind.used_by | by_kind.required_symbols


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


def contract_mode(call: dict) -> str | None:
    """The `--contract` value this call passed, if any.

    Distinct from `is_consumer_scoped`/`CONSUMER_SCOPE_FLAGS`: a contract
    domain (`public`/`exports`/`all`/`auto`) narrows *which evidence the
    tool consults*, not which consumer the question is about, and a
    scenario can declare one (`invocation.contract`) without also declaring
    a consumer. `--contract` alone (bare boolean-shaped, no value) never
    happens on the real CLI — it always takes a mode — so a bare flag with
    nothing following it is not matched, matching the false-negative
    default this module uses throughout.
    """
    argv = call.get("argv", [])
    for index, token in enumerate(argv):
        if token == "--contract" and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith("--contract="):
            return token[len("--contract=") :]
    return None


def strongest_reported_verdict(
    run_dir: Path, calls: list[dict], *, only_scoped: bool = False
) -> str | None:
    """The most severe verdict any real comparison in this run produced.

    `only_scoped` restricts the reckoning to consumer-scoped calls
    (`is_consumer_scoped`) — for a claim that answers a scoped question, an
    earlier *unscoped* comparison of the same pair is not a milder report of
    the same fact to be held against it, it is the answer to a different
    question (`shared/consumer-scoping.md`: "Neither direction is a filtered
    view of the other"). Suppression-style gaming *within* the scoped calls
    themselves — running two differently-scoped calls and citing the milder
    one — is unaffected: this only drops unscoped calls from consideration,
    never a scoped one.
    """
    severest: int | None = None
    for call in calls:
        if not ran_to_a_verdict(call):
            continue
        if only_scoped and not is_consumer_scoped(call):
            continue
        verdict = reported_verdict(run_dir, call)
        if verdict is None:
            continue
        index = VERDICT_ORDER.index(verdict)
        severest = index if severest is None else max(severest, index)
    return None if severest is None else VERDICT_ORDER[severest]


def reported_full_verdict(run_dir: Path, call: dict) -> str | None:
    """The library-wide `full_verdict` this call's own JSON report stated.

    JSON only, unlike `reported_verdict` — `full_verdict` has no established
    Markdown "field" text this module already scans for, and inventing a
    regex for one on the strength of a single report shape risks a false
    match on unrelated prose. A JSON-only cited call that carries the field
    is still checked; a Markdown-only one degrades to "nothing to check
    against" rather than guessing — the same false-negative-over-false-
    positive default this module uses throughout.
    """
    for text in _artifact_texts(run_dir, call):
        try:
            parsed: Any = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("full_verdict") in VERDICT_ORDER:
            value: str = parsed["full_verdict"]
            return value
    return None


def reported_full_verdicts(run_dir: Path, calls: Iterable[dict]) -> frozenset[str]:
    """Every distinct `full_verdict` these calls' own reports stated.

    Deliberately takes whatever iterable of calls the caller already
    resolved as *cited* (`_cited(calls, claim)`'s `resolved.values()`) —
    `full_verdict` is being checked against the specific report(s) a claim
    rested its own `full_verdict` on, not against every report the run
    happened to produce. A caller wanting an *unambiguous* grounding check
    treats more than one distinct value as "can't tell which one the claim
    should match" rather than picking one — the same false-negative-over-
    false-positive default this module uses throughout.
    """
    return frozenset(
        v for c in calls if (v := reported_full_verdict(run_dir, c)) is not None
    )


def reported_contract_coverage_failures(run_dir: Path, call: dict) -> list[Any] | None:
    """This call's own `contract_coverage_failures` ledger, if its report has one.

    JSON only (per ADR-049 Phase 7 / `contract_coverage_exit.py`, only
    `--format json` carries this field at all — markdown/text output never
    does even when `--contract` was genuinely engaged), and distinguishes
    three states a caller must not collapse: the key absent (`None` — this
    report can't be used to verify the claim either way, e.g. because it
    wasn't emitted as JSON), the key present and empty (`[]` — real,
    positive evidence that the selected domain's coverage was actually
    complete), and the key present and non-empty (real, positive evidence
    of a genuine coverage gap). A `--contract` call's own use of the flag
    (`contract_mode`) is not this: that only proves contract evaluation ran,
    not what its coverage ledger actually says.
    """
    for text in _artifact_texts(run_dir, call):
        try:
            parsed: Any = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "contract_coverage_failures" in parsed:
            failures = parsed["contract_coverage_failures"]
            if isinstance(failures, list):
                return failures
    return None
