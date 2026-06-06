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

"""Workflow-level scenarios for kernel (BTF) and accelerator (SYCL) stacks — G6.

The BTF and SYCL *parsers* are unit-tested elsewhere; these tests drive the
canonical *workflows* end-to-end through the standard ``compare`` + report path
so the use cases are validated as flows, not just parsers:

- **Kernel / eBPF (BTF):** a kernel struct gains a field (the out-of-tree
  "module vs ``vmlinux`` BTF" break). Real BTF bytes are parsed by
  :func:`parse_btf_from_bytes`, converted to the checker's type metadata, and
  run through ``compare`` — the layout detectors fire format-agnostically.
- **SYCL / heterogeneous (PI/UR):** a plugin-interface entrypoint is dropped,
  driven through ``compare`` and the reporter (not just the ``diff_sycl`` unit
  detector).

See docs/development/usecase-coverage-evaluation.md (gap G6) and
docs/user-guide/kernel-btf.md.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from abicheck.btf_metadata import (
    BTF_KIND_INT,
    BTF_KIND_STRUCT,
    BTF_MAGIC,
    BTF_VERSION,
    parse_btf_from_bytes,
)
from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.ctf_metadata import (
    CTF_K_INTEGER,
    CTF_K_STRUCT,
    CTF_MAGIC,
    CTF_VERSION_3,
    parse_ctf_from_bytes,
)
from abicheck.model import AbiSnapshot
from abicheck.reporter import to_json, to_markdown
from abicheck.sycl_metadata import SyclMetadata, SyclPluginInfo

_REPO = Path(__file__).parent.parent

# ── BTF blob builder (minimal, self-contained) ───────────────────────────────


class _BtfBlob:
    """Build a minimal BTF blob: one INT type plus one struct."""

    def __init__(self) -> None:
        self._strings = bytearray(b"\x00")
        self._types: list[bytes] = []
        self._offsets: dict[str, int] = {"": 0}

    def _str(self, s: str) -> int:
        if s in self._offsets:
            return self._offsets[s]
        off = len(self._strings)
        self._strings.extend(s.encode() + b"\x00")
        self._offsets[s] = off
        return off

    def _add(self, name: str, kind: int, vlen: int, size: int, extra: bytes = b"") -> int:
        info = (kind << 24) | (vlen & 0xFFFF)
        self._types.append(struct.pack("<III", self._str(name) if name else 0, info, size) + extra)
        return len(self._types)

    def build_struct(self, struct_name: str, n_fields: int) -> bytes:
        """A struct with ``n_fields`` int members (each 4 bytes / 32 bits)."""
        self._add("int", BTF_KIND_INT, 0, 4, extra=struct.pack("<I", 32))
        members = b""
        for i in range(n_fields):
            members += struct.pack("<III", self._str(f"f{i}"), 1, i * 32)
        self._add(struct_name, BTF_KIND_STRUCT, n_fields, n_fields * 4, extra=members)
        type_data = b"".join(self._types)
        str_data = bytes(self._strings)
        header = struct.pack(
            "<HBBIIIII", BTF_MAGIC, BTF_VERSION, 0, 24,
            0, len(type_data), len(type_data), len(str_data),
        )
        return header + type_data + str_data


def _btf_snapshot(version: str, struct_name: str, n_fields: int) -> AbiSnapshot:
    meta = parse_btf_from_bytes(_BtfBlob().build_struct(struct_name, n_fields))
    return AbiSnapshot(library="vmlinux", version=version, dwarf=meta.to_dwarf_metadata())


# ── Scenario: kernel struct layout change via BTF ─────────────────────────────


def test_btf_struct_gains_field_is_breaking_through_compare() -> None:
    """A kernel struct that gains a field is an out-of-tree-module ABI break and
    flows through ``compare`` as a layout change with the real BTF parser."""
    old = _btf_snapshot("5.10", "task_state", n_fields=2)
    new = _btf_snapshot("5.11", "task_state", n_fields=3)  # gains a field

    result = compare(old, new)

    assert result.verdict is Verdict.BREAKING
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.STRUCT_SIZE_CHANGED in kinds


def test_btf_identical_structs_are_no_change() -> None:
    old = _btf_snapshot("5.10", "task_state", n_fields=3)
    new = _btf_snapshot("5.10", "task_state", n_fields=3)

    result = compare(old, new)
    assert result.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE)
    assert not any(c.kind is ChangeKind.STRUCT_SIZE_CHANGED for c in result.changes)


# ── Scenario: SYCL plugin-interface change ────────────────────────────────────


def _sycl_snapshot(version: str, entry_points: list[str]) -> AbiSnapshot:
    plugin = SyclPluginInfo(
        name="level_zero",
        library="libpi_level_zero.so",
        interface_type="pi",
        pi_version="1.2",
        entry_points=entry_points,
        backend_type="level_zero",
    )
    sycl = SyclMetadata(
        implementation="dpcpp",
        runtime_version="",
        pi_version="1.2",
        plugins=[plugin],
        plugin_search_paths=["/usr/lib/sycl"],
    )
    return AbiSnapshot(library="libsycl.so", version=version, sycl=sycl)


def test_sycl_entrypoint_drop_is_breaking_and_reaches_reports() -> None:
    old = _sycl_snapshot("1", ["piPluginInit", "piPlatformsGet", "piDevicesGet"])
    new = _sycl_snapshot("2", ["piPluginInit", "piPlatformsGet"])  # drops piDevicesGet

    result = compare(old, new)
    assert result.verdict is Verdict.BREAKING
    assert any(c.kind is ChangeKind.SYCL_PI_ENTRYPOINT_REMOVED for c in result.changes)

    # The finding reaches the standard report path (JSON + Markdown), not just
    # the diff_sycl unit detector.
    import json as _json
    payload = _json.loads(to_json(result))
    assert payload["verdict"] == "BREAKING"
    assert "piDevicesGet" in to_markdown(result)


def test_sycl_additive_entrypoint_is_not_breaking() -> None:
    old = _sycl_snapshot("1", ["piPluginInit", "piPlatformsGet"])
    new = _sycl_snapshot("2", ["piPluginInit", "piPlatformsGet", "piDevicesGet"])

    result = compare(old, new)
    assert result.verdict is not Verdict.BREAKING


# ── Scenario: UR (Unified Runtime) adapter — PI's successor ───────────────────


def _ur_snapshot(version: str, entry_points: list[str]) -> AbiSnapshot:
    plugin = SyclPluginInfo(
        name="level_zero",
        library="libur_adapter_level_zero.so",
        interface_type="ur",          # Unified Runtime, not PI
        pi_version="0.10",
        entry_points=entry_points,
        backend_type="level_zero",
    )
    sycl = SyclMetadata(
        implementation="dpcpp",
        runtime_version="",
        pi_version="0.10",
        plugins=[plugin],
        plugin_search_paths=["/usr/lib/sycl"],
    )
    return AbiSnapshot(library="libur_loader.so", version=version, sycl=sycl)


def test_ur_adapter_entrypoint_drop_is_breaking_and_reaches_reports() -> None:
    """A dropped UR adapter entrypoint is a BREAKING SYCL change driven end-to-end
    through compare + the report path — the UR mirror of the PI scenario."""
    old = _ur_snapshot("1", ["urAdapterGet", "urPlatformGet", "urDeviceGet"])
    new = _ur_snapshot("2", ["urAdapterGet", "urPlatformGet"])  # drops urDeviceGet

    result = compare(old, new)
    assert result.verdict is Verdict.BREAKING
    assert any(c.kind is ChangeKind.SYCL_PI_ENTRYPOINT_REMOVED for c in result.changes)

    payload = json.loads(to_json(result))
    assert payload["verdict"] == "BREAKING"
    assert "urDeviceGet" in to_markdown(result)


def test_ur_adapter_additive_entrypoint_is_not_breaking() -> None:
    old = _ur_snapshot("1", ["urAdapterGet", "urPlatformGet"])
    new = _ur_snapshot("2", ["urAdapterGet", "urPlatformGet", "urDeviceGet"])

    result = compare(old, new)
    assert result.verdict is not Verdict.BREAKING


# ── Scenario: kernel CTF struct layout change ─────────────────────────────────


class _CtfBlob:
    """Build a minimal CTF v3 blob: one INT type plus one struct."""

    def __init__(self) -> None:
        self._strings = bytearray(b"\x00")
        self._offsets: dict[str, int] = {"": 0}
        self._types: list[bytes] = []

    def _str(self, s: str) -> int:
        if s in self._offsets:
            return self._offsets[s]
        off = len(self._strings)
        self._strings.extend(s.encode() + b"\x00")
        self._offsets[s] = off
        return off

    def _add(self, name: str, kind: int, vlen: int, size_or_type: int, extra: bytes = b"") -> None:
        info = (kind << 24) | (vlen & 0xFFFF)
        self._types.append(struct.pack("<III", self._str(name) if name else 0, info, size_or_type) + extra)

    def build_struct(self, struct_name: str, n_fields: int) -> bytes:
        self._add("int", CTF_K_INTEGER, 0, 4, extra=struct.pack("<I", 32))
        members = b""
        for i in range(n_fields):
            members += struct.pack("<II", self._str(f"f{i}"), (1 << 16) | (i * 32))
        self._add(struct_name, CTF_K_STRUCT, n_fields, n_fields * 4, extra=members)
        type_data = b"".join(self._types)
        str_data = bytes(self._strings)
        header = struct.pack("<HBB", CTF_MAGIC, CTF_VERSION_3, 0)
        header += struct.pack("<IIIIIIII", 0, 0, 0, 0, 0, 0, len(type_data), len(str_data))
        return header + type_data + str_data


def _ctf_snapshot(version: str, struct_name: str, n_fields: int) -> AbiSnapshot:
    meta = parse_ctf_from_bytes(_CtfBlob().build_struct(struct_name, n_fields))
    return AbiSnapshot(library="vmlinux", version=version, dwarf=meta.to_dwarf_metadata())


def test_ctf_struct_gains_field_is_breaking_through_compare() -> None:
    """CTF mirror of the BTF struct-growth break, driven through compare."""
    old = _ctf_snapshot("1", "task_state", n_fields=2)
    new = _ctf_snapshot("2", "task_state", n_fields=3)

    result = compare(old, new)
    assert result.verdict is Verdict.BREAKING
    assert ChangeKind.STRUCT_SIZE_CHANGED in {c.kind for c in result.changes}


# ── Committed BTF example fixture (examples/case121) ──────────────────────────


def test_committed_btf_example_matches_ground_truth() -> None:
    """The committed examples/case121 BTF blobs reproduce the ground-truth verdict
    through the real parse_btf_from_bytes → compare path (no kernel toolchain)."""
    case = _REPO / "examples" / "case121_kernel_btf_struct_field_added"
    gt = json.loads((_REPO / "examples" / "ground_truth.json").read_text())
    entry = gt["verdicts"]["case121_kernel_btf_struct_field_added"]

    def _snap(blob: str) -> AbiSnapshot:
        meta = parse_btf_from_bytes((case / blob).read_bytes())
        return AbiSnapshot(library="vmlinux", version=blob, dwarf=meta.to_dwarf_metadata())

    result = compare(_snap("v1.btf"), _snap("v2.btf"))
    assert result.verdict.value == entry["expected"]
    emitted = {c.kind.value for c in result.changes}
    for kind in entry["expected_kinds"]:
        assert kind in emitted, f"expected {kind} in {sorted(emitted)}"


def test_resolve_input_ingests_raw_btf_blob() -> None:
    """resolve_input() detects a bare BTF blob by magic and parses it (no ELF)."""
    from abicheck.service import resolve_input
    case = _REPO / "examples" / "case121_kernel_btf_struct_field_added"
    snap = resolve_input(case / "v1.btf")
    assert snap.dwarf is not None and snap.dwarf.has_dwarf
    assert "task_state" in (snap.dwarf.structs or {})


def test_resolve_input_ingests_raw_ctf_blob() -> None:
    """resolve_input() detects a bare CTF blob by magic and parses it."""
    import tempfile

    from abicheck.service import resolve_input
    with tempfile.TemporaryDirectory() as td:
        blob = Path(td) / "types.ctf"
        blob.write_bytes(_CtfBlob().build_struct("task_state", n_fields=2))
        snap = resolve_input(blob)
    assert snap.dwarf is not None and snap.dwarf.has_dwarf
    assert "task_state" in (snap.dwarf.structs or {})


def test_resolve_input_rejects_truncated_btf_blob() -> None:
    """A file with the BTF magic but a truncated/garbage body parses to empty
    metadata; resolve_input must reject it (not accept an empty baseline)."""
    import struct
    import tempfile

    import pytest

    from abicheck.errors import ValidationError
    from abicheck.service import resolve_input
    # BTF magic (little-endian 0xEB9F) + version, then a truncated/garbage body.
    bad = struct.pack("<HBB", BTF_MAGIC, BTF_VERSION, 0) + b"\x00\x00\x00"
    with tempfile.TemporaryDirectory() as td:
        blob = Path(td) / "truncated.btf"
        blob.write_bytes(bad)
        with pytest.raises(ValidationError, match="(?i)detect|format"):
            resolve_input(blob)


def test_resolve_input_rejects_header_only_btf_blob() -> None:
    """A valid BTF header with zero type records (type_len=0) must be rejected,
    not accepted as an empty has_dwarf baseline (Codex P2)."""
    import struct
    import tempfile

    import pytest

    from abicheck.errors import ValidationError
    from abicheck.service import resolve_input
    # 24-byte BTF header with type_off/len=0 and a 1-byte string table — a valid
    # header but no type records.
    header = struct.pack("<HBBIIIII", BTF_MAGIC, BTF_VERSION, 0, 24, 0, 0, 0, 1)
    with tempfile.TemporaryDirectory() as td:
        blob = Path(td) / "header_only.btf"
        blob.write_bytes(header + b"\x00")
        with pytest.raises(ValidationError, match="(?i)detect|format"):
            resolve_input(blob)


def test_resolve_input_non_typeinfo_file_is_not_misdetected() -> None:
    """A plain non-binary file is not mistaken for a BTF/CTF blob."""
    import tempfile

    import pytest

    from abicheck.errors import ValidationError
    from abicheck.service import resolve_input
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "notes.txt"
        f.write_text("just some text, not a type-info blob\n")
        with pytest.raises(ValidationError, match="(?i)detect|format"):
            resolve_input(f)
