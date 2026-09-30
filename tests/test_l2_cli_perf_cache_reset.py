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

"""The L2 perf harness may only ever empty a cache root it created.

`_reset_cache` used to `rmtree` any path it was handed; a unit test handed
it `/tmp`, and every run of that test deleted whatever the process could
reach under `/tmp` while other jobs were using it -- xdist basetemps, a
gcc object mid-link, `run.sh` scratch directories, a commit-signing helper.
The contract, stated over several kinds of foreign directory rather than
the one that bit: an unmarked path is refused *and left byte-for-byte
intact*; a marked one is emptied without following links out of it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_l2_cli_perf_contracts import harness


def _tree(root: Path) -> dict[str, bytes | None]:
    return {
        str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None)
        for p in sorted(root.rglob("*"))
    }


def _populate(root: Path) -> None:
    (root / "a").mkdir(parents=True)
    (root / "a" / "f").write_bytes(b"1")
    (root / "g").write_bytes(b"2")


@pytest.mark.parametrize(
    "shape",
    ["plain-dir", "marker-only-in-a-child", "marker-is-a-directory", "empty-dir"],
)
def test_an_unmarked_directory_is_refused_and_left_intact(
    tmp_path: Path, shape: str
) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    if shape != "empty-dir":
        _populate(root)
    if shape == "marker-only-in-a-child":
        (root / "a" / harness.CACHE_ROOT_MARKER).touch()
    if shape == "marker-is-a-directory":
        (root / harness.CACHE_ROOT_MARKER).mkdir()
    before = _tree(root)
    with pytest.raises(ValueError, match="refusing to clear"):
        harness._reset_cache(root)
    assert _tree(root) == before


def test_a_missing_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        harness._reset_cache(tmp_path / "never-created")
    assert not (tmp_path / "never-created").exists()


def test_a_prepared_root_is_emptied_but_kept(tmp_path: Path) -> None:
    root = harness.prepare_cache_root(tmp_path / "cache")
    _populate(root)
    harness._reset_cache(root)
    assert root.is_dir()
    assert [p.name for p in root.iterdir()] == [harness.CACHE_ROOT_MARKER]
    harness._reset_cache(root)  # idempotent on an already-cold root


def test_a_link_out_of_the_root_is_removed_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _populate(outside)
    before = _tree(outside)
    root = harness.prepare_cache_root(tmp_path / "cache")
    try:
        (root / "dirlink").symlink_to(outside, target_is_directory=True)
        (root / "filelink").symlink_to(outside / "g")
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    harness._reset_cache(root)
    assert [p.name for p in root.iterdir()] == [harness.CACHE_ROOT_MARKER]
    assert _tree(outside) == before


def test_the_real_work_area_prepares_its_cache_root(tmp_path: Path) -> None:
    area = harness._ScenarioArea(tmp_path / "area", install_spy=False)
    assert (area.cache_root / harness.CACHE_ROOT_MARKER).is_file()
    harness._reset_cache(area.cache_root)
