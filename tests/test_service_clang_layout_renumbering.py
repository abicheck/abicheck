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

"""``service.run_dump``'s ELF/PE/Mach-O tails must renumber closure
identities exactly once, AFTER ``attach_clang_layout`` runs -- not before.

Reported (Codex review, fresh evidence): ``_dump_elf``'s own ``dumper.dump()``
already renumbers a snapshot's closure markers to stable ``#N`` form before
returning. ``attach_clang_layout`` then runs the G28 layout tool afterward,
which independently derives a base class's name straight from clang's own
(still ``:line:col``-form) spelling and inserts it into
``RecordType.base_offsets`` -- so a closure-parameterized base's offset
landed in ``base_offsets`` keyed by the pre-renumber spelling while
``RecordType.bases`` itself already carried the post-renumber ``#N`` one.
``_check_base_offsets()`` does an exact key lookup, so old/new snapshots
could never join on that key, silently missing a real base-offset ABI
change.

Fixed by deferring renumbering (``qualified_name_segments.
defer_closure_identity_renumbering()``) across the whole ``_dump_elf``/
``_dump_pe``/``_dump_macho`` + ``attach_clang_layout`` sequence, then
renumbering exactly once at the very end -- the same pattern
``run_dump``'s hybrid recursion already uses for the identical reason
(two independent sub-dumps must not each assign a closure its own,
possibly-different ordinal before a later merge/enrichment step).
"""

from __future__ import annotations

from unittest.mock import patch

from abicheck.model import AbiSnapshot, RecordType
from abicheck.qualified_name_segments import renumber_anonymous_closure_identities
from abicheck.service import run_dump

_RAW_BASE = "Base<(lambda:x.h:1:2)>"


def _fake_dump(*_args, **_kwargs) -> AbiSnapshot:
    """Mirrors dumper.dump()'s own final renumbering call -- a no-op when
    deferred (the fix), a real premature renumbering when not (the bug)."""
    snap = AbiSnapshot(
        library="test",
        version="1.0",
        from_headers=True,
        types=[RecordType(name="Owner", kind="class", bases=[_RAW_BASE])],
    )
    return renumber_anonymous_closure_identities(snap)


def _fake_attach_clang_layout(snap: AbiSnapshot, *_args, **_kwargs) -> AbiSnapshot:
    """Mirrors clang_layout_tool._apply_record_facts: the tool's own base
    name, independent of whatever this snapshot's bases entry currently
    spells it as."""
    snap.types[0].base_offsets = {_RAW_BASE: 64}
    return snap


class TestRunDumpRenumbersAfterClangLayoutAttach:
    def test_elf_base_offsets_and_bases_agree_on_ordinal_spelling(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)

        with (
            patch("abicheck.service_dump_native._dump_elf", side_effect=_fake_dump),
            patch(
                "abicheck.service_dump_native._attach_header_graph",
                side_effect=lambda s, *_a, **_k: s,
            ),
            patch(
                "abicheck.service_dump_native.attach_clang_layout",
                side_effect=_fake_attach_clang_layout,
            ),
        ):
            result = run_dump(p, "elf", header_backend="clang")

        rec = result.types[0]
        assert list(rec.base_offsets) == rec.bases
        assert rec.bases == ["Base<(lambda:x.h#1)>"]

    def test_pe_base_offsets_and_bases_agree_on_ordinal_spelling(self, tmp_path):
        """Same mismatch, same fix, on the PE/Mach-O shared tail
        (_finish_native_snapshot)."""
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)

        with (
            patch("abicheck.service_dump_native._dump_pe", side_effect=_fake_dump),
            patch(
                "abicheck.service_dump_native._attach_header_graph",
                side_effect=lambda s, *_a, **_k: s,
            ),
            patch(
                "abicheck.service_dump_native.attach_clang_layout",
                side_effect=_fake_attach_clang_layout,
            ),
        ):
            result = run_dump(p, "pe", header_backend="clang")

        rec = result.types[0]
        assert list(rec.base_offsets) == rec.bases
        assert rec.bases == ["Base<(lambda:x.h#1)>"]

    def test_macho_base_offsets_and_bases_agree_on_ordinal_spelling(self, tmp_path):
        """Same mismatch, same fix, on the Mach-O side of the same shared
        tail."""
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 100)

        with (
            patch("abicheck.service_dump_native._dump_macho", side_effect=_fake_dump),
            patch(
                "abicheck.service_dump_native._attach_header_graph",
                side_effect=lambda s, *_a, **_k: s,
            ),
            patch(
                "abicheck.service_dump_native.attach_clang_layout",
                side_effect=_fake_attach_clang_layout,
            ),
        ):
            result = run_dump(p, "macho", header_backend="clang")

        rec = result.types[0]
        assert list(rec.base_offsets) == rec.bases
        assert rec.bases == ["Base<(lambda:x.h#1)>"]
