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

"""A test that reads the repository's own text must state the encoding.

Bug class `tests.locale_dependent_repo_text_read`. `Path.read_text()` and
`open(path)` with no `encoding=` decode using the *host's* preferred encoding.
That is UTF-8 on the Linux and macOS lanes and `cp1252` on a default Windows
runner, so a checked-in UTF-8 file containing any non-ASCII byte — an em dash
or a `§`, which this repository's own comment prose is full of — reads fine on
two platforms and raises `UnicodeDecodeError` on the third.

The `test_workflow_*.py` guards hit exactly this: four modules each read
`.github/workflows/*.yml` at import time with a bare `read_text()`, and the
Windows unit lane failed at *collection*, not on any assertion. A fixed-input
regression test ("this module can now be imported") would foreclose only those
four files, so this module states the class instead, in two halves:

* a **structural** half — no test module may read a path rooted at the
  repository with the encoding left to the platform; and
* a **behavioural** half — the checked-in text those readers consume really
  does contain bytes a `cp1252` host cannot decode, so the structural rule is
  defending against a live failure rather than a hypothetical one. Without
  this the rule could pass vacuously the day the repository became pure ASCII,
  which is precisely the "toy-scale fixture" failure mode AGENTS.md's
  third-party-boundary lesson describes.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from _workflow_files import ENCODING, workflow_paths

TESTS_DIR = Path(__file__).resolve().parent

#: Names that resolve to a repository-rooted path in this test suite. A read
#: relative to one of these is reading checked-in content; a read relative to
#: `tmp_path` or a fixture directory the test itself wrote is not.
_REPO_ROOTED_NAMES = frozenset({"REPO_ROOT", "WORKFLOW_DIR", "ROOT", "PROJECT_ROOT"})


def _is_repo_rooted(node: ast.AST) -> bool:
    """True when `node` is built out of a repository-root constant, however
    many `/` joins and attribute hops deep."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in _REPO_ROOTED_NAMES:
            return True
    return False


def _opens_in_binary_mode(call: ast.Call) -> bool:
    """A binary read decodes nothing, so no encoding applies to it."""
    mode = call.args[1] if len(call.args) >= 2 else None
    for kw in call.keywords:
        if kw.arg == "mode":
            mode = kw.value
    return isinstance(mode, ast.Constant) and "b" in str(mode.value)


def _encoding_is_stated(call: ast.Call) -> bool:
    if any(kw.arg == "encoding" for kw in call.keywords):
        return True
    if _opens_in_binary_mode(call):
        return True
    # open(path, mode, buffering, encoding) positionally — rare, but legal.
    return len(call.args) >= 4


def _unencoded_repo_reads(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding=ENCODING))
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("read_text", "write_text"):
            target = func.value
        elif isinstance(func, ast.Name) and func.id == "open":
            target = node.args[0] if node.args else None
        else:
            continue
        if target is None or not _is_repo_rooted(target):
            continue
        if _encoding_is_stated(node):
            continue
        findings.append(f"{path.name}:{node.lineno}: {ast.unparse(node)[:90]}")
    return findings


def test_no_test_reads_checked_in_text_with_a_platform_dependent_encoding() -> None:
    findings: list[str] = []
    for module in sorted(TESTS_DIR.rglob("*.py")):
        findings += _unencoded_repo_reads(module)
    assert not findings, (
        "these reads of checked-in repository text leave the encoding to the "
        "host's locale, so they decode as cp1252 on the Windows lanes and fail "
        "on any non-ASCII byte; read through tests/_workflow_files.py's "
        "read_repo_text(), or pass encoding='utf-8':\n  " + "\n  ".join(findings)
    )


def test_the_scan_can_actually_see_an_unencoded_read() -> None:
    """Guards the AST scan: a matcher that recognised nothing would make the
    assertion above vacuously true."""
    module = ast.parse(
        "from x import REPO_ROOT\n"
        "a = (REPO_ROOT / 'f.yml').read_text()\n"
        "b = open(REPO_ROOT / 'f.yml')\n"
        "b2 = open(REPO_ROOT / 'f.bin', 'rb')\n"
        "c = (REPO_ROOT / 'f.yml').read_text(encoding='utf-8')\n"
        "d = (tmp_path / 'f.yml').read_text()\n"
    )
    calls = [n for n in ast.walk(module) if isinstance(n, ast.Call)]
    flagged = []
    for node in calls:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "read_text":
            target = func.value
        elif isinstance(func, ast.Name) and func.id == "open":
            target = node.args[0]
        else:
            continue
        if _is_repo_rooted(target) and not _encoding_is_stated(node):
            flagged.append(node.lineno)
    assert flagged == [2, 3], (
        "expected the bare repo-rooted reads on lines 2 and 3 -- and not the "
        f"binary open, the stated encoding, or the tmp_path read; got {flagged}"
    )


@pytest.mark.parametrize("path", workflow_paths(), ids=lambda p: p.name)
def test_workflow_text_is_utf8_that_a_cp1252_host_could_not_have_decoded(
    path: Path,
) -> None:
    """Each workflow must be valid UTF-8 — and collectively they must contain
    at least one byte `cp1252` rejects, or the rule above defends nothing."""
    path.read_bytes().decode("utf-8")


def test_some_checked_in_workflow_really_defeats_the_platform_default() -> None:
    undecodable = []
    for path in workflow_paths():
        try:
            path.read_bytes().decode("cp1252")
        except UnicodeDecodeError:
            undecodable.append(path.name)
    assert undecodable, (
        "no checked-in workflow contains a byte cp1252 rejects, so the "
        "encoding rule above would pass even if every reader regressed; "
        "re-derive the non-vacuity precondition before deleting this test"
    )
