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

"""Which evidence inputs ``compare``'s inline source-tree dump consumes.

A pure decision, split out of ``commands/compare.py`` so it can be tested
directly and without importing the Click command tree.
"""

from __future__ import annotations

from pathlib import Path


def inline_dump_evidence_routing(
    sources: Path | None,
    build_info: Path | None,
    sources_raw: bool,
    build_info_raw: bool,
) -> tuple[Path | None, Path | None, Path | None, Path | None]:
    """Split one side's ``--sources``/``--build-info`` between the inline dump
    and the out-of-band pack path: ``(dump_sources, dump_build_info,
    kept_sources, kept_build_info)``.

    The inline dump must receive exactly what ``abicheck dump --sources S
    --build-info B`` would, so the two commands select the same compile units
    for the same evidence. A *pack-shaped* ``--build-info`` next to a raw
    ``--sources`` tree is therefore handed to the dump too -- that is what
    seeds its L4 replay with the pack's compile units (``collect_inline_pack``'s
    ``base_build``). Routing it out-of-band instead (the previous behaviour)
    left the inline replay to discover the tree's own compile DB / inferred
    build query, so a pack scoped to one Bazel target (1 TU) replayed every TU
    the workspace had (19 in the reporting lab), while ``dump`` replayed 1.
    Whatever the dump consumes is not kept, so the out-of-band path never
    attaches it a second time.
    """
    dump_sources = sources if sources_raw else None
    # A pack-shaped build-info is consumed alongside a raw source tree (see
    # above); on its own it stays an out-of-band pack.
    dump_build_info = build_info if (build_info_raw or sources_raw) else None
    kept_sources = None if sources_raw else sources
    kept_build_info = None if dump_build_info is not None else build_info
    return dump_sources, dump_build_info, kept_sources, kept_build_info
