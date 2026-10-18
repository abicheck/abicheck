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

"""The compile-DB adapter stores POSIX-separated paths whatever OS wrote the
database. A native ``D:\\...\\src\\foo.cpp`` never matches the ``/``-keyed graph
paths built from the same evidence, and the evidence may be compared on another
OS, so the separator must not depend on the collecting host.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.buildsource.adapters.compile_db import CompileDbAdapter
from abicheck.buildsource.redaction import RedactionPolicy


def _collect_one(tmp_path: Path, directory: str, file: str):
    db = tmp_path / "compile_commands.json"
    db.write_text(
        json.dumps(
            [
                {
                    "directory": directory,
                    "file": file,
                    "arguments": ["c++", "-std=c++17", "-c", file],
                }
            ]
        ),
        encoding="utf-8",
    )
    # Home redaction is a separate policy (it rewrites a home prefix to "~");
    # switch it off so these cases isolate the separator behaviour under test.
    redaction = RedactionPolicy(redact_home=False)
    return CompileDbAdapter(db, redaction=redaction).collect().compile_units[0]


@pytest.mark.parametrize(
    ("directory_suffix", "file", "source_tail"),
    [
        ("", "src/foo.cpp", "src/foo.cpp"),
        ("", "src\\foo.cpp", "src/foo.cpp"),
        ("\\build", "src/foo.cpp", "build/src/foo.cpp"),
        ("\\build", "src\\foo.cpp", "build/src/foo.cpp"),
    ],
)
def test_source_and_directory_use_posix_separators(
    tmp_path: Path, directory_suffix: str, file: str, source_tail: str
) -> None:
    # Expected values come from pathlib's as_posix(), not from the code under
    # test, and are rooted at tmp_path so the case holds on every host: a bare
    # "/work/build" is drive-relative on Windows and would read D:/work/....
    root = tmp_path.as_posix()
    unit = _collect_one(tmp_path, str(tmp_path) + directory_suffix, file)
    assert unit.source == f"{root}/{source_tail}"
    assert unit.directory == root + directory_suffix.replace("\\", "/")
    assert "\\" not in unit.id
