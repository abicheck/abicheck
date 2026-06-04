# Copyright 2026 Nikolay Petrov
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

"""libuv private implementation-type churn (ISSUE-30/35/65).

The conda-forge campaign flagged libuv 1.5x comparisons as BREAKING from a mix
of findings. Splitting them by what they actually mean for binary ABI:

* ``uv_cpu_info_s::model`` ``char *`` -> ``const char *`` — a pointee-const
  change, binary-ABI-neutral (handled by the const-pointer fix; covered here).
* ``uv_tcp_keepalive`` parameter *rename* — a source/API-signature change, not a
  binary break. abicheck already classifies ``PARAM_RENAMED`` as ``API_BREAK``
  (source), which produces a source-break verdict, NOT a hard binary
  ``BREAKING`` one. This test pins that distinction.

The remaining libuv findings in that campaign (``uv__io_s::cb`` field removal,
``uv__io_cb`` typedef removal) are genuine struct-layout changes that libabigail
also reports as reachable subtype changes through exported functions, so they
are correctly breaking and are intentionally NOT suppressed.
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dwarf_metadata import DwarfMetadata, FieldInfo, StructLayout
from abicheck.model import AbiSnapshot, Function, Param, Visibility


def _fn(name: str, params: list[Param]) -> Function:
    return Function(name=name, mangled=name, return_type="int",
                    params=params, visibility=Visibility.PUBLIC)


def test_param_rename_is_source_level_not_binary_breaking():
    old = AbiSnapshot(library="libuv.so.1", version="1",
                      functions=[_fn("uv_tcp_keepalive", [Param(name="enable", type="int")])])
    new = AbiSnapshot(library="libuv.so.1", version="2",
                      functions=[_fn("uv_tcp_keepalive", [Param(name="on", type="int")])])
    r = compare(old, new)
    assert ChangeKind.PARAM_RENAMED in {c.kind for c in r.changes}
    # Source-level break, not a hard binary ABI break.
    assert r.verdict == Verdict.API_BREAK
    assert r.verdict != Verdict.BREAKING


def test_private_struct_field_pointee_const_change_is_neutral():
    # uv_cpu_info_s::model char* -> const char* under stable struct layout.
    old_s = StructLayout(name="uv_cpu_info_s", byte_size=24, fields=[
        FieldInfo(name="model", type_name="char *", byte_offset=0, byte_size=8),
        FieldInfo(name="speed", type_name="int", byte_offset=8, byte_size=4),
    ])
    new_s = StructLayout(name="uv_cpu_info_s", byte_size=24, fields=[
        FieldInfo(name="model", type_name="const char *", byte_offset=0, byte_size=8),
        FieldInfo(name="speed", type_name="int", byte_offset=8, byte_size=4),
    ])
    old = AbiSnapshot(library="libuv.so.1", version="1")
    new = AbiSnapshot(library="libuv.so.1", version="2")
    old.dwarf = DwarfMetadata(has_dwarf=True, structs={"uv_cpu_info_s": old_s})  # type: ignore[attr-defined]
    new.dwarf = DwarfMetadata(has_dwarf=True, structs={"uv_cpu_info_s": new_s})  # type: ignore[attr-defined]
    r = compare(old, new)
    assert ChangeKind.STRUCT_FIELD_TYPE_CHANGED not in {c.kind for c in r.changes}
    assert r.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE)
