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

"""One owner for reading the repository's own workflow/Action YAML in tests.

The four `test_workflow_*.py` guards each independently re-derived
`.github/workflows` and each called a bare `path.read_text()`. That bare call
is the bug: `Path.read_text()` with no `encoding=` decodes using the
*platform's* preferred encoding, which is UTF-8 on Linux/macOS and `cp1252`
on a default Windows runner. Every one of these files is checked-in UTF-8
carrying real non-ASCII prose (em dashes and `§` in the comment blocks), so
the guards collected fine on two platforms and raised `UnicodeDecodeError`
at *import* time on the third — turning assurance meant to protect CI into a
red Windows lane.

The fix is one loader rather than five corrected call sites: a workflow file
read anywhere in the test suite is read here, with the encoding stated. The
encoding of a file the repository itself owns is not a per-caller decision.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

#: Checked-in repository text is UTF-8 regardless of the host's locale.
ENCODING = "utf-8"


def read_repo_text(path: Path) -> str:
    """Read a checked-in repository text file with its encoding stated."""
    return path.read_text(encoding=ENCODING)


def workflow_paths() -> list[Path]:
    """Every workflow definition, in a stable order."""
    return sorted(WORKFLOW_DIR.glob("*.yml"))


def workflow_texts() -> list[tuple[Path, str]]:
    """Each workflow's path and raw text."""
    return [(path, read_repo_text(path)) for path in workflow_paths()]
