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

"""ADR-063 Phase 0 (Codex review, PR #909): dumper_hybrid.merge_snapshots's
RecordType layout backfill must carry a Fact[T] sibling's real status
alongside the legacy scalar it backfills, not let replace_with_fact_sync's
default derivation silently promote it to Fact.present(...).

Split out of test_dumper_hybrid.py, which is at its AI-readiness file-size
debt budget -- this file exists purely so this one regression case has
somewhere to live without growing that one further.
"""

from __future__ import annotations

from abicheck.dumper_hybrid import merge_snapshots
from abicheck.model import AbiSnapshot, Fact, RecordType


def _snap(types=None, **kwargs):
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        types=types or [],
        from_headers=True,
        **kwargs,
    )


def test_vptr_offset_bits_fact_carries_clangs_real_status() -> None:
    # Backfilling vptr_offset_bits from clang_t must carry clang_t's own
    # Fact status -- else replace_with_fact_sync's default derivation
    # silently promotes clang's real Fact.partial(...) (the Itanium
    # primary-base heuristic caveat) to a confirmed Fact.present(...) it
    # never became.
    t_old = RecordType(name="Widget", kind="class", size_bits=64, vptr_offset_bits=None)
    t_clang = RecordType(
        name="Widget",
        kind="class",
        size_bits=64,
        vptr_offset_bits=0,
        vptr_offset_bits_fact=Fact.partial(0),
    )
    castxml = _snap(types=[t_old], ast_producer="castxml")
    clang = _snap(types=[t_clang], ast_producer="clang")
    merged_t = merge_snapshots(castxml, clang).type_by_name("Widget")
    assert merged_t.vptr_offset_bits == 0
    assert merged_t.vptr_offset_bits_fact == Fact.partial(0)
