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

"""The glibc version node every libc on an architecture provides (the ABI baseline).

A ``GLIBC_`` version requirement at or below the architecture's baseline is the
oldest node that every glibc on that architecture carries, so requiring it raises
no deployment floor (``SYMBOL_VERSION_REQUIRED_ADDED_COMPAT``). Only a requirement
above the baseline is a floor.

The table is keyed on the ELF ``e_machine`` string the reader records. Each
entry is the lowest ``GLIBC_`` node of that architecture's libc, checked on this
host with ``readelf -V``:

- ``EM_X86_64``: ``/lib/x86_64-linux-gnu/libc.so.6`` lowest node is ``GLIBC_2.2.5``.
- ``EM_386``: ``/usr/lib32/libc.so.6`` lowest node is ``GLIBC_2.0``.

An architecture missing from the table has no measured baseline and gets no
exemption: every ``GLIBC_`` node is judged against the binary's own floor. That is
the conservative direction (a possible extra floor finding, never a suppressed one).
Other architectures (aarch64, arm, ppc64le, s390x, riscv64, ...) are not in the table
until their libc's lowest node is measured the same way.
"""

from __future__ import annotations

__all__ = ["GLIBC_ARCH_BASELINE", "arch_baseline"]

#: ``e_machine`` -> (baseline version tuple, its tag). ``GLIBC_`` nodes only.
GLIBC_ARCH_BASELINE: dict[str, tuple[tuple[int, ...], str]] = {
    "EM_X86_64": ((2, 2, 5), "GLIBC_2.2.5"),
    "EM_386": ((2, 0), "GLIBC_2.0"),
}


def arch_baseline(machine: str | None) -> tuple[tuple[int, ...], str] | None:
    """The ``GLIBC_`` baseline for *machine*, or ``None`` when it is unmeasured."""
    if machine is None:
        return None
    return GLIBC_ARCH_BASELINE.get(machine)
