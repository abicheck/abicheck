# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Nikolay Petrov
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

"""Collect one of N disjoint slices of the test modules (``--collect-partition``).

``scripts/gen_repo_facts.py`` counts the fast lane with a full ``pytest
--collect-only`` over ~92,000 tests. In one process that took 107 s of its
120 s budget on a GitHub runner and tipped over on a slower one. With
``--collect-partition=K/N`` each process collects only the test modules whose
path hashes to slice K, so N processes on N cores share the work, and their
counts sum to the full count: every ``test_*.py`` module lands in exactly one
slice, and nothing else is a test module.

Like ``--collect-mentioning`` (tests/pytest_marker_prefilter.py), the filter
runs in ``pytest_ignore_collect`` during ordinary ``tests/`` collection, never
as a file list on the command line, which imports some modules differently.
``tests/test_collect_partition.py`` collects the real tree both ways and
requires the same node ids.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

OPTION = "--collect-partition"


def add_option(parser) -> None:
    parser.addoption(
        OPTION,
        default=None,
        metavar="K/N",
        help=(
            "Collect only test modules in slice K of N (1-based, by path hash); "
            "the N slices are disjoint and cover every module. See "
            "tests/pytest_collect_partition.py."
        ),
    )


def parse_partition(spec: str) -> tuple[int, int]:
    """``"K/N"`` as ``(K, N)``; raises ``ValueError`` unless ``1 <= K <= N``."""
    k_text, sep, n_text = spec.partition("/")
    if not sep:
        raise ValueError(f"{OPTION} expects K/N, got {spec!r}")
    k, n = int(k_text), int(n_text)
    if not 1 <= k <= n:
        raise ValueError(f"{OPTION}: need 1 <= K <= N, got {spec!r}")
    return k, n


def slice_of(relative: str, n: int) -> int:
    """The 1-based slice of the module at *relative* (a POSIX path): a stable
    hash, so the same module lands in the same slice in every process."""
    digest = hashlib.sha256(relative.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % n + 1


def should_ignore(path: Path, root: Path, spec: str | None) -> bool:
    """True when *path* is a test module outside the requested slice.

    Directories, conftests and helper modules are always collected."""
    if not spec or path.suffix != ".py" or not path.name.startswith("test_"):
        return False
    k, n = parse_partition(spec)
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = path.as_posix()
    return slice_of(relative, n) != k
