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

"""A compatible layout conclusion that a config-level layout flag would undo (ADR-095).

At stripped ``{B,H}`` the layout comes from the public headers, parsed under
the default layout configuration. A flag the header cannot carry
(``-fpack-struct=N``, ``-fshort-enums``) changes that layout, and nothing in a
stripped binary records it. Most conclusions do not depend on it; two do,
because they hold only *by virtue of* padding or an ``int``-sized enum:

* a field added to a record whose size did not change (it landed in tail
  padding or an interior hole: packing removes the hole, so the size or an
  offset moves);
* an enum without a fixed underlying type whose value range now needs a wider
  short-enum width (1 -> 2 -> 4 bytes) while its header size is unchanged.

For each, this detector emits ``LAYOUT_BUILD_CONFIG_UNVERIFIED``
(``COMPATIBLE_WITH_RISK``), so the pair cannot read as a clean ``COMPATIBLE``.
It is silent when either side carries DWARF or build-context evidence, or when
the header parse already received a layout flag: then the layout is not
merely assumed.
"""

from __future__ import annotations

from ..detector_registry import registry
from ..diff_helpers import make_change
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.debug_evidence import debug_info_evidence
from ..model.entities import EnumType, RecordType
from ..model.snapshot import AbiSnapshot

__all__ = [
    "LAYOUT_FLAG_PREFIXES",
    "build_config_sensitive_changes",
    "layout_config_unverified",
    "short_enum_width",
]

#: Compile switches that change record or enum layout without the header
#: saying so. A header parse that received one is not assuming the default.
LAYOUT_FLAG_PREFIXES: tuple[str, ...] = (
    "-fpack-struct",
    "-fshort-enums",
    "-fno-short-enums",
    "-mms-bitfields",
    "-mno-ms-bitfields",
    "-malign-double",
    "-mno-align-double",
)

_INT_UNDERLYING = frozenset({"int", "unsigned int", "signed int", "unsigned"})


def _side_unverified(snap: AbiSnapshot) -> bool:
    if not snap.from_headers or debug_info_evidence(snap).any:
        return False
    if snap.parsed_with_build_context:
        return False
    return not any(
        arg.startswith(LAYOUT_FLAG_PREFIXES) for arg in snap.ast_compile_args
    )


def layout_config_unverified(old: AbiSnapshot, new: AbiSnapshot) -> bool:
    """Both sides' layout is header-derived under an assumed default configuration."""
    return _side_unverified(old) and _side_unverified(new)


def short_enum_width(values: list[int]) -> int:
    """Byte width gcc/clang ``-fshort-enums`` picks for an enum holding *values*."""
    if not values:
        return 1
    lo, hi = min(values), max(values)
    for width in (1, 2, 4):
        bits = width * 8
        if (lo >= 0 and hi < (1 << bits)) or (
            lo >= -(1 << (bits - 1)) and hi < (1 << (bits - 1))
        ):
            return width
    return 8


def _record_change(old_rec: RecordType, new_rec: RecordType) -> str | None:
    if old_rec.is_opaque or new_rec.is_opaque or old_rec.kind == "union":
        return None
    if old_rec.size_bits is None or old_rec.size_bits != new_rec.size_bits:
        return None
    old_names = {f.name for f in old_rec.fields}
    added = [f.name for f in new_rec.fields if f.name and f.name not in old_names]
    if not added:
        return None
    return (
        f"field(s) {', '.join(added)} added without a size change "
        f"({old_rec.size_bits // 8} bytes): absorbed by padding that packing removes"
    )


def _enum_change(old_enum: EnumType, new_enum: EnumType) -> str | None:
    if old_enum.is_scoped or new_enum.is_scoped:
        return None  # a scoped enum always has a fixed underlying type
    if (
        old_enum.underlying_type not in _INT_UNDERLYING
        or new_enum.underlying_type != old_enum.underlying_type
    ):
        return None
    old_w = short_enum_width([m.value for m in old_enum.members])
    new_w = short_enum_width([m.value for m in new_enum.members])
    if old_w == new_w:
        return None
    return (
        f"value range needs {new_w} bytes (was {old_w}) under -fshort-enums "
        f"while the header-derived size is unchanged"
    )


def build_config_sensitive_changes(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    if not layout_config_unverified(old, new):
        return []
    changes: list[Change] = []
    old_recs = {r.qualified_name or r.name: r for r in old.declarations.types}
    for new_rec in new.declarations.types:
        key = new_rec.qualified_name or new_rec.name
        old_rec = old_recs.get(key)
        if old_rec is None:
            continue
        why = _record_change(old_rec, new_rec)
        if why:
            changes.append(
                make_change(
                    ChangeKind.LAYOUT_BUILD_CONFIG_UNVERIFIED,
                    symbol=new_rec.name,
                    name=new_rec.name,
                    new=why,
                    qualified_name=key,
                    entity_id=old_rec.entity_id or new_rec.entity_id,
                )
            )
    old_enums = {e.qualified_name or e.name: e for e in old.declarations.enums}
    for new_enum in new.declarations.enums:
        key = new_enum.qualified_name or new_enum.name
        old_enum = old_enums.get(key)
        if old_enum is None:
            continue
        why = _enum_change(old_enum, new_enum)
        if why:
            changes.append(
                make_change(
                    ChangeKind.LAYOUT_BUILD_CONFIG_UNVERIFIED,
                    symbol=new_enum.name,
                    name=new_enum.name,
                    new=why,
                    qualified_name=key,
                    entity_id=old_enum.entity_id or new_enum.entity_id,
                )
            )
    return changes


@registry.detector("layout_build_config")
def _diff_layout_build_config(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    return build_config_sensitive_changes(old, new)
