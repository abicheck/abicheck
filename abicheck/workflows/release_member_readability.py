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

"""An unmatched release member no reader can open is ``failed``, not one-sided.

A release compare extracts only the members that pair. Discovery accepts
``libfoo.so``-style names on the name alone (``classify.
BinaryExtensionClassifier`` keeps text linker-script stubs discoverable), so
a NEW-only ``notes.so`` holding text was never opened and read as an
unmatched ``not_supplied`` member -- its unreadability unreported, though the
same file, once paired, is a ``failed`` member with an extraction error.

:func:`unreadable_unmatched_members` is the one rule, applied by every route
that holds a live side after pairing (the live fan-out's
``prepare_release_inputs`` and the stored-OLD/live-NEW driver,
``bundle_side_input``): an unmatched member whose file no content classifier
recognizes is returned as an acquisition failure, which the caller passes
to the scope record as that side's ``*_failed`` (the ``--dso-only`` "could
not classify" channel, ADR-065 D1). A matched member is left alone: its
extraction already reports the failure.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def unreadable_member_reason(path: Path) -> str:
    return (
        f"{path.name} is not readable as an ABI input: no ELF, PE or Mach-O "
        "image and no ABI snapshot document (it was discovered by its file "
        "name only)"
    )


def unreadable_unmatched_members(
    side_map: Mapping[str, Path], matched: set[str] | frozenset[str]
) -> dict[str, str]:
    """``{unreadable unmatched key: reason}`` for one side.

    *side_map* is one side's ``match key -> live file`` map; *matched* the
    keys that paired. Only an unmatched member whose file exists is probed,
    so a stored side's recorded names are never read from the filesystem.
    The member stays in *side_map*: the scope record names it by its file
    there, and gives a ``*_failed`` entry precedence over one-sidedness.
    """
    from ..extract.member_identity import is_content_readable_member

    unreadable: dict[str, str] = {}
    for key, path in side_map.items():
        if key in matched:
            continue
        try:
            is_file = path.is_file()
        except OSError:  # present (discovery listed it) but not even stat-able
            is_file = True
        if is_file and not is_content_readable_member(path):
            unreadable[key] = unreadable_member_reason(path)
    return unreadable
