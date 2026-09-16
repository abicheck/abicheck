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

"""Building the zip archives the `verify-source-run` tests unpack.

Shared rather than copied: `test_action_run_selection.py` and
`test_action_artifact_extraction.py` both need archives whose recorded Unix
*mode* is meaningful (the symlink and non-regular refusals are read from
it), and a second copy of that detail is a second place for a fixture to
drift out of agreement with what a real `actions/upload-artifact` zip looks
like -- which `tests/CLAUDE.md` records as having already cost four review
findings on one PR elsewhere in this suite.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

#: `ZipInfo.create_system` for Unix. The mode bits in `external_attr` are
#: only read back when the archive says it came from a Unix host, so a
#: fixture that omits this silently exercises the non-Unix path instead.
UNIX_HOST = 3


def build_zip(
    tmp_path: Path,
    entries: list[tuple[str, bytes]],
    *,
    symlinks: tuple[str, ...] = (),
    modes: dict[str, int] | None = None,
    compress: int = zipfile.ZIP_DEFLATED,
    name: str = "artifact.zip",
) -> Path:
    """An archive with *entries*, recording real Unix modes."""
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", compression=compress) as zf:
        for entry_name, payload in entries:
            info = zipfile.ZipInfo(entry_name)
            info.compress_type = compress
            info.create_system = UNIX_HOST
            mode = (
                0o120000
                if entry_name in symlinks
                else (modes or {}).get(entry_name, 0o100644)
            )
            info.external_attr = mode << 16
            zf.writestr(info, payload)
    return path
