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

"""`scripts/mutmut_stable_param_ids.py` against a real pytest, used as mutmut
uses it: several `pytest.main` sessions in one process, the later ones
selecting tests by the node ids the first one recorded.

Oracles are independent of the plugin: the first session's ids in a process
*without* the plugin (pytest's own spelling), and the set of tests that
actually ran.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from hypothesis import given, settings, strategies as st

REPO = Path(__file__).resolve().parents[1]

# Every id shape pytest escapes, through every way a parametrize mark is
# spelled and shared: callable ids, list ids, auto ids, a class-level mark
# reaching two methods, a module-level `pytestmark`, and a one-shot generator
# (which the plugin must leave to pytest's own cache).
_FIXTURE = r"""
import pytest
VALS = ["a\\b", "line\nbreak", "café", "refs\\artifacts\\x.json", "plain"]

@pytest.mark.parametrize("v", VALS, ids=lambda v: v)
def test_callable(v): pass

@pytest.mark.parametrize("v", VALS, ids=list(VALS))
def test_list(v): pass

@pytest.mark.parametrize("v", VALS)
def test_auto(v): pass

@pytest.mark.parametrize("v", VALS, ids=list(VALS))
class TestShared:
    def test_one(self, v): pass
    def test_two(self, v): pass

@pytest.mark.parametrize("v", ["p\\q", "r"], ids=(i for i in ["g\\1", "g2"]))
class TestGenerator:
    def test_one(self, v): pass
    def test_two(self, v): pass
"""
_MODULE_MARK = r"""
import pytest
pytestmark = pytest.mark.parametrize("v", ["m\\1", "m2"], ids=lambda v: v)
def test_a(v): pass
def test_b(v): pass
"""

_DRIVER = r"""
import json, sys, pytest

class Record:
    def __init__(self): self.collected, self.ran = [], []
    def pytest_collection_finish(self, session):
        self.collected = [i.nodeid for i in session.items]
    def pytest_runtest_logreport(self, report):
        if report.when == "call": self.ran.append(report.nodeid)

plugin_args = sys.argv[1:]
out = []
first = None
for session in range(3):
    rec = Record()
    # Sessions after the first select by the first session's ids, as mutmut's
    # clean and per-mutant runs select by the stats pass's ids.
    args = ["tests"] if first is None else first
    # An empty config of our own, not /dev/null: that path does not exist on
    # Windows, where pytest would exit 4 before collecting anything.
    rc = int(pytest.main(["-q", "-p", "no:cacheprovider", "--rootdir=.", "-c",
                          "pytest.ini", *plugin_args, *args], plugins=[rec]))
    if first is None:
        first = rec.collected
    out.append({"rc": rc, "collected": rec.collected, "ran": rec.ran})
print("RESULT" + json.dumps(out))
"""


def _sessions(tmp_path: Path, plugin: bool) -> list[dict]:
    tests = tmp_path / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    # UTF-8 explicitly: the fixture holds "café", and Windows' locale default
    # (cp1252) writes bytes Python then refuses to parse as source.
    (tests / "test_ids.py").write_text(_FIXTURE, encoding="utf-8")
    (tests / "test_module_mark.py").write_text(_MODULE_MARK, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    args = ["-p", "mutmut_stable_param_ids"] if plugin else []
    env = {**os.environ, "PYTHONPATH": str(REPO / "scripts")}
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, str(driver), *args],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT"))
    return json.loads(line[len("RESULT") :])


def test_without_the_plugin_a_later_session_cannot_find_the_recorded_ids(
    tmp_path: Path,
) -> None:
    """Negative control: pins pytest's own behaviour, so this file fails
    loudly (rather than passing vacuously) if a pytest release fixes it."""
    first, second, _ = _sessions(tmp_path, plugin=False)
    assert first["rc"] == 0
    assert second["rc"] == 4, "pytest kept the ids stable: the defect is gone"


def test_every_session_collects_and_runs_exactly_the_first_sessions_ids(
    tmp_path: Path,
) -> None:
    sessions = _sessions(tmp_path, plugin=True)
    first = sessions[0]
    assert first["rc"] == 0 and first["collected"]
    for later in sessions[1:]:
        assert later["rc"] == 0
        assert later["collected"] == first["collected"]
        assert sorted(later["ran"]) == sorted(first["collected"])


def test_the_plugin_does_not_change_pytests_own_first_spelling(tmp_path: Path) -> None:
    with_plugin = _sessions(tmp_path / "a", plugin=True)[0]["collected"]
    without = _sessions(tmp_path / "b", plugin=False)[0]["collected"]
    assert with_plugin == without
    # The shapes the defect lives in are really present in the oracle.
    assert any("\\\\" in i for i in without) and any("\\n" in i for i in without)


def test_the_mutmut_lane_loads_the_plugin_from_its_copied_tree() -> None:
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["mutmut"]
    args = cfg["pytest_add_cli_args"]
    assert args[args.index("-p") + 1] == "scripts.mutmut_stable_param_ids"
    # mutmut runs from mutants/, which holds only source_paths + also_copy.
    assert "scripts" in cfg["also_copy"]
    assert (REPO / "scripts" / "mutmut_stable_param_ids.py").is_file()


def _plugin():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import mutmut_stable_param_ids
    finally:
        sys.path.remove(str(REPO / "scripts"))
    return mutmut_stable_param_ids


@settings(max_examples=500, deadline=None)
@given(st.lists(st.text(), max_size=4))
def test_unescape_inverts_pytests_own_escaping(raw: list[str]) -> None:
    """Oracle: pytest's real escape function. Whatever the id text (control
    characters, backslashes, any code point), the inverse recovers it."""
    from _pytest.python import _ascii_escaped_by_config

    plugin = _plugin()
    escaped = [_ascii_escaped_by_config(r, None) for r in raw]
    assert plugin.unescape_ids(escaped, _ascii_escaped_by_config, None) == raw


def test_an_entry_that_is_not_an_exact_escape_is_left_to_pytest() -> None:
    from _pytest.python import _ascii_escaped_by_config

    plugin = _plugin()
    # Not something pytest's escaping can produce (a lone trailing backslash,
    # non-ASCII, a non-string), so there is no exact inverse to hand back.
    for bad in (["ok", "tail\\"], ["café"], [None]):
        assert plugin.unescape_ids(bad, _ascii_escaped_by_config, None) is None
    assert plugin.unescape_ids(["x"], None, None) is None
