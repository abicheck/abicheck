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

A ``GLIBC_`` requirement at or below the architecture's baseline is the oldest node
every glibc on that architecture carries, so it raises no deployment floor. A
requirement above it is a floor. An architecture with no measured baseline yields
no answer here: the caller reports the requirement as evidence-limited, never as a
floor (weaker evidence narrows conclusions; it never fabricates a break).

Sources, in glibc's tree (``sysdeps/unix/sysv/linux/<arch>/``): the ``DEFAULT``
line of ``shlib-versions`` is the baseline of libc for that ABI. Fetched from the
glibc master branch on raw.githubusercontent.com (bminor/glibc). i386 has no
``DEFAULT`` line; its lowest node is the first ``GLIBC_`` version in
``sysdeps/unix/sysv/linux/i386/libc.abilist``. PowerPC64 big-endian is not listed:
glibc chooses the baseline from the ELFv1/ELFv2 ABI (``HAVE_ELFV2_ABI``), which
the e_machine and EI_DATA fields do not record.

Key: ``(e_machine, elf_class, ei_data)``; ``None`` for ``ei_data`` means the entry
holds for both byte orders.
"""

from __future__ import annotations

from typing import NamedTuple

__all__ = ["GlibcBaseline", "arch_baseline"]


class GlibcBaseline(NamedTuple):
    version: tuple[int, ...]
    tag: str
    source: str


_X86_64 = "sysdeps/unix/sysv/linux/x86_64/64/shlib-versions"
_I386 = "sysdeps/unix/sysv/linux/i386/libc.abilist (lowest node)"
_AARCH64 = "sysdeps/unix/sysv/linux/aarch64/shlib-versions"
_ARM = "sysdeps/unix/sysv/linux/arm/shlib-versions"
_PPC64 = "sysdeps/unix/sysv/linux/powerpc/powerpc64/shlib-versions (ELFv2 branch)"
_S390 = "sysdeps/unix/sysv/linux/s390/s390-64/shlib-versions"
_RISCV = "sysdeps/unix/sysv/linux/riscv/shlib-versions (lp64 / ilp32 branches)"
_LOONGARCH = "sysdeps/unix/sysv/linux/loongarch/shlib-versions"

#: (e_machine, elf_class, ei_data or None) -> baseline.
GLIBC_ARCH_BASELINE: dict[tuple[str, int, str | None], GlibcBaseline] = {
    ("EM_X86_64", 64, None): GlibcBaseline((2, 2, 5), "GLIBC_2.2.5", _X86_64),
    ("EM_386", 32, None): GlibcBaseline((2, 0), "GLIBC_2.0", _I386),
    ("EM_AARCH64", 64, None): GlibcBaseline((2, 17), "GLIBC_2.17", _AARCH64),
    ("EM_ARM", 32, "LSB"): GlibcBaseline((2, 4), "GLIBC_2.4", _ARM),
    ("EM_PPC64", 64, "LSB"): GlibcBaseline((2, 17), "GLIBC_2.17", _PPC64),
    ("EM_S390", 64, "MSB"): GlibcBaseline((2, 2), "GLIBC_2.2", _S390),
    ("EM_RISCV", 64, None): GlibcBaseline((2, 27), "GLIBC_2.27", _RISCV),
    ("EM_RISCV", 32, None): GlibcBaseline((2, 33), "GLIBC_2.33", _RISCV),
    ("EM_LOONGARCH", 64, None): GlibcBaseline((2, 36), "GLIBC_2.36", _LOONGARCH),
}


def arch_baseline(
    machine: str | None, elf_class: int | None = None, ei_data: str | None = None
) -> GlibcBaseline | None:
    """The ``GLIBC_`` baseline for an architecture, or ``None`` when unmeasured.

    An exact ``ei_data`` entry wins; otherwise the both-orders entry applies.
    """
    if machine is None or elf_class is None:
        return None
    exact = GLIBC_ARCH_BASELINE.get((machine, elf_class, ei_data))
    if exact is not None:
        return exact
    return GLIBC_ARCH_BASELINE.get((machine, elf_class, None))
