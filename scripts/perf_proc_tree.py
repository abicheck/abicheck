#!/usr/bin/env python3
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

"""``/proc`` readers for the perf receipt's process-tree memory sampler.

Split out of ``perf_receipt.py`` to keep that file under its size record: the
per-process RSS and PSS readers and the descendant walk that
``perf_receipt.TreeRssSampler`` samples each interval. Linux-only by nature;
callers check for ``/proc`` before sampling.
"""

from __future__ import annotations

import os
from pathlib import Path


def proc_rss_bytes(pid: int) -> int | None:
    try:
        fields = Path(f"/proc/{pid}/statm").read_text(encoding="utf-8").split()
        return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, IndexError, ValueError):
        return None


def proc_pss_bytes(pid: int) -> int | None:
    """*pid*'s proportional set size from ``smaps_rollup``, or ``None``."""
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


def descendants(root: int) -> list[int]:
    """*root* plus every live descendant, via one ``/proc`` scan.

    Builds the parent map from ``/proc/<pid>/stat`` and walks down from *root*,
    rather than reading ``children`` files recursively: one pass is cheaper, and
    it cannot partially observe a tree that is changing under it.
    """
    parents: dict[int, int] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return [root]
    for name in entries:
        if not name.isdigit():
            continue
        try:
            stat = Path(f"/proc/{name}/stat").read_text(encoding="utf-8")
            # The comm field may itself contain spaces and parentheses, so
            # split after the LAST ')' rather than on whitespace.
            ppid = int(stat[stat.rindex(")") + 1 :].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        parents[int(name)] = ppid
    tree = [root]
    frontier = [root]
    while frontier:
        current = frontier.pop()
        kids = [pid for pid, ppid in parents.items() if ppid == current]
        tree.extend(kids)
        frontier.extend(kids)
    return tree
