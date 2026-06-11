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

"""Offline tests for the conda-forge resolution helpers of the parity loop.

Only the pure resolution/naming helpers are exercised here (no network, conda,
or abicheck). The orchestration in ``main`` is integration-only and validated by
hand against real binaries.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path("validation/scripts/run_tracker_parity.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("run_tracker_parity", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# A trimmed anaconda.org-style payload: two versions, two subdirs, multiple builds.
_API = {
    "files": [
        {
            "version": "2.9.4",
            "basename": "linux-64/libxml2-2.9.4-0.tar.bz2",
            "attrs": {"subdir": "linux-64"},
        },
        {
            "version": "2.9.4",
            "basename": "linux-64/libxml2-2.9.4-4.tar.bz2",
            "attrs": {"subdir": "linux-64"},
        },
        {
            "version": "2.9.4",
            "basename": "linux-64/libxml2-2.9.4-2.tar.bz2",
            "attrs": {"subdir": "linux-64"},
        },
        {
            "version": "2.9.4",
            "basename": "osx-64/libxml2-2.9.4-4.tar.bz2",
            "attrs": {"subdir": "osx-64"},
        },
        {
            "version": "2.9.3",
            "basename": "linux-64/libxml2-2.9.3-9.tar.bz2",
            "attrs": {"subdir": "linux-64"},
        },
    ]
}


def test_conda_download_url_joins_channel_and_basename() -> None:
    mod = _load_module()
    assert (
        mod.conda_download_url("linux-64/libxml2-2.9.4-4.tar.bz2")
        == "https://conda.anaconda.org/conda-forge/linux-64/libxml2-2.9.4-4.tar.bz2"
    )
    # tolerate a stray leading slash on the basename
    assert mod.conda_download_url("/linux-64/x.conda").count("conda-forge//") == 0


def test_select_conda_basename_picks_newest_build_in_subdir() -> None:
    mod = _load_module()
    # highest build number (-4) wins among the three linux-64 builds of 2.9.4
    assert (
        mod.select_conda_basename(_API, "2.9.4", "linux-64")
        == "linux-64/libxml2-2.9.4-4.tar.bz2"
    )
    # subdir is respected
    assert (
        mod.select_conda_basename(_API, "2.9.4", "osx-64")
        == "osx-64/libxml2-2.9.4-4.tar.bz2"
    )
    # missing version -> None (pair stays UNCOMPARABLE upstream)
    assert mod.select_conda_basename(_API, "9.9.9", "linux-64") is None
    assert mod.select_conda_basename(_API, "2.9.4", "win-64") is None


def test_build_number_parses_hash_and_plain_builds() -> None:
    mod = _load_module()
    assert mod._build_number("linux-64/zstd-1.5.5-hfc55251_0.conda") == 0
    assert mod._build_number("linux-64/libxml2-2.9.4-4.tar.bz2") == 4
    assert mod._build_number("linux-64/tbb-2021.9.0-hf52228f_3.conda") == 3
    assert mod._build_number("no-build-number") == -1


def test_logical_name_strips_so_suffix_and_embedded_version() -> None:
    mod = _load_module()
    assert mod._logical_name("lib/libxml2.so.2.9.4") == "libxml2"
    assert mod._logical_name("lib/libcapnp-1.4.0.so") == "libcapnp"
    assert mod._logical_name("lib/libssl.so.3") == "libssl"
