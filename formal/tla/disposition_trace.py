"""Record real disposition-pipeline runs as abstract DispositionPipeline traces.

E11 (trace validation). ``DispositionPipeline.tla`` was until now checked
only against a hand transcription of itself (``check_disposition.py``); this
module ties it to the implementation. While :func:`recording` is active,
every ``DispositionPipeline.run`` call is captured as a sequence of abstract
states, one per step boundary, in the spec's own variables:

* ``changes`` -- the ids of the finding objects in the step's output list;
* ``buckets`` -- the ids in ``suppressed``/``redundant``/``opaque_filtered``/
  ``out_of_surface``;
* ``ledger`` -- the ids the disposition ledger holds a record for;
* ``seen`` -- every id that has appeared in any of the three.

A finding's abstract id is its position in first-appearance order within the
run (object identity, held alive for the run so ``id()`` is never reused).

The recorder only observes: it wraps each step in a delegating proxy and
reads the context after ``_record_dropped_duplicates`` has run, so the
pipeline's own behavior is unchanged. ``check_trace.py`` checks the recorded
traces against the spec (Python and TLC).
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from abicheck.policy.finding_disposition import pipeline as pl

BUCKETS = ("suppressed", "redundant", "opaque_filtered", "out_of_surface")


@dataclass
class _Run:
    """One ``DispositionPipeline.run`` call being recorded."""

    label: str
    ids: dict[int, int] = field(default_factory=dict)
    anchors: list[Any] = field(default_factory=list)  # keeps id() unique
    states: list[dict[str, Any]] = field(default_factory=list)

    def abstract(self, objs: Any) -> list[int]:
        out = []
        for o in objs:
            key = id(o)
            if key not in self.ids:
                self.ids[key] = len(self.ids) + 1
                self.anchors.append(o)
            out.append(self.ids[key])
        return out

    def snap(self, step: str, changes: list[Any], ctx: Any) -> None:
        ledger = ctx.disposition_ledger
        ledger_objs = list(getattr(ledger, "_anchors", ())) if ledger else []
        ch = self.abstract(changes)
        bk = self.abstract(o for b in BUCKETS for o in getattr(ctx, b))
        lg = self.abstract(ledger_objs)
        prev_seen = set(self.states[-1]["seen"]) if self.states else set()
        self.states.append(
            {
                "step": step,
                "changes": sorted(set(ch)),
                "duplicates": len(ch) - len(set(ch)),
                "buckets": sorted(set(bk)),
                "ledger": sorted(set(lg)),
                "seen": sorted(prev_seen | set(ch) | set(bk) | set(lg)),
            }
        )


@dataclass
class Recorder:
    """Collects one trace per pipeline run while :func:`recording` is active."""

    label: str = ""
    traces: list[dict[str, Any]] = field(default_factory=list)
    _current: list[_Run] = field(default_factory=list)

    def dump(self, path: Path) -> None:
        path.write_text(json.dumps(self.traces, indent=1) + "\n")


class _StepProxy:
    """Delegates everything to the real step; snapshots before its first run."""

    def __init__(self, step: Any, rec: Recorder) -> None:
        self._step = step
        self._rec = rec

    def __getattr__(self, name: str) -> Any:
        return getattr(self._step, name)

    def run(self, changes: list[Any], ctx: Any) -> list[Any]:
        run = self._rec._current[-1]
        if not run.states:
            run.snap("<init>", changes, ctx)
        return self._step.run(changes, ctx)


@contextlib.contextmanager
def recording(label: str = "") -> Iterator[Recorder]:
    """Record every disposition pipeline run inside the block."""
    rec = Recorder(label=label)
    orig_run = pl.DispositionPipeline.run
    orig_record = pl._record_dropped_duplicates

    def run(self: Any, changes: list[Any], *args: Any, **kwargs: Any) -> Any:
        rec._current.append(_Run(label=rec.label))
        real_steps = self.steps
        self.steps = [_StepProxy(s, rec) for s in real_steps]
        try:
            ctx = orig_run(self, changes, *args, **kwargs)
        finally:
            self.steps = real_steps
            run_ = rec._current.pop()
        if run_.states:
            rec.traces.append({"label": run_.label, "states": run_.states})
        return ctx

    def record(
        before: Any,
        after: Any,
        bucket_total_before: int,
        ctx: Any,
        step_name: str,
        disposition: Any = pl.DEFAULT_DROPPED_DISPOSITION,
    ) -> None:
        orig_record(before, after, bucket_total_before, ctx, step_name, disposition)
        if rec._current:
            rec._current[-1].snap(step_name, after, ctx)

    pl.DispositionPipeline.run = run  # type: ignore[method-assign]
    pl._record_dropped_duplicates = record
    try:
        yield rec
    finally:
        pl.DispositionPipeline.run = orig_run  # type: ignore[method-assign]
        pl._record_dropped_duplicates = orig_record
