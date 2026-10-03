# SPDX-License-Identifier: Apache-2.0
"""Ordered, optionally parallel execution and console reporting of example cases.

``validate_examples.py``'s ``--jobs`` support. Cases are independent (each
builds in its own work directory, and the header-AST/snapshot disk caches
write atomically), so they can run in a process pool; the result list keeps
the submission order regardless of completion order, which keeps the JSON
artifact identical to a sequential run's. The human-readable progress and
summary lines live here too; the JSON artifact stays with the validator.
Results are duck-typed ``validate_examples.CaseResult`` values.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from typing import Any, TypeVar

_R = TypeVar("_R")

STATUS_ICONS = {
    "PASS": "✅",
    "FAIL": "❌",
    "XFAIL": "⚠️ ",
    "SKIP": "⏭️ ",
    "ERROR": "\U0001f4a5",
}


def run_ordered(
    fn: Callable[..., _R],
    calls: Sequence[tuple[Any, ...]],
    *,
    jobs: int,
    initializer: Callable[..., None] | None = None,
    initargs: tuple[Any, ...] = (),
) -> Iterator[_R]:
    """Yield ``fn(*args)`` for each entry of *calls*, in *calls* order.

    ``jobs <= 1`` (or a single call) runs inline, so a caller can stop
    consuming early (``--fail-fast``) without starting later cases. Otherwise
    the calls run in a process pool; *initializer* runs in each worker, which
    is how state that is not inherited under the ``spawn``/``forkserver``
    start methods (the Linux default from Python 3.14) reaches it.
    """
    if jobs <= 1 or len(calls) <= 1:
        for args in calls:
            yield fn(*args)
        return
    with ProcessPoolExecutor(
        max_workers=min(jobs, len(calls)),
        initializer=initializer,
        initargs=initargs,
    ) as pool:
        futures = [pool.submit(fn, *args) for args in calls]
        for future in futures:
            yield future.result()


def print_progress(res: Any) -> None:
    """One progress line per finished case (non-JSON mode)."""
    msg = f"  {res.message}" if res.message else ""
    icon = STATUS_ICONS.get(res.status, "?")
    print(f"{icon} {res.name:<42}  {res.status} [{res.variant}]{msg}", flush=True)


def print_console_summary(
    results: Sequence[Any], counts: dict[str, int], *, json_out: bool
) -> int:
    """Totals (non-JSON mode) plus FAIL/KINDS_MISMATCH lines on stderr.

    Returns the exit code: 1 when any case FAILed or ERRORed, else 0.
    """
    if not json_out:
        sep = "\u2500" * 60
        print(f"\n{sep}")
        print(
            f"Total: {len(results)}  "
            + "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        )
    failures = counts.get("FAIL", 0) + counts.get("ERROR", 0)
    for r in results:
        if r.status in ("FAIL", "ERROR"):
            print(
                f"FAIL: {r.name}  expected={r.expected!r} got={r.got!r}  {r.message}",
                file=sys.stderr,
            )
    if counts.get("KINDS_MISMATCH"):
        for r in results:
            if r.kinds_strict == "mismatch":
                print(
                    f"KINDS_MISMATCH: {r.name} [{r.status}]  {r.kinds_strict_detail}",
                    file=sys.stderr,
                )
    return 1 if failures else 0
