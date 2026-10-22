#!/usr/bin/env python3
"""Process-tree PSS sampling: the memory a forking run really costs.

:class:`~perf_receipt.TreeRssSampler` sums RSS over a process tree, which
counts every page once *per process that maps it*. That is a fine bound for a
parent plus a few unrelated compiler children, but it badly overstates a run
that forks workers: a forked child starts by sharing the parent's whole heap
copy-on-write, so N children look like N extra parents although almost none of
those pages were ever copied. Since release members fan out to forked
children by default (``ABICHECK_MEMBER_PROCESSES``,
``abicheck/workflows/release_member_processes.py``), the RSS sum of a
10-library directory compare grew past 1 GB while the physical memory the run
needed did not.

PSS (proportional set size, ``/proc/<pid>/smaps_rollup``) charges each page to
the processes sharing it in proportion, so the PSS **sum** over a tree counts a
shared copy-on-write page exactly once: it is the tree's real resident cost.
Reading ``smaps_rollup`` walks the process's page tables, so it is dearer than
``statm``; at a tree of a dozen processes sampled every 20 ms that is noise.

The sampler records both sums from the same instants, so a receipt can show the
naive figure next to the gated one.
"""

from __future__ import annotations

from pathlib import Path

from perf_receipt import TreeRssSampler, _descendants, _proc_rss_bytes


def proc_pss_bytes(pid: int) -> int | None:
    """``Pss:`` from ``/proc/<pid>/smaps_rollup`` in bytes, or ``None``."""
    try:
        text = Path(f"/proc/{pid}/smaps_rollup").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("Pss:"):
            try:
                return int(line.split()[1]) * 1024
            except (IndexError, ValueError):
                return None
    return None


class TreePssSampler(TreeRssSampler):
    """A :class:`TreeRssSampler` that also tracks the tree's peak PSS sum.

    ``peak`` (RSS sum) keeps its meaning; ``peak_pss`` is the largest PSS sum
    seen at one sampling instant. ``pss_unavailable_reason`` is set when no
    live process ever yielded a PSS figure (no ``smaps_rollup``: kernel older
    than 4.14, or a restricted ``/proc``), so a caller can fall back to the
    RSS sum *visibly* instead of gating on a zero.
    """

    def __init__(self, interval_seconds: float = 0.02) -> None:
        super().__init__(interval_seconds)
        self.peak_pss = 0
        self.pss_samples = 0

    @property
    def pss_unavailable_reason(self) -> str | None:
        if self.unavailable_reason:
            return self.unavailable_reason
        if not self.pss_samples:
            return "no /proc/<pid>/smaps_rollup Pss figure was readable"
        return None

    def start(self, root_pid: int) -> None:
        if self.unavailable_reason:
            return
        import threading

        def loop() -> None:
            while not self._stop.is_set():
                rss_total = pss_total = live = pss_live = 0
                for pid in _descendants(root_pid):
                    rss = _proc_rss_bytes(pid)
                    if rss is None:
                        continue
                    rss_total += rss
                    live += 1
                    pss = proc_pss_bytes(pid)
                    if pss is not None:
                        pss_total += pss
                        pss_live += 1
                if live:
                    self.samples += 1
                    self.peak = max(self.peak, rss_total)
                    self.max_processes = max(self.max_processes, live)
                # A PSS sample counts only when every RSS-bearing process
                # supplied one: a partial sum (a process that exited between
                # the two reads) understates the tree and must not set the peak.
                if live and pss_live == live:
                    self.pss_samples += 1
                    self.peak_pss = max(self.peak_pss, pss_total)
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
