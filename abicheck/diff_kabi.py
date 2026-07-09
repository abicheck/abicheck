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

"""G23 Phase D1 — Linux kernel kABI (``Module.symvers``) diff detector."""
from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import make_change
from .model import AbiSnapshot


def _is_gpl(export_type: str) -> bool:
    """True if *export_type* is a GPL-restricted ``EXPORT_SYMBOL`` variant.

    The kernel's license gate keys off a ``_GPL`` suffix on the export macro
    (``EXPORT_SYMBOL_GPL``, ``EXPORT_SYMBOL_NS_GPL``); the plain and namespaced
    non-GPL forms are freely linkable by any module. Only the GPL *class*
    determines linkability, so we compare that rather than the raw string —
    otherwise a symbol that merely gains a namespace (``EXPORT_SYMBOL`` →
    ``EXPORT_SYMBOL_NS``) would spuriously read as an export-type change on top
    of the dedicated namespace finding.
    """
    return export_type.endswith("_GPL")


@registry.detector(
    "kabi",
    requires_support=lambda o, n: (
        o.kabi is not None and n.kabi is not None,
        "missing Module.symvers (kABI) metadata",
    ),
)
def _diff_kabi(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Diff two ``Module.symvers`` manifests (D1)."""
    assert old.kabi is not None and new.kabi is not None
    old_e = old.kabi.entries
    new_e = new.kabi.entries
    changes: list[Change] = []

    for sym in sorted(old_e.keys() - new_e.keys()):
        changes.append(
            make_change(ChangeKind.KABI_SYMBOL_REMOVED, symbol=sym, name=sym)
        )
    for sym in sorted(new_e.keys() - old_e.keys()):
        changes.append(
            make_change(ChangeKind.KABI_SYMBOL_ADDED, symbol=sym, name=sym)
        )

    for sym in sorted(old_e.keys() & new_e.keys()):
        o, n = old_e[sym], new_e[sym]
        if o.crc != n.crc:
            changes.append(
                make_change(
                    ChangeKind.KABI_CRC_CHANGED,
                    symbol=sym,
                    name=sym,
                    old=o.crc,
                    new=n.crc,
                )
            )
        # A license-class change only breaks consumers in the *restricting*
        # direction: non-GPL → GPL locks out proprietary modules that could
        # previously link. GPL → non-GPL is a relaxation (widens availability),
        # and a namespace-only change (EXPORT_SYMBOL → EXPORT_SYMBOL_NS) keeps
        # the same GPL class — neither is an availability break, so we flag only
        # the restricting transition and leave the namespace to its own finding.
        if _is_gpl(n.export_type) and not _is_gpl(o.export_type):
            changes.append(
                make_change(
                    ChangeKind.KABI_EXPORT_TYPE_CHANGED,
                    symbol=sym,
                    name=sym,
                    old=o.export_type,
                    new=n.export_type,
                )
            )
        # A gained or changed export namespace requires a matching
        # MODULE_IMPORT_NS() in the consumer; dropping it is compatible.
        if o.namespace != n.namespace and n.namespace:
            changes.append(
                make_change(
                    ChangeKind.KABI_SYMBOL_NAMESPACE_CHANGED,
                    symbol=sym,
                    name=sym,
                    old=o.namespace or "(none)",
                    new=n.namespace,
                )
            )
    return changes
