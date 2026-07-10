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
"""Binary↔header/source AST symbol-mapping audit (validation/uxl-plugin-source-scan-2026-07.md).

Answers one question for a shipped shared object + its public headers: *does every
exported dynamic symbol map to a declaration abicheck parsed from the public
headers?* The unmapped remainder is the accidental-ABI / leak surface a plugin
source scan is meant to catch.

The authoritative matcher is abicheck's own ``exported_not_public`` cross-check run
with the per-check cap disabled (``max_per_check=0``) — it drives from the binary
export table and normalises Itanium constructor/destructor variants (C1/C2/C3,
D0/D1/D2) and ABI-tag drift, which a naive mangled-name string compare misses (on
oneDAL that difference is ~4000 symbols). Each unmapped symbol is bucketed so a
genuine resolver gap is distinguishable from a legitimately non-public export
(leaked libstdc++/{fmt}, internal namespace, RTTI for an internal type, explicit
template instantiation, interop entry needing an unshipped SDK header).

Pure measurement over an existing dump snapshot + the ``.so``; no network. Fetch
the artifacts however you like (conda-forge, distro packages, a local build) — the
companion markdown documents the exact conda-forge builds used for the UXL run.

Usage
-----
    # 1. dump the library's L2 surface (clang backend shown; castxml also works)
    ABICHECK_AST_FRONTEND=clang abicheck dump libfoo.so.1 \\
        -H include/foo/umbrella.hpp -I include --lang c++ \\
        --public-header-dir include -o foo.abi.json

    # 2. audit the mapping
    python validation/scripts/symbol_mapping_audit.py foo foo.abi.json libfoo.so.1
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter

from abicheck.buildsource.crosscheck import CrosscheckConfig, run_crosschecks
from abicheck.serialization import load_snapshot


def binary_export_count(so: str) -> int:
    """Count defined GLOBAL/WEAK FUNC|OBJECT dynamic symbols (the exported ABI)."""
    proc = subprocess.run(
        ["readelf", "--dyn-syms", "-W", so], capture_output=True, text=True, check=False
    )
    n = 0
    for line in proc.stdout.splitlines():
        f = line.split()
        if len(f) < 8 or not f[0].endswith(":"):
            continue
        if f[3] in ("FUNC", "OBJECT") and f[4] in ("GLOBAL", "WEAK") and f[6] != "UND":
            n += 1
    return n


def classify_unmapped(sym: str) -> str:
    """Bucket an unmapped export so a resolver gap is separable from a non-public one."""
    if sym.startswith(
        ("_ZNSt", "_ZSt", "_ZNKSt", "_ZGVZNKSt", "_ZTVSt", "_ZTISt", "_ZNVSt")
    ):
        return "leaked-libstdc++"
    if "3fmt" in sym:
        return "leaked-fmt"
    if "4impl" in sym or "8internal" in sym or "6detail" in sym or "::impl::" in sym:
        return "internal-namespace"
    if sym.startswith(("_ZTS", "_ZTI", "_ZTV", "_ZTh", "_ZTv", "_ZTc")):
        return "rtti/vtable"
    if not sym.startswith("_Z"):
        return "C-symbol"
    return "cpp-other"


def audit(name: str, snapshot_path: str, so: str) -> dict[str, object]:
    snap = load_snapshot(snapshot_path)
    # max_per_check=0 disables the anti-flood cap so we see the *true* count.
    result = run_crosschecks(snap, CrosscheckConfig(max_per_check=0))
    enp = [c for c in result.findings if c.kind.value == "exported_not_public"]
    pne = [c for c in result.findings if c.kind.value == "public_not_exported"]
    exports = binary_export_count(so)
    unmapped = len(enp)
    mapped = exports - unmapped
    return {
        "project": name,
        "binary_exports": exports,
        "mapped_by_abicheck": mapped,
        "unmapped_exported_not_public": unmapped,
        "mapping_pct": round(100 * mapped / max(1, exports), 2),
        "public_not_exported": len(pne),
        "unmapped_classes": dict(Counter(classify_unmapped(c.symbol) for c in enp)),
        "unmapped_sample": [c.symbol for c in enp[:25]],
    }


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit(
            "usage: symbol_mapping_audit.py <name> <snapshot.abi.json> <library.so>"
        )
    out = audit(sys.argv[1], sys.argv[2], sys.argv[3])
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
