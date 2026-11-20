"""Score one tool run against the independent expectation (``competitor_truth``).

Two measurements, kept apart:

* **decision**: the tool's own answer mapped to a maintainer decision
  (``NO_CHANGE`` < ``COMPATIBLE`` < ``ADVISORY`` < ``API_BREAK`` <
  ``BREAKING``), then an *outcome* against the expected verdict bounds:
  ``correct``, ``wrong``, ``advisory`` (a change shown, no incompatibility
  claimed, where a break was expected), ``no_signal`` (nothing reported where
  a change was expected), ``unsupported`` (the mode cannot take this input or
  express this decision), ``error``, ``timeout``.
* **details**: per claim (entity, fields, size, offsets, functions) ``match``,
  ``miss``, ``unsupported`` (the output format cannot express it) or ``n/a``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from abiforge.checkers import abicc, abicheck_cli, abidiff
from abiforge.checkers.tools import ToolRun
from abiforge.model.expect import VERDICT_RANK, Expectation
from abiforge.run.competitor_truth import DetailTruth

DECISION_RANK = {
    "NO_CHANGE": 0,
    "COMPATIBLE": 1,
    "ADVISORY": 2,
    "API_BREAK": 3,
    "BREAKING": 4,
}
OUTCOMES = (
    "correct",
    "wrong",
    "advisory",
    "no_signal",
    "unsupported",
    "error",
    "timeout",
)
CLAIMS = ("entity", "fields", "size", "offsets", "functions")
#: Claims a mode's output format can express (others are ``unsupported``).
SUPPORTS = {
    "abicheck": set(CLAIMS),
    "abidiff": set(CLAIMS),
    "abicc-dump": {"entity", "fields", "size", "functions"},
    "abicc-xml": {"entity", "fields", "size", "functions"},
}
#: Modes that have a source-compatibility answer at all.
SOURCE_AWARE = {"abicheck", "abicc-dump", "abicc-xml"}


@dataclass
class Observed:
    """What a report said, reduced to the common vocabulary."""

    decision: str | None = None
    status: str = "ok"  # ok | error | timeout | unsupported | unavailable
    reason: str = ""
    entities: set[str] = field(default_factory=set)
    fields: dict[str, set[str]] = field(default_factory=dict)  # entity -> members
    sizes: dict[str, tuple[int, int]] = field(default_factory=dict)
    moved: dict[str, set[str]] = field(default_factory=dict)
    functions: set[str] = field(default_factory=set)
    sides: dict[str, str] = field(default_factory=dict)  # ABICC binary/source decisions
    status_bits: dict[str, bool] = field(default_factory=dict)
    excerpt: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        return {
            "decision": self.decision,
            "status": self.status,
            "reason": self.reason,
            "entities": sorted(self.entities),
            "fields": {k: sorted(v) for k, v in sorted(self.fields.items())},
            "sizes": {k: list(v) for k, v in sorted(self.sizes.items())},
            "moved": {k: sorted(v) for k, v in sorted(self.moved.items())},
            "functions": sorted(self.functions),
            "sides": self.sides,
            "status_bits": self.status_bits,
            "excerpt": self.excerpt[:12],
        }


def _failed(tr: ToolRun, ok_exits: set[int]) -> Observed | None:
    if tr.unavailable:
        return Observed(status="unavailable", reason=tr.unavailable)
    if tr.timed_out:
        return Observed(status="timeout", reason=" ".join(tr.steps[-1].argv[:2]))
    bad = next((s for s in tr.steps if s.exit not in ok_exits), None)
    if bad is not None or "report" not in tr.reports:
        s = bad or tr.steps[-1]
        return Observed(
            status="error", reason=f"exit {s.exit}: {s.stderr_tail.strip()[-300:]}"
        )
    return None


def observe_abicheck(tr: ToolRun, d: Path) -> Observed:
    if f := _failed(tr, set(abicheck_cli.ANSWER_EXITS)):
        return f
    rep = abicheck_cli.parse((d / tr.reports["report"]).read_text(encoding="utf-8"))
    ob = Observed(decision=_verdict_decision(rep["verdict"]))
    for c in rep["changes"]:
        kind, sym = c["kind"], c.get("symbol") or ""
        ent, _, member = sym.partition("::")
        # The typed field_name first; a finding that has none still names its
        # member in the description a maintainer reads ("Field added: rec::extra").
        named = (
            re.search(rf"\b{re.escape(ent)}::(\w+)", c.get("description") or "")
            if ent
            else None
        )
        member = c.get("field_name") or member or (named.group(1) if named else "")
        ob.entities.add(ent)
        ob.functions |= set(c.get("affected_symbols") or ())
        if c.get("entity") in ("function", "variable"):
            ob.functions.add(ent)
        if member and ("field" in kind or "member" in kind):
            ob.fields.setdefault(ent, set()).add(member)
        if kind in ("type_size_changed", "struct_size_changed") and c.get("old_value"):
            ob.sizes[ent] = (int(c["old_value"]) // 8, int(c["new_value"]) // 8)
        if kind.endswith("field_offset_changed") and member:
            ob.moved.setdefault(ent, set()).add(member)
        ob.excerpt.append(
            f"{c.get('severity')}: {kind} {sym}: {c.get('description', '')}"[:200]
        )
    return ob


def _verdict_decision(v: str) -> str:
    return {"COMPATIBLE_WITH_RISK": "ADVISORY"}.get(v, v)


def observe_abidiff(tr: ToolRun, d: Path) -> Observed:
    if f := _failed(tr, {0, 4, 8, 12}):
        return f
    code = tr.steps[-1].exit
    text = (d / tr.reports["report"]).read_text(encoding="utf-8")
    rep = abidiff.parse(text)
    ob = Observed(
        decision=abidiff.decision(code, rep), status_bits=abidiff.decode_status(code)
    )
    ob.functions = rep.changed_functions | rep.removed_functions | rep.added_functions
    ob.entities = (
        set(ob.functions)
        | rep.changed_variables
        | rep.removed_variables
        | rep.added_variables
        | set(rep.types)
    )
    for name, t in rep.types.items():
        ob.fields[name] = t.added | t.removed | t.retyped
        if t.size:
            ob.sizes[name] = (t.size[0] // 8, t.size[1] // 8)
        if t.moved:
            ob.moved[name] = set(t.moved)
    ob.excerpt = [line.rstrip() for line in text.splitlines() if line.strip()][:12]
    return ob


def observe_abicc(tr: ToolRun, d: Path) -> Observed:
    if f := _failed(tr, {0, 1}):
        return f
    sides = abicc.parse((d / tr.reports["report"]).read_text(encoding="utf-8"))
    ob = Observed(decision=abicc.decision(sides))
    for kind, side in sides.items():
        ob.sides[kind] = abicc.side_decision(side)
        names = side.added_symbols | side.removed_symbols | set(side.symbol_problems)
        ob.functions |= names
        ob.entities |= names | set(side.types)
        for tname, tp in side.types.items():
            ob.functions |= tp.affected
            ob.fields.setdefault(tname, set()).update(
                tp.targets(
                    "Added_",
                    "Removed_",
                    "Field_",
                    "Renamed_Field",
                    "Moved_Field",
                    "Struct_",
                    "Union_",
                )
            )
            if tp.size:
                ob.sizes[tname] = (tp.size[0] // 8, tp.size[1] // 8)
            ob.excerpt += [
                f"{kind}/{sev}: {tname} {pid} {t}" for sev, pid, t in tp.problems
            ]
        ob.excerpt += [f"{kind}: removed {s}" for s in sorted(side.removed_symbols)]
        ob.excerpt += [f"{kind}: added {s}" for s in sorted(side.added_symbols)]
        ob.excerpt += [
            f"{kind}/{sev}: {s} {pid}"
            for s, ps in side.symbol_problems.items()
            for sev, pid in ps
        ]
    return ob


OBSERVERS = {
    "abicheck": observe_abicheck,
    "abidiff": observe_abidiff,
    "abicc-dump": observe_abicc,
    "abicc-xml": observe_abicc,
}


def decision_outcome(mode: str, ob: Observed, exp: Expectation) -> str:
    """The decision outcome of one run against the expected verdict bounds."""
    if ob.status in ("error", "unavailable"):
        return "error" if ob.status == "error" else "unsupported"
    if ob.status in ("timeout", "unsupported"):
        return ob.status
    dec = ob.decision or "NO_CHANGE"
    rank = (
        VERDICT_RANK["COMPATIBLE_WITH_RISK"] if dec == "ADVISORY" else VERDICT_RANK[dec]
    )
    lo = VERDICT_RANK[exp.verdict_min]
    hi = VERDICT_RANK[exp.verdict_max] if exp.verdict_max else VERDICT_RANK["BREAKING"]
    if lo <= rank <= hi:
        return "correct"
    source_only = exp.verdict_min == "API_BREAK" and exp.verdict_max == "API_BREAK"
    if source_only and mode not in SOURCE_AWARE and dec != "BREAKING":
        return "unsupported"
    if dec == "NO_CHANGE" and lo > 0:
        return "no_signal"
    if dec in ("ADVISORY", "COMPATIBLE") and lo >= VERDICT_RANK["API_BREAK"]:
        return "advisory"
    return "wrong"


def detail_outcomes(mode: str, ob: Observed, dt: DetailTruth) -> dict[str, str]:
    out: dict[str, str] = {}
    failed = ob.status != "ok"
    rec = dt.record
    expect = {
        "entity": bool(dt.entities),
        "fields": bool(dt.fields),
        # Positive facts only: a tool that saw nothing must not score on an
        # absent size change or an empty set of moved offsets.
        "size": bool(dt.size_changed),
        "offsets": bool(dt.offsets_moved),
        "functions": bool(dt.functions),
    }
    for claim in CLAIMS:
        if not expect[claim]:
            out[claim] = "n/a"
        elif claim not in SUPPORTS[mode]:
            out[claim] = "unsupported"
        elif failed:
            out[claim] = ob.status
        elif claim == "entity":
            out[claim] = _m(dt.entities <= ob.entities)
        elif claim == "fields":
            out[claim] = _m(dt.fields <= ob.fields.get(rec or "", set()))
        elif claim == "size":
            got = ob.sizes.get(rec or "")
            out[claim] = _m(got == dt.size)
        elif claim == "offsets":
            out[claim] = _m(ob.moved.get(rec or "", set()) == dt.offsets_moved)
        else:
            out[claim] = _m(dt.functions <= ob.functions)
    return out


def _m(ok: bool) -> str:
    return "match" if ok else "miss"


def strip_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()
