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

"""Shared "is this the canonical CI lane" helper — a non-``test_`` leaf
module (tests/CLAUDE.md's `_strict_process.py`/`_detector_mutations.py`
pattern), split out so a module using it in its own ``pytestmark`` (which
must import as little as possible, since it's evaluated at *collection*
time on every lane) doesn't have to grow past the file-size cap to also
carry this helper's own direct unit tests — see test_canonical_lane.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def canonical_python(root: Path = ROOT) -> tuple[int, int]:
    """``repo_facts.json``'s ``canonical_python``, the single source of truth
    (AGENTS.md's "Line-coverage floor" section) for which unit-test lane is
    the one that actually matters for a platform/interpreter-independent
    check — falls back to 3.13 (today's value) if the file is
    missing/malformed rather than failing collection over it.

    A caller using this in a module-level ``pytestmark`` is evaluated at
    *collection* time, on every lane — a malformed ``repo_facts.json`` (not
    a dict, or a dict without this key) must degrade to the fallback rather
    than raise, or collection breaks everywhere that module is collected,
    not just where the skip decision would apply (CodeRabbit review, PR
    #877). ``raw["canonical_python"]`` on a non-dict (e.g. ``[]``) raises
    ``TypeError``, not ``KeyError``.
    """
    try:
        raw = json.loads((root / "repo_facts.json").read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return 3, 13
        major, minor = (int(p) for p in str(raw["canonical_python"]).split("."))
        return major, minor
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        return 3, 13


def is_canonical_lane(root: Path = ROOT) -> bool:
    """Linux + ``repo_facts.json``'s ``canonical_python`` — the one unit-test
    matrix leg a platform/interpreter-independent check needs to run on."""
    return sys.platform.startswith("linux") and sys.version_info[
        :2
    ] == canonical_python(root)
