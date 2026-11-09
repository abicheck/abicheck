"""Expectations as a function of configuration, and the three-signal reconciliation.

An operator's ground truth is an ``Expectation`` derived from the edit's
meaning under C/C++ ABI rules for one ``Config``: the base expectation, then
the first matching ``Rule`` (e.g. "enum append is compatible unless
-fshort-enums crosses the underlying size"), then the evidence rule (an edit
the evidence abicheck was given cannot show is *unobservable*: nothing is
required, the benign ceiling still holds).

``reconcile`` puts three independent signals side by side:

1. ``ExecTruth``: what compiled consumers actually did (execution oracle);
2. the ``Expectation`` for the config;
3. abicheck's verdict and ``ChangeKind`` set,

and classifies the cell. Classes, most severe first:

- ``oracle-conflict``: the expectation says benign but execution broke. The
  *ground truth* is wrong; a test bug, never an allowed xfail.
- ``false-clean``: execution broke (binary, or the consumer no longer
  compiles), the evidence can show it, and abicheck reads compatible.
- ``false-positive``: abicheck is stronger than the benign ceiling and
  execution ran and shows nothing (without execution it is ``wrong-verdict``).
- ``wrong-verdict``: any other verdict outside the expected bounds.
- ``wrong-kind``: the verdict is right, an expected kind is missing.
- ``refused``: abicheck declined to compare (e.g. a profile mismatch). Not
  clean and not a pass: the run proved nothing about this edit.

Non-failing classes: ``agree``, ``non-observable`` (abicheck and the
expectation say break, execution on this platform shows none: e.g. int ->
long long in an x86-64 register) and ``unobservable`` (execution broke, the
given evidence cannot show it, abicheck stayed clean).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from tests._edit_corpus_config import EVIDENCE_RANK, Config

# Verdict names in increasing severity (see abicheck.checker_types.Verdict).
VERDICT_RANK: dict[str, int] = {
    "NO_CHANGE": 0,
    "COMPATIBLE": 1,
    "COMPATIBLE_WITH_RISK": 2,
    "API_BREAK": 3,
    "BREAKING": 4,
}

MISMATCH_CLASSES = (
    "oracle-conflict",
    "false-clean",
    "false-positive",
    "wrong-verdict",
    "wrong-kind",
    "refused",
)
PASSING_CLASSES = ("agree", "non-observable", "unobservable")


@dataclass(frozen=True)
class Expectation:
    expected: frozenset[str] = frozenset()
    any_of: tuple[frozenset[str], ...] = ()
    verdict_min: str = "BREAKING"
    verdict_max: str | None = None
    observable: bool = True
    note: str = ""

    @property
    def benign(self) -> bool:
        """The edit is declared not to break consumers' binaries."""
        return (
            self.verdict_max is not None
            and VERDICT_RANK[self.verdict_max] <= VERDICT_RANK["COMPATIBLE_WITH_RISK"]
        )

    @property
    def source_compatible(self) -> bool:
        """The edit is declared not to break consumers' sources either."""
        return (
            self.verdict_max is not None
            and VERDICT_RANK[self.verdict_max] < VERDICT_RANK["API_BREAK"]
        )

    def problems(self, verdict: str, kinds: frozenset[str]) -> list[str]:
        """How an abicheck answer departs from this expectation."""
        if verdict == "REFUSED":
            return [f"abicheck refused to compare ({', '.join(sorted(kinds))})"]
        out: list[str] = []
        missing = sorted(self.expected - kinds)
        if missing:
            out.append(f"missing expected kinds {missing}")
        for group in self.any_of:
            if not (group & kinds):
                out.append(f"none of {sorted(group)} reported")
        rank = VERDICT_RANK[verdict]
        if rank < VERDICT_RANK[self.verdict_min]:
            out.append(f"verdict {verdict} weaker than required {self.verdict_min}")
        if self.verdict_max is not None and rank > VERDICT_RANK[self.verdict_max]:
            out.append(
                f"verdict {verdict} stronger than benign maximum {self.verdict_max}"
            )
        return out


@dataclass(frozen=True)
class Rule:
    """When ``when(config)`` holds, the expectation becomes ``base`` with ``changes``."""

    when: Callable[[Config], bool]
    changes: dict[str, object]
    why: str
    # Overrides the operator's evidence floor while this rule holds.
    min_evidence: str | None = None

    def apply(self, base: Expectation) -> Expectation:
        return replace(base, note=self.why, **self.changes)  # type: ignore[arg-type]


def evidence_floor(exp: Expectation, config: Config, min_evidence: str) -> Expectation:
    """Below *min_evidence* the edit is invisible: require nothing, keep the ceiling."""
    if EVIDENCE_RANK[config.evidence] >= EVIDENCE_RANK[min_evidence]:
        return exp
    return replace(
        exp,
        expected=frozenset(),
        any_of=(),
        verdict_min="NO_CHANGE",
        observable=False,
        note=f"{config.evidence} evidence cannot show this edit (needs {min_evidence})",
    )


@dataclass(frozen=True)
class ExecTruth:
    """What real consumers did. ``available=False`` means no signal, not 'no break'."""

    available: bool
    binary_break: bool = False
    source_break: bool = False
    signals: tuple[str, ...] = ()
    reason: str = ""
    # The binary break rests on the layout probe alone (sizeof/offsetof/enum
    # values differ) with no consumer run diverging: a compiler-measured
    # surrogate for a break, not an observed one.
    layout_only: bool = False

    def describe_break(self) -> str:
        what = "; ".join(self.signals)
        if self.layout_only:
            return f"layout probe only (surrogate, no consumer run diverged): {what}"
        return f"consumers broke: {what}"


@dataclass(frozen=True)
class Reconciled:
    cls: str
    problems: tuple[str, ...]
    detail: str = field(default="")

    @property
    def is_mismatch(self) -> bool:
        return self.cls in MISMATCH_CLASSES


def reconcile(
    exec_truth: ExecTruth, exp: Expectation, verdict: str, kinds: frozenset[str]
) -> Reconciled:
    if verdict == "REFUSED":
        return Reconciled("refused", tuple(exp.problems(verdict, kinds)))
    problems = tuple(exp.problems(verdict, kinds))
    rank = VERDICT_RANK[verdict]
    broke = exec_truth.available and (
        exec_truth.binary_break or exec_truth.source_break
    )
    if exec_truth.available and (
        (exp.benign and exec_truth.binary_break)
        or (exp.source_compatible and exec_truth.source_break)
    ):
        return Reconciled(
            "oracle-conflict",
            problems,
            "the expectation declares this edit harmless, yet "
            + exec_truth.describe_break(),
        )
    if broke:
        clean = (
            rank <= VERDICT_RANK["COMPATIBLE"]
            if exec_truth.binary_break
            else rank < VERDICT_RANK["API_BREAK"]
        )
        if clean:
            if not exp.observable:
                return Reconciled("unobservable", problems, exp.note)
            return Reconciled("false-clean", problems, exec_truth.describe_break())
    if any("stronger than benign" in p for p in problems):
        # A false-positive claims consumers are fine: that needs execution.
        if exec_truth.available and not broke:
            return Reconciled("false-positive", problems)
        return Reconciled("wrong-verdict", problems)
    if any(p.startswith("verdict") for p in problems):
        return Reconciled("wrong-verdict", problems)
    if problems:
        return Reconciled("wrong-kind", problems)
    if (
        exec_truth.available
        and not broke
        and exp.observable
        and VERDICT_RANK[exp.verdict_min] >= VERDICT_RANK["API_BREAK"]
        and rank >= VERDICT_RANK["API_BREAK"]
    ):
        return Reconciled(
            "non-observable", problems, "no consumer shape observed the break here"
        )
    return Reconciled("agree", problems)


def issue_keys(problems: tuple[str, ...] | list[str]) -> frozenset[str]:
    """Normalize ``Expectation.problems`` into comparable keys.

    ``missing:KIND`` per missing expected kind, ``none-of:A|B`` per unmet
    any-of group, ``weaker-than:V`` / ``stronger-than:V`` for a verdict outside
    its bound, ``refused:Error`` for a refusal. A recorded mismatch is the same
    finding only if these keys match exactly.
    """
    keys: set[str] = set()
    for p in problems:
        if m := re.fullmatch(r"missing expected kinds \[(.*)\]", p):
            keys |= {"missing:" + k.strip("' ") for k in m.group(1).split(",")}
        elif m := re.fullmatch(r"none of \[(.*)\] reported", p):
            keys.add(
                "none-of:"
                + "|".join(sorted(k.strip("' ") for k in m.group(1).split(",")))
            )
        elif m := re.fullmatch(r"verdict \w+ weaker than required (\w+)", p):
            keys.add(f"weaker-than:{m.group(1)}")
        elif m := re.fullmatch(r"verdict \w+ stronger than benign maximum (\w+)", p):
            keys.add(f"stronger-than:{m.group(1)}")
        elif m := re.fullmatch(r"abicheck refused to compare \((.*)\)", p):
            keys |= {f"refused:{e.strip()}" for e in m.group(1).split(",")}
        else:
            raise ValueError(f"unrecognized problem text: {p!r}")
    return frozenset(keys)
