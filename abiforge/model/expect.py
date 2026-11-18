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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from abiforge.checkers.abicheck import observable_at
from abiforge.model.config import Config
from abiforge.model.producer_capabilities import dwarf_witness_present

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
PASSING_CLASSES = ("agree", "non-observable", "unobservable", "evidence-limited")


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
        """The edit is declared not to break consumers' binaries.

        An ``API_BREAK`` ceiling is a source-only break: already-built
        consumers must keep running, so a binary break contradicts it.
        """
        return (
            self.verdict_max is not None
            and VERDICT_RANK[self.verdict_max] <= VERDICT_RANK["API_BREAK"]
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

    def apply(self, base: Expectation) -> Expectation:
        return replace(base, note=self.why, **self.changes)  # type: ignore[arg-type]


#: Language names as ``abicheck``'s observability conditions spell them.
_LANGUAGE_NAME = {"c": "c", "c++": "cxx"}


def kind_observable(kind: str, config: Config) -> bool:
    """Whether *config* observes *kind*: some ``observable_at`` alternative is inside it.

    A kind with no declared ``observable_at`` (UNCLASSIFIED) is taken as
    observable: the edit is never weakened on a missing declaration. Such
    kinds are listed by ``coverage`` as unclassified, not dropped silently.
    """
    spec = observable_at(kind)
    if spec is None:
        return True
    atoms = config.evidence_set.atoms
    language = _LANGUAGE_NAME[config.lang]
    for cond in spec:
        if cond.platform is not None and config.objfmt not in cond.platform:
            continue
        if cond.language is not None and language not in cond.language:
            continue
        if cond.arch is not None and config.arch not in cond.arch:
            continue
        if any(
            alt <= atoms
            and ("D" not in alt or dwarf_witness_present(kind, config.producer))
            for alt in cond.alternatives
        ):
            return True
    return False


@dataclass(frozen=True)
class BinaryLicence:
    """A verdict ceiling an edit may take only when a binary fact is confirmed.

    The fact is one ``abiforge.run.binfacts`` reads from the two built
    libraries (nm/readelf), never from abicheck's answer. While the fact holds,
    the expectation's ``verdict_max`` rises to ``verdict_max`` (the RISK-level
    finding the fact entails is honest); while it does not, the strict
    expectation stands and a finding of that level is a false positive.
    """

    fact: str
    verdict_max: str

    def apply(self, exp: Expectation, facts: Mapping[str, bool]) -> Expectation:
        if not facts.get(self.fact) or not exp.observable:
            return exp
        if exp.verdict_max is not None and (
            VERDICT_RANK[exp.verdict_max] >= VERDICT_RANK[self.verdict_max]
        ):
            return exp
        return replace(
            exp,
            verdict_max=self.verdict_max,
            note=f"{exp.note} [licensed: {self.fact} confirmed by nm]".strip(),
        )


#: A kind that follows from the edit's verdict rather than from its own
#: evidence (``with_derived``): it never decides whether the edit is observable.
ADVISORY_KINDS: frozenset[str] = frozenset({"SONAME_BUMP_RECOMMENDED"})


def observability_floor(
    exp: Expectation, config: Config, carrier: frozenset[str] = frozenset()
) -> Expectation:
    """The expectation the evidence can show, kind by kind.

    The edit's own kinds decide whether it is observable: a kind the
    configuration does not observe is not required, and when no edit kind is
    observed the edit is unobservable (nothing is required, the benign ceiling
    still holds). An advisory kind (``ADVISORY_KINDS``) follows the edit and is
    never the reason an edit is observable. The note says what was dropped.
    """
    edit = (exp.expected | frozenset().union(*exp.any_of)) - ADVISORY_KINDS
    seen = frozenset(k for k in edit if kind_observable(k, config))
    if carrier and not (config.evidence_set.atoms & carrier):
        # A build-flag edit: the flag is recorded only by a carrier atom, so a
        # configuration without one cannot see any of the edit's kinds, even
        # where a header-level kind would otherwise be observable.
        seen = frozenset()
    hidden = edit - seen
    if edit and not seen:
        return replace(
            exp,
            expected=frozenset(),
            any_of=(),
            verdict_min="NO_CHANGE",
            observable=False,
            note=(
                f"{config.evidence_label} evidence cannot show this edit "
                f"(no edit kind is observable: {sorted(hidden)})"
            ),
        )
    if not hidden:
        return exp
    expected = (exp.expected & seen) | (exp.expected & ADVISORY_KINDS)
    any_of = tuple(kept for group in exp.any_of if (kept := group & seen))
    return replace(
        exp,
        expected=expected,
        any_of=any_of,
        note=(
            f"{exp.note} "
            f"[reduced: {sorted(hidden)} not observable at {config.evidence_label}]"
        ).strip(),
    )


def binary_depth_cap(exp: Expectation, config: Config) -> Expectation:
    """ADR-084 at the expectation: an API_BREAK the edit's observed kinds back
    only through binary-producible kinds is COMPATIBLE_WITH_RISK at binary or
    DWARF depth, unless header or source evidence is present.

    Mirrors ``policy.binary_gated_contract``: the verdict cap applies only when
    every observed kind of the edit is ``BINARY_GATED`` and no ``H`` or ``S``
    atom is in the configuration. A header-gated kind keeps its verdict.
    """
    from abiforge.checkers.abicheck import binary_gated_kind_names

    if not exp.observable or exp.verdict_min != "API_BREAK":
        return exp
    if {"H", "S"} & set(config.evidence_set.atoms):
        return exp
    kinds = (exp.expected | frozenset().union(*exp.any_of)) - ADVISORY_KINDS
    if not kinds or not kinds <= binary_gated_kind_names():
        return exp
    return replace(
        exp,
        # The ceiling stays API_BREAK: the source break is real, so the edit is
        # not declared harmless (``benign``); the cap only lowers the floor.
        verdict_min="COMPATIBLE_WITH_RISK",
        verdict_max="API_BREAK",
        note=(
            f"{exp.note} [ADR-084: {config.evidence_label} evidence without headers "
            "or sources cannot back an API_BREAK for binary-gated kinds]"
        ).strip(),
    )


# A (build, consumer) pair's status. Only "ran" is a signal: "infeasible" is a
# mix that cannot work whatever the edit (libstdc++ against libc++, the two
# _GLIBCXX_USE_CXX11_ABI std::strings, a standard the header does not compile
# in), "unavailable" a mix this host cannot build. Neither is ever a pass.
PAIR_STATUSES = ("ran", "infeasible", "unavailable")


@dataclass(frozen=True)
class PairTruth:
    """What one consumer did against one library build."""

    consumer: str  # ResolvedConsumer.id
    status: str  # one of PAIR_STATUSES
    binary_break: bool = False
    source_break: bool = False
    signals: tuple[str, ...] = ()
    reason: str = ""

    @property
    def outcome(self) -> str:
        if self.status != "ran":
            return self.status
        if self.binary_break:
            return "binary-break"
        return "source-break" if self.source_break else "no-break"


@dataclass(frozen=True)
class ExecTruth:
    """What real consumers did. ``available=False`` means no signal, not 'no break'.

    ``binary_break``/``source_break`` hold if *any* pair that ran broke (or
    the build-level layout probe differs): a breaking edit must break at least
    one consumer, a compatible one must break none. ``pairs`` keeps each
    (build, consumer) result, infeasible and unavailable pairs included.
    """

    available: bool
    binary_break: bool = False
    source_break: bool = False
    signals: tuple[str, ...] = ()
    reason: str = ""
    # The binary break rests on the layout probe alone (sizeof/offsetof/enum
    # values differ) with no consumer run diverging: a compiler-measured
    # surrogate for a break, not an observed one.
    layout_only: bool = False
    pairs: tuple[PairTruth, ...] = ()

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
    exec_truth: ExecTruth,
    exp: Expectation,
    verdict: str,
    kinds: frozenset[str],
    *,
    disclosed: frozenset[str] = frozenset(),
    evidence: frozenset[str] | None = None,
) -> Reconciled:
    """Classify one cell against the execution oracle.

    ``disclosed`` are the kinds whose downgrade ADR-084 disclosed in the run;
    ``evidence`` the configuration's atoms (None: unknown, so the ADR-084 rule
    does not apply).
    """
    if verdict == "REFUSED":
        return Reconciled("refused", tuple(exp.problems(verdict, kinds)))
    problems = tuple(exp.problems(verdict, kinds))
    rank = VERDICT_RANK[verdict]
    broke = exec_truth.available and (
        exec_truth.binary_break or exec_truth.source_break
    )
    if (
        evidence is not None
        and not ({"H", "S"} & evidence)
        and exec_truth.available
        and exec_truth.source_break
        and not exec_truth.binary_break
        and verdict == "COMPATIBLE_WITH_RISK"
        and kinds & disclosed
        and not problems
    ):
        # ADR-084 at reconcile: a source-only break that DWARF or binary input
        # cannot back is judged correct only as a disclosed COMPATIBLE_WITH_RISK.
        return Reconciled(
            "evidence-limited",
            problems,
            "source-only break at binary depth: its downgrade is disclosed (ADR-084)",
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
