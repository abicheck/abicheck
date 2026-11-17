#!/usr/bin/env python3
"""Check recorded disposition traces against DispositionPipeline.tla (E11).

A trace (from ``trace_corpus.py``) is a sequence of abstract states, one per
pipeline step boundary. Each consecutive pair must be one ``StepWith(D, M, A,
B, R)`` of the spec, with the witnesses read off the two states:

* ``M`` = findings that left ``changes`` into a bucket;
* ``D`` = findings that left ``changes`` and reached no bucket;
* ``A`` = new ids that appeared in ``changes``;
* ``B`` = new ids that appeared only in a bucket;
* ``R`` = ids the step recorded in the ledger itself (everything the ledger
  gained beyond what the count trigger records).

The transition is accepted only if the spec's enabling conditions hold for
those witnesses and the successor state equals the spec's. Every state must
also satisfy ``NothingVanishes``. ``--tlc JAR`` additionally hands each trace
to TLC as ``DispositionTrace.tla`` (generated), which accepts it only by
reaching its last state; any deadlock or invariant violation is a rejection.

Exit 0 when every trace is accepted, 1 otherwise, 2 when there is nothing to
check.

    python3 -I formal/tla/check_trace.py traces.json [--tlc tla2tools.jar] [--tlc-sample N]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

State = dict[str, Any]


def witnesses(s0: State, s1: State) -> dict[str, set[int]]:
    """The StepWith arguments that turn *s0* into *s1*, read off the states."""
    ch0, b0, l0, seen0 = (set(s0[k]) for k in ("changes", "buckets", "ledger", "seen"))
    ch1, b1, l1 = (set(s1[k]) for k in ("changes", "buckets", "ledger"))
    left = ch0 - ch1
    M = left & b1
    D = left - b1
    A = ch1 - ch0
    B = b1 - b0 - ch0
    R = l1 - l0
    if trigger(ch0, D, M, A, B):
        R -= D
    return {"D": D, "M": M, "A": A, "B": B, "R": R, "seen0": seen0}


def trigger(ch0: set[int], D: set[int], M: set[int], A: set[int], B: set[int]) -> bool:
    after = (ch0 - (D | M)) | A
    return len(after) + len(M | B) < len(ch0)


def step_problems(s0: State, s1: State, self_record: bool) -> list[str]:
    """Why *s0* -> *s1* is not a StepWith of the spec (empty when it is)."""
    w = witnesses(s0, s1)
    D, M, A, B, R, seen0 = w["D"], w["M"], w["A"], w["B"], w["R"], w["seen0"]
    ch0, b0, l0 = (set(s0[k]) for k in ("changes", "buckets", "ledger"))
    out = []
    if s1["duplicates"]:
        out.append(f"changes holds {s1['duplicates']} duplicate object(s)")
    if (A | B) & seen0:
        out.append(f"re-introduced an already seen id {sorted((A | B) & seen0)}")
    if R and not self_record:
        out.append(f"ledger gained {sorted(R)} outside the count trigger")
    fresh_r = R - seen0 - A - B
    if fresh_r and not self_record:
        out.append(f"recorded never-visible ids {sorted(fresh_r)}")
    # The spec's successor for these witnesses must equal the observed state.
    exp_changes = (ch0 - (D | M)) | A
    exp_buckets = b0 | M | B
    exp_ledger = l0 | R | (D if trigger(ch0, D, M, A, B) else set())
    if exp_changes != set(s1["changes"]):
        out.append("changes' differs from the spec's successor")
    if exp_buckets != set(s1["buckets"]):
        gone = sorted(b0 - set(s1["buckets"]))
        out.append(f"buckets lost {gone}" if gone else "buckets' differs")
    if exp_ledger != set(s1["ledger"]):
        out.append(f"ledger lost {sorted(l0 - set(s1['ledger']))}")
    return out


def vanished(s: State) -> set[int]:
    return set(s["seen"]) - set(s["changes"]) - set(s["buckets"]) - set(s["ledger"])


def check(
    traces: list[dict[str, Any]], self_record: bool
) -> tuple[Counter[str], list[str]]:
    counts: Counter[str] = Counter()
    examples: list[str] = []
    for t in traces:
        states = t["states"]
        counts["traces"] += 1
        counts["transitions"] += len(states) - 1
        bad = False
        for i, (s0, s1) in enumerate(zip(states, states[1:])):
            w = witnesses(s0, s1)
            if w["D"] and (w["A"] or w["B"]):
                counts["mixed_drop_and_add"] += 1
            if w["R"]:
                counts["self_recording_steps"] += 1
            for p in step_problems(s0, s1, self_record):
                bad = True
                key = f"{s1['step']}: {p.split(' [')[0].split(' {')[0]}"
                counts[f"reject {key}"] += 1
                if len(examples) < 20:
                    examples.append(f"{t['label']} #{i + 1} {s1['step']}: {p}")
        for s in states:
            if vanished(s):
                bad = True
                counts["VANISHED"] += 1
                examples.append(
                    f"{t['label']} {s['step']}: vanished {sorted(vanished(s))}"
                )
                break
        counts["rejected_traces" if bad else "accepted_traces"] += 1
    return counts, examples


# --- seeded-wrong traces ----------------------------------------------------


def _copy(t: dict[str, Any], label: str) -> dict[str, Any]:
    return {
        "label": f"{t['label']} [{label}]",
        "states": json.loads(json.dumps(t["states"])),
    }


def mutants(t: dict[str, Any]) -> list[dict[str, Any]]:
    """Wrong versions of *t* that the spec must reject, one per defect shape.

    * ``unrecorded_drop`` -- a step drops findings and the ledger never gets
      them (the defect ADR-067 D1 forbids);
    * ``count_masked`` -- the E7 counterexample: the same drop, plus a new
      finding added in the same call so the count trigger does not fire;
    * ``ledger_lost`` -- a recorded finding later disappears from the ledger;
    * ``resurrected`` -- a dropped finding object reappears in ``changes``.

    Mutants exist only where *t* has a drop that the trigger recorded.
    """
    states = t["states"]
    k = next(
        (
            i
            for i in range(1, len(states))
            if witnesses(states[i - 1], states[i])["D"]
            and set(witnesses(states[i - 1], states[i])["D"])
            <= set(states[i]["ledger"])
        ),
        None,
    )
    if k is None:
        return []
    dropped = set(witnesses(states[k - 1], states[k])["D"])
    out = []
    m = _copy(t, "unrecorded_drop")
    for s in m["states"][k:]:
        s["ledger"] = sorted(set(s["ledger"]) - dropped)
    out.append(m)
    m = _copy(t, "count_masked")
    new_ids = list(range(10_000, 10_000 + len(dropped)))
    for s in m["states"][k:]:
        s["ledger"] = sorted(set(s["ledger"]) - dropped)
        s["changes"] = sorted(set(s["changes"]) | set(new_ids))
        s["seen"] = sorted(set(s["seen"]) | set(new_ids))
    out.append(m)
    if k + 1 < len(states):
        m = _copy(t, "ledger_lost")
        for s in m["states"][k + 1 :]:
            s["ledger"] = sorted(set(s["ledger"]) - dropped)
        out.append(m)
        m = _copy(t, "resurrected")
        for s in m["states"][k + 1 :]:
            s["changes"] = sorted(set(s["changes"]) | dropped)
        out.append(m)
    return out


# --- TLC -------------------------------------------------------------------


def _tla_set(xs: list[int]) -> str:
    return "{" + ", ".join(map(str, xs)) + "}"


def trace_module(t: dict[str, Any]) -> str:
    states = t["states"]
    ids = sorted({i for s in states for i in s["seen"]})
    rows = []
    for s in states:
        seen = s["seen"]
        rows.append(
            "[changes |-> {c}, buckets |-> {b}, ledger |-> {l}, seen |-> {s}, fresh |-> {f}]".format(
                c=_tla_set(s["changes"]),
                b=_tla_set(s["buckets"]),
                l=_tla_set(s["ledger"]),
                s=_tla_set(seen),
                f=_tla_set(sorted(set(ids) - set(seen))),
            )
        )
    return (
        "---- MODULE TraceData ----\nEXTENDS Naturals\n"
        f"TraceIds == {_tla_set(ids) if ids else '{}'}\n"
        f"TraceSteps == {len(states) - 1}\n"
        "Trace == <<\n  " + ",\n  ".join(rows) + "\n>>\n====\n"
    )


def run_tlc(jar: Path, traces: list[dict[str, Any]]) -> tuple[int, list[str]]:
    java = shutil.which("java")
    if not java:
        raise SystemExit("java not found")
    bad: list[str] = []
    for t in traces:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name in (
                "DispositionPipeline.tla",
                "DispositionTrace.tla",
                "DispositionTrace.cfg",
            ):
                shutil.copy(HERE / name, d / name)
            (d / "TraceData.tla").write_text(trace_module(t))
            r = subprocess.run(
                [
                    java,
                    "-XX:+UseParallelGC",
                    "-cp",
                    str(jar),
                    "tlc2.TLC",
                    "-workers",
                    "1",
                    "-config",
                    "DispositionTrace.cfg",
                    "DispositionTrace.tla",
                ],
                cwd=d,
                capture_output=True,
                text=True,
                check=False,
            )
            ok = r.returncode == 0 and "No error has been found" in r.stdout
            if not ok:
                tail = [
                    ln
                    for ln in r.stdout.splitlines()
                    if "Error" in ln or "violated" in ln
                ]
                bad.append(f"{t['label']}: {tail[:3] or r.stdout[-300:]}")
    return len(traces), bad


def self_test(traces: list[dict[str, Any]], jar: Path | None) -> int:
    """Each mutant shape, taken from the first trace that has one, must be
    rejected by the Python checker and (with *jar*) by TLC."""
    by_shape: dict[str, dict[str, Any]] = {}
    for t in traces:
        for m in mutants(t):
            by_shape.setdefault(m["label"].rsplit("[", 1)[1].rstrip("]"), m)
    if not by_shape:
        print("no trace has a recorded drop: nothing to mutate")
        return 2
    rc = 0
    for shape, m in sorted(by_shape.items()):
        counts, _ = check([m], self_record=True)
        py_ok = counts["rejected_traces"] == 1
        tlc_ok = None
        if jar:
            _, bad = run_tlc(jar, [m])
            tlc_ok = len(bad) == 1
        print(
            f"mutant {shape}: python {'rejected' if py_ok else 'ACCEPTED'}"
            + (
                ""
                if tlc_ok is None
                else f", tlc {'rejected' if tlc_ok else 'ACCEPTED'}"
            )
        )
        rc |= 0 if py_ok and tlc_ok is not False else 1
    missing = {"unrecorded_drop", "count_masked", "ledger_lost", "resurrected"} - set(
        by_shape
    )
    if missing:
        print(f"mutant shapes not produced: {sorted(missing)}")
        rc = 1
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("traces", type=Path, nargs="+")
    ap.add_argument(
        "--tlc", type=Path, help="tla2tools.jar: also check traces with TLC"
    )
    ap.add_argument(
        "--tlc-sample",
        type=int,
        default=0,
        help="TLC on the first N distinct traces only",
    )
    ap.add_argument(
        "--strict-v1",
        action="store_true",
        help="reject self-recording steps (the spec before E11 had no R)",
    )
    ap.add_argument(
        "--mutants",
        action="store_true",
        help="self-test: every seeded-wrong trace must be rejected by both checkers",
    )
    a = ap.parse_args(argv)
    if a.mutants:
        return self_test(
            [t for p in a.traces for t in json.loads(p.read_text())], a.tlc
        )
    traces = [t for p in a.traces for t in json.loads(p.read_text())]
    if not traces:
        print("no traces: nothing was checked")
        return 2
    counts, examples = check(traces, self_record=not a.strict_v1)
    for k in sorted(counts):
        print(f"{k}: {counts[k]}")
    for e in examples:
        print("  ", e)
    rc = 0 if counts["rejected_traces"] == 0 else 1
    if a.tlc:
        # Distinct abstract shapes only: the label is not part of the state.
        uniq: dict[str, dict[str, Any]] = {}
        for t in traces:
            uniq.setdefault(json.dumps(t["states"], sort_keys=True), t)
        sample = list(uniq.values())
        if a.tlc_sample:
            sample = sample[: a.tlc_sample]
        n, bad = run_tlc(a.tlc, sample)
        print(f"tlc: checked {n} distinct traces, rejected {len(bad)}")
        for b in bad[:10]:
            print("  ", b)
        if bad != [] and rc == 0:
            print("TLC and the Python checker disagree")
        rc = rc or (1 if bad else 0)
    return rc


if __name__ == "__main__":
    sys.exit(main())
