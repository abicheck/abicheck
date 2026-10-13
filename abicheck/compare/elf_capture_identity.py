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

"""Whether both snapshots were parsed by a G23-aware ELF reader.

Shared gate for the ELF detectors in ``diff_platform_elf_dynamic`` and
``diff_platform_elf_symbols``, so neither split imports the other's private.
"""

from __future__ import annotations

from typing import Any


def both_captured_elf_identity(old_elf: Any, new_elf: Any) -> bool:
    """True only when BOTH snapshots were parsed by a G23-aware version.

    Every G23 ELF fact (machine, static-TLS, gnu-property, …) is captured in the
    same parse pass, so a real ELF parsed by current code always has a non-empty
    ``machine``. A legacy snapshot serialized before these fields existed — or a
    header-only / parse-failed side — has machine="" and rehydrates the new
    booleans to their defaults (``has_static_tls=False``, empty gnu_properties),
    which would otherwise read as "the feature was absent" rather than "unknown"
    and fabricate a finding. Gate the new detectors on this so a legacy baseline
    never triggers e.g. a spurious ``static_tls_introduced``.
    """
    return bool(getattr(old_elf, "machine", "") and getattr(new_elf, "machine", ""))
