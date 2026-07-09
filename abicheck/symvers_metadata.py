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

"""G23 Phase D1 — Linux kernel ``Module.symvers`` (kABI) adapter.

``Module.symvers`` is the canonical Linux kernel-ABI (kABI) manifest that
distro kABI stability guarantees are built on. Each line is a tab-separated
record::

    <CRC>\t<Symbol>\t<Module>\t<Export Type>\t<Namespace>

The fifth ``Namespace`` column was added in kernel 5.4 and may be empty; older
kernels omit it entirely, so both the 4- and 5-field forms are accepted
(kbuild/modules docs). The genksyms ``CRC`` encodes the symbol's *type
signature*: when ``CONFIG_MODVERSIONS`` is on, the loader rejects an
out-of-tree module whose embedded CRC disagrees, so a CRC change is a hard load
break even though the symbol name is unchanged.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class KabiEntry:
    """One ``Module.symvers`` record."""

    crc: str            # e.g. "0x12345678"
    symbol: str
    module: str         # "vmlinux" or a module path
    export_type: str    # EXPORT_SYMBOL / EXPORT_SYMBOL_GPL / EXPORT_SYMBOL_NS[_GPL]
    namespace: str = ""  # 5th column (kernel ≥ 5.4); "" when absent


@dataclass
class KabiMetadata:
    """Parsed ``Module.symvers`` — symbol → entry."""

    entries: dict[str, KabiEntry] = field(default_factory=dict)


def parse_symvers(text: str) -> KabiMetadata:
    """Parse ``Module.symvers`` text into :class:`KabiMetadata`.

    Accepts both the 4-field (pre-5.4) and 5-field (with ``Namespace``) forms.
    Malformed lines are skipped rather than raising, so a partially-valid file
    still yields the records it can.
    """
    meta = KabiMetadata()
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        crc, symbol, module, export_type = parts[0], parts[1], parts[2], parts[3]
        namespace = parts[4] if len(parts) >= 5 else ""
        if not symbol:
            continue
        meta.entries[symbol] = KabiEntry(
            crc=crc.strip(),
            symbol=symbol.strip(),
            module=module.strip(),
            export_type=export_type.strip(),
            namespace=namespace.strip(),
        )
    return meta


def looks_like_symvers(text: str) -> bool:
    """Heuristic: True if *text* is a ``Module.symvers`` manifest.

    Requires at least one line whose first tab field is a hex CRC and whose
    fourth field is an ``EXPORT_SYMBOL`` variant — enough to distinguish it from
    other tab-separated text without a filename hint.
    """
    for raw in text.splitlines():
        parts = raw.rstrip("\n").split("\t")
        if len(parts) < 4:
            continue
        crc, _sym, _mod, export_type = parts[0], parts[1], parts[2], parts[3]
        if crc.startswith("0x") and export_type.startswith("EXPORT_SYMBOL"):
            return True
        # A non-empty, non-comment line that is not a symvers record → not symvers.
        if raw.strip() and not raw.lstrip().startswith("#"):
            return False
    return False


def parse_symvers_file(path: Path) -> KabiMetadata:
    """Read and parse a ``Module.symvers`` file (empty metadata on error)."""
    try:
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                return KabiMetadata()
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return KabiMetadata()
    return parse_symvers(text)
