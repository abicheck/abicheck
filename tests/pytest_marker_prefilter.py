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

"""Collect only the test files that can carry a marker (``--collect-mentioning``).

``-m slow`` over ``tests/`` keeps ~270 of ~64,000 tests, but pytest still
imports and collects every module first -- in each xdist worker. Measured on
a 4-core container that was ~87s per worker before a single slow test ran,
and roughly twice that on a GitHub runner: the largest fixed cost of the
``slow`` lane.

``--collect-mentioning=slow`` skips, at collection time, any test module whose
source text never contains ``slow``. A marker can only reach a test through
code naming it -- ``pytest.mark.slow``, ``pytestmark = ...slow``, or an alias
imported under a name containing it -- so the files kept are a superset of
those that can hold a ``slow`` test, and the ``-m`` expression still decides
which tests in them run.

The one hole is an alias whose own name hides the word (``heavy =
pytest.mark.slow`` imported elsewhere as ``heavy``). That is why the claim is
not left to this argument: ``tests/test_marker_prefilter.py`` collects the
whole tree with and without the prefilter and requires the same ``-m slow``
node ids, so a hole fails the build instead of silently dropping tests.

Filtering happens in ``pytest_ignore_collect`` during ordinary ``tests/``
collection rather than by passing a file list on the command line: an
explicit file list changes how some modules import (measured: one module
failed to import that way, dropping five slow tests).
"""

from __future__ import annotations

from pathlib import Path

OPTION = "--collect-mentioning"


def add_option(parser) -> None:
    parser.addoption(
        OPTION,
        default=None,
        metavar="TEXT",
        help=(
            "Skip collecting test modules whose source never contains TEXT "
            "(e.g. `-m slow --collect-mentioning=slow`); see tests/pytest_marker_prefilter.py."
        ),
    )


def should_ignore(path: Path, text: str | None) -> bool:
    """True when *path* is a test module that cannot mention *text*.

    Only ``test_*.py`` files are filtered; directories, conftests and helper
    modules are always collected as usual."""
    if not text or path.suffix != ".py" or not path.name.startswith("test_"):
        return False
    try:
        return text not in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
