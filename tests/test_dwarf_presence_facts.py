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

"""``dwarf_presence``: cheap section probes as ``Fact[bool]`` producers."""

from __future__ import annotations

import pytest

from abicheck.model import Fact, FactStatus


def test_cheap_debug_presence_honors_forced_btf(monkeypatch, tmp_path):
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "vmlinux"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr(
        "abicheck.dwarf_presence._has_btf", lambda _p: Fact.present(True)
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence._has_ctf", lambda _p: Fact.present(False)
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_dwarf_presence_metadata",
        lambda _p: pytest.fail("forced BTF must not probe DWARF first"),
    )

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(
        so_path,
        debug_format="btf",
    )

    assert dwarf_meta.has_dwarf is True
    assert dwarf_adv.has_dwarf is True


def test_cheap_debug_presence_honors_forced_dwarf(monkeypatch, tmp_path):
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata
    from abicheck.dwarf_metadata import DwarfMetadata
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_dwarf_presence_metadata",
        lambda _p: (
            DwarfMetadata(has_dwarf=True),
            AdvancedDwarfMetadata(has_dwarf=True),
        ),
    )

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(
        so_path,
        debug_format="dwarf",
    )

    assert dwarf_meta.has_dwarf is True
    assert dwarf_adv.has_dwarf is True


def test_cheap_debug_presence_honors_forced_ctf(monkeypatch, tmp_path):
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr(
        "abicheck.dwarf_presence._has_ctf", lambda _p: Fact.present(True)
    )

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(
        so_path,
        debug_format="ctf",
    )

    assert dwarf_meta.has_dwarf is True
    assert dwarf_adv.has_dwarf is True


def test_cheap_debug_presence_rejects_unknown_format(tmp_path):
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")

    with pytest.raises(ValueError, match="Invalid debug_format"):
        cheap_debug_presence_metadata(so_path, debug_format="split-dwarf")


def test_cheap_debug_presence_auto_prefers_kernel_btf(monkeypatch, tmp_path):
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "vmlinux"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr(
        "abicheck.dwarf_presence._is_kernel_binary", lambda _p: Fact.present(True)
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence._has_btf", lambda _p: Fact.present(True)
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence._has_ctf", lambda _p: Fact.present(False)
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_dwarf_presence_metadata",
        lambda _p: pytest.fail("kernel BTF should be selected before DWARF"),
    )

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(so_path)

    assert dwarf_meta.has_dwarf is True
    assert dwarf_adv.has_dwarf is True


def test_cheap_debug_presence_auto_prefers_dwarf_when_present(monkeypatch, tmp_path):
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata
    from abicheck.dwarf_metadata import DwarfMetadata
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr(
        "abicheck.dwarf_presence._is_kernel_binary", lambda _p: Fact.present(False)
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_dwarf_presence_metadata",
        lambda _p: (
            DwarfMetadata(has_dwarf=True),
            AdvancedDwarfMetadata(has_dwarf=True),
        ),
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence._has_btf", lambda _p: pytest.fail("DWARF wins")
    )

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(so_path)

    assert dwarf_meta.has_dwarf is True
    assert dwarf_adv.has_dwarf is True


def test_cheap_debug_presence_auto_falls_back_to_btf(monkeypatch, tmp_path):
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata
    from abicheck.dwarf_metadata import DwarfMetadata
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr(
        "abicheck.dwarf_presence._is_kernel_binary", lambda _p: Fact.present(False)
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_dwarf_presence_metadata",
        lambda _p: (
            DwarfMetadata(has_dwarf=False),
            AdvancedDwarfMetadata(has_dwarf=False),
        ),
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence._has_btf", lambda _p: Fact.present(True)
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence._has_ctf", lambda _p: pytest.fail("BTF wins")
    )

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(so_path)

    assert dwarf_meta.has_dwarf is True
    assert dwarf_adv.has_dwarf is True


def test_cheap_debug_presence_auto_falls_back_to_ctf(monkeypatch, tmp_path):
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata
    from abicheck.dwarf_metadata import DwarfMetadata
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr(
        "abicheck.dwarf_presence._is_kernel_binary", lambda _p: Fact.present(False)
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_dwarf_presence_metadata",
        lambda _p: (
            DwarfMetadata(has_dwarf=False),
            AdvancedDwarfMetadata(has_dwarf=False),
        ),
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence._has_btf", lambda _p: Fact.present(False)
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence._has_ctf", lambda _p: Fact.present(True)
    )

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(so_path)

    assert dwarf_meta.has_dwarf is True
    assert dwarf_adv.has_dwarf is True


def test_cheap_debug_presence_returns_empty_when_no_debug(monkeypatch, tmp_path):
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata
    from abicheck.dwarf_metadata import DwarfMetadata
    from abicheck.dwarf_presence import cheap_debug_presence_metadata

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr(
        "abicheck.dwarf_presence._is_kernel_binary", lambda _p: Fact.present(False)
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_dwarf_presence_metadata",
        lambda _p: (
            DwarfMetadata(has_dwarf=False),
            AdvancedDwarfMetadata(has_dwarf=False),
        ),
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence._has_btf", lambda _p: Fact.present(False)
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence._has_ctf", lambda _p: Fact.present(False)
    )

    dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(so_path)

    assert dwarf_meta.has_dwarf is False
    assert dwarf_adv.has_dwarf is False


def test_cheap_debug_presence_helpers_treat_probe_errors_as_unknown(
    monkeypatch, tmp_path
):
    import abicheck.btf_metadata as btf_metadata
    import abicheck.ctf_metadata as ctf_metadata
    from abicheck import dwarf_presence

    so_path = tmp_path / "not-elf.so"
    so_path.write_bytes(b"not an elf")

    monkeypatch.setattr(
        btf_metadata,
        "has_btf_section",
        lambda _p: (_ for _ in ()).throw(RuntimeError("btf probe failed")),
    )
    monkeypatch.setattr(
        ctf_metadata,
        "has_ctf_section",
        lambda _p: (_ for _ in ()).throw(RuntimeError("ctf probe failed")),
    )

    assert dwarf_presence._has_btf(so_path).status is FactStatus.FAILED
    assert dwarf_presence._has_ctf(so_path).status is FactStatus.FAILED
    assert dwarf_presence._is_kernel_binary(so_path).status is FactStatus.FAILED


def test_cheap_debug_presence_failed_probe_is_not_absent(monkeypatch, tmp_path):
    """A failed CTF probe next to a clean BTF negative merges to unknown
    (evidence_merge), which the presence flag reads as "no evidence in hand"."""
    from abicheck.dwarf_advanced import AdvancedDwarfMetadata
    from abicheck.dwarf_metadata import DwarfMetadata
    from abicheck.dwarf_presence import cheap_debug_presence_metadata
    from abicheck.model.evidence_merge import merge_presence

    so_path = tmp_path / "lib.so"
    so_path.write_bytes(b"\x7fELF")
    monkeypatch.setattr(
        "abicheck.dwarf_presence._is_kernel_binary", lambda _p: Fact.present(False)
    )
    monkeypatch.setattr(
        "abicheck.dwarf_presence.cheap_dwarf_presence_metadata",
        lambda _p: (
            DwarfMetadata(has_dwarf=False),
            AdvancedDwarfMetadata(has_dwarf=False),
        ),
    )
    btf, ctf = Fact.present(False), Fact.failed("ctf probe failed")
    monkeypatch.setattr("abicheck.dwarf_presence._has_btf", lambda _p: btf)
    monkeypatch.setattr("abicheck.dwarf_presence._has_ctf", lambda _p: ctf)
    assert merge_presence(btf, ctf).status is FactStatus.FAILED
    dwarf_meta, _ = cheap_debug_presence_metadata(so_path)
    assert dwarf_meta.has_dwarf is False


@pytest.mark.parametrize("payload", [b"", b"\x7fELF", b"not an elf at all"])
def test_unreadable_elf_is_failed_not_absent(tmp_path, payload):
    """Real (unpatched) probes over an unparsable file: FAILED, never PRESENT(False)."""
    from abicheck import dwarf_presence

    so_path = tmp_path / "broken.so"
    so_path.write_bytes(payload)
    for probe in (
        dwarf_presence._has_btf,
        dwarf_presence._has_ctf,
        dwarf_presence._is_kernel_binary,
        dwarf_presence._has_dwarf,
    ):
        fact = probe(so_path)
        assert fact.status is FactStatus.FAILED, (probe.__name__, fact)
