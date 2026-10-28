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

"""``build_bundle_snapshot()`` -- the real-path wrapper, handed live binaries
on disk -- keeps the filesystem alias probe on.

It delegates to ``build_bundle_snapshot_from_metadata()``, whose own default
is ``probe_filesystem=False``; the wrapper must thread ``True`` through, or it
silently loses symlink-target and hard-link alias probing (Codex review).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import abicheck.bundle as bundle_mod
from abicheck.elf_metadata import ElfMetadata


def test_build_bundle_snapshot_still_probes_the_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}
    real_impl = bundle_mod.build_bundle_snapshot_from_metadata

    def _capturing(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return real_impl(*args, **kwargs)

    monkeypatch.setattr(bundle_mod, "build_bundle_snapshot_from_metadata", _capturing)
    # Only the ELF magic is needed here; the parse itself is stubbed because a
    # real export-table read failure is refused by build_bundle_snapshot().
    monkeypatch.setattr(
        bundle_mod, "parse_elf_metadata", lambda path: ElfMetadata(soname=path.name)
    )
    lib = tmp_path / "libfoo.so"
    lib.write_bytes(b"\x7fELF" + b"\0" * 60)

    bundle_mod.build_bundle_snapshot({"libfoo.so": lib})

    assert captured.get("probe_filesystem") is True
