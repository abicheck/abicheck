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

"""Binary-format descriptors shared by the dump orchestrator and its tests.

``_FormatHandler`` records how one binary format is recognised by its magic
bytes and which optional keyword arguments its builder accepts. The format
table itself and the builders stay in :mod:`abicheck.dumper`, which owns the
dispatch; this module only owns the descriptor type.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..model.snapshot import AbiSnapshot


@dataclass(frozen=True)
class _FormatHandler:
    """One binary format: how to recognise it and how to dump it (C3).

    The registry collapses the per-format magic-byte knowledge and the
    ``dump()`` dispatch into a single declarative entry — adding a new binary
    format is a new ``_FormatHandler`` in ``_FORMAT_HANDLERS`` rather than edits
    scattered across ``_detect_format`` and ``dump``'s if/elif chain.

    ``accepts_dwarf_only`` / ``accepts_debug_format`` record which optional
    kwargs the format's builder takes, so ``dump()`` forwards exactly the same
    arguments each ``_dump_*`` accepted before (ELF: both; Mach-O: dwarf_only
    only; PE: neither).
    """

    name: str
    builder: Callable[..., AbiSnapshot]
    magics: tuple[bytes, ...] = ()
    magic_prefix: bytes | None = None
    accepts_dwarf_only: bool = False
    accepts_debug_format: bool = False

    def matches_magic(self, magic: bytes) -> bool:
        if magic in self.magics:
            return True
        if (
            self.magic_prefix is not None
            and magic[: len(self.magic_prefix)] == self.magic_prefix
        ):
            return True
        return False
