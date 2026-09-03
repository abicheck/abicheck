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

"""The write-or-``--check`` driver every committed-snapshot generator shares.

A generator script owns its fixtures — which cases exist, what each snapshot
means — and nothing else: the walk over ``{case: {filename: builder}}``, the
serialization, the byte-comparison in ``--check`` mode, the drift reporting and
the exit code are the same procedure in each. ``gen_g20_fixtures.py`` and
``gen_reachability_examples.py`` each carried their own copy of it (CodeFactor:
duplicate code); stated once here, so "regenerate" and "check for drift" cannot
mean subtly different things depending on which generator you ran.

A **leaf** module, pure stdlib apart from the caller-supplied builders.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

#: A fixture is either a builder producing an ``AbiSnapshot``-shaped object to
#: serialize, or literal file text (a hand-written YAML sidecar) to write as-is.
Fixture = Callable[[], Any] | str


def render_fixture(builder: Fixture, to_dict: Callable[[Any], Any]) -> str:
    """Serialize one fixture to its exact on-disk text."""
    if isinstance(builder, str):
        return builder
    return json.dumps(to_dict(builder()), indent=2, sort_keys=True) + "\n"


def sync_fixtures(
    fixtures: Mapping[str, Mapping[str, Fixture]],
    *,
    case_dir: Callable[[str], Path],
    root: Path,
    to_dict: Callable[[Any], Any],
    check: bool,
    label: str,
    regen_command: str,
) -> int:
    """Write every fixture, or (with *check*) report drift without writing.

    *fixtures* maps a case directory name to its ``{filename: fixture}`` set.
    *case_dir* resolves a case name to its on-disk directory -- production
    callers pass ``example_catalog.case_dir`` (Phase 3,
    docs/contribute/plans/examples-catalog-split.md); a test injects its own
    resolver (e.g. ``lambda name: tmp_path / "examples" / name``) rather than
    pointing this driver at the real catalog. Keeping the join itself behind
    a caller-supplied function -- not a flat directory this module joins by
    hand -- is what lets Phase 4's directory split change only
    ``example_catalog.case_dir`` and leave every one of this driver's callers
    untouched. *label* names the family in the summary line ("G20 fixtures"),
    and *regen_command* is what the drift message tells the reader to run.
    Returns the process exit code: 1 when *check* found drift, 0 otherwise.
    """
    drift = False
    written = 0
    for case_name, files in fixtures.items():
        target_dir = case_dir(case_name)
        for filename, builder in files.items():
            content = render_fixture(builder, to_dict)
            path = target_dir / filename
            if check:
                # Absence is drift in its own right, checked before the content
                # comparison: mapping a missing file to "" would let an empty
                # literal fixture pass --check while nothing is committed.
                if not path.is_file() or path.read_text(encoding="utf-8") != content:
                    print(f"drift: {path.relative_to(root)}", file=sys.stderr)
                    drift = True
            else:
                target_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                written += 1

    if check:
        if drift:
            print(f"{label} out of date. Run: {regen_command}", file=sys.stderr)
            return 1
        print(f"{label} up to date.")
        return 0
    print(f"Wrote {written} {label.lower()} file(s).")
    return 0
