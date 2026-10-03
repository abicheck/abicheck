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

"""``--collect-mentioning`` (tests/pytest_marker_prefilter.py) drops no test.

The ``slow`` lane passes ``-m slow --collect-mentioning=slow`` so pytest does
not import the tens of thousands of test modules that cannot hold a ``slow``
test. That is a speed-up only if the selection is *unchanged*, and the one
way to know is to collect it both ways: ``test_prefilter_selects_exactly_*``
does, over the real tree, so an alias that hides the word (``heavy =
pytest.mark.slow``) fails here instead of silently dropping tests from CI.
The other tests pin the hook's own contract and prove the comparison catches
exactly that hole.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pytest_marker_prefilter import should_ignore

ROOT = Path(__file__).resolve().parent.parent


def _collected(args: list[str], *, cwd: Path) -> set[str]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    # 0: collected; anything else (collection error, usage error) is a
    # failed measurement, never an empty selection.
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    return {line for line in proc.stdout.splitlines() if "::" in line}


@pytest.mark.repo_scan
def test_prefilter_selects_exactly_the_slow_tests_full_collection_does() -> None:
    full = _collected(["tests/", "-m", "slow"], cwd=ROOT)
    pre = _collected(["tests/", "-m", "slow", "--collect-mentioning=slow"], cwd=ROOT)
    # Vacuity guard: the lane really selects something.
    assert len(full) > 100, len(full)
    assert pre == full, {"dropped": sorted(full - pre), "added": sorted(pre - full)}


def test_only_test_modules_that_never_mention_the_text_are_skipped(
    tmp_path: Path,
) -> None:
    files = {
        "test_marked.py": "import pytest\n\n@pytest.mark.slow\ndef test_a(): pass\n",
        "test_plain.py": "def test_b(): pass\n",
        "conftest.py": "",
        "_helper.py": "",
        "test_data.txt": "",
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    ignored = {name for name in files if should_ignore(tmp_path / name, "slow")}
    # Only a test module lacking the text; helpers, conftests and non-Python
    # files are always collected as usual.
    assert ignored == {"test_plain.py"}
    # No text means no filtering at all.
    assert not any(should_ignore(tmp_path / name, None) for name in files)
    assert not should_ignore(tmp_path, "slow")


def test_the_comparison_catches_a_marker_alias_that_hides_the_word(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: a mark reaching a test through an alias whose name
    never says ``slow`` is the prefilter's one hole. The full and prefiltered
    collections must then differ, which is what the real-tree test asserts
    never happens."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tmp_path / "conftest.py").write_text(
        "from pytest_marker_prefilter import OPTION, add_option, should_ignore\n\n"
        "def pytest_addoption(parser):\n    add_option(parser)\n\n"
        "def pytest_ignore_collect(collection_path, config):\n"
        "    return True if should_ignore(collection_path, config.getoption(OPTION)) else None\n"
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    slow: x\n")
    (tests / "_marks.py").write_text("import pytest\n\nheavy = pytest.mark.slow\n")
    (tests / "test_hidden.py").write_text(
        "import os, sys\nsys.path.insert(0, os.path.dirname(__file__))\n"
        "from _marks import heavy\n\n@heavy\ndef test_hidden(): pass\n"
    )
    (tests / "test_visible.py").write_text(
        "import pytest\n\n@pytest.mark.slow\ndef test_visible(): pass\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "tests"))
    full = _collected(["-p", "no:xdist", "tests/", "-m", "slow"], cwd=tmp_path)
    pre = _collected(
        ["-p", "no:xdist", "tests/", "-m", "slow", "--collect-mentioning=slow"],
        cwd=tmp_path,
    )
    assert full == {
        "tests/test_hidden.py::test_hidden",
        "tests/test_visible.py::test_visible",
    }
    assert pre == {"tests/test_visible.py::test_visible"}
