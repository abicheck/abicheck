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

"""Characterization tests for ``snapshot_from_dict``'s legacy ``python_ext``
backfill (G14) — written *before* ADR-061 gap E's closure package 5 moves
this backfill out of ``serialization.py`` (Phase 3's rule: pin behavior
before relocating it), so the move can be verified byte-for-byte behavior
preserving regardless of which module ends up owning the derivation.

Covers every branch `snapshot_from_dict` currently takes around the
``python_ext`` key:

- the key absent entirely (a pre-G14 snapshot, or a `dump` path that never
  attached it) with binary metadata present -> re-derive via
  ``detect_python_extension``;
- the key absent with *no* binary metadata at all -> stays ``None`` (nothing
  to derive from);
- the key present as an explicit ``null`` (the dumper checked and answered
  "not an extension") -> stays ``None``, never re-derived;
- the key present with a real value -> the persisted value is used as-is,
  never re-derived even if it disagrees with what re-detection would say;
- the Mach-O ``imported_symbols`` caveat: a legacy Mach-O snapshot with no
  captured import table must not derive a (misleadingly empty) extension
  surface, even though the key is otherwise absent and ELF/PE would
  normally trigger a derivation;
- the same Mach-O snapshot once ``imported_symbols`` -- even an empty list --
  is present, derivation resumes normally.
"""

from __future__ import annotations

import copy

from abicheck.elf_metadata import (
    ElfImport,
    ElfMetadata,
    ElfSymbol,
    SymbolBinding,
    SymbolType,
)
from abicheck.macho_metadata import MachoMetadata
from abicheck.model import AbiSnapshot
from abicheck.python_ext import detect_python_extension
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict
from abicheck.storage.sectioned_document import to_sectioned_document


def _extension_snapshot() -> AbiSnapshot:
    elf = ElfMetadata(
        symbols=[
            ElfSymbol(
                name="PyInit_foo",
                binding=SymbolBinding.GLOBAL,
                sym_type=SymbolType.FUNC,
            )
        ],
        imports=[
            ElfImport(
                name="PyList_New",
                binding=SymbolBinding.GLOBAL,
                sym_type=SymbolType.FUNC,
            )
        ],
    )
    snap = AbiSnapshot(
        library="foo", version="1.0", elf=elf, source_path="foo.cpython-313.so"
    )
    snap.python_ext = detect_python_extension(snap)
    assert snap.python_ext is not None
    assert snap.python_ext.is_extension
    return snap


def test_missing_key_with_elf_metadata_is_backfilled() -> None:
    snap = _extension_snapshot()
    d = snapshot_to_dict(snap)
    assert "python_ext" in d
    del d["python_ext"]

    reloaded = snapshot_from_dict(d)

    assert reloaded.python_ext is not None
    assert reloaded.python_ext.is_extension
    assert reloaded.python_ext.module_name == snap.python_ext.module_name
    assert reloaded.python_ext.init_symbol == snap.python_ext.init_symbol


def test_missing_key_with_no_binary_metadata_stays_none() -> None:
    snap = AbiSnapshot(library="plainlib", version="1.0")
    d = snapshot_to_dict(snap)
    d.pop("python_ext", None)

    reloaded = snapshot_from_dict(d)

    assert reloaded.python_ext is None


def test_explicit_null_is_never_re_derived() -> None:
    snap = _extension_snapshot()
    d = snapshot_to_dict(snap)
    # Explicit null: the dumper checked and recorded "not an extension".
    d["python_ext"] = None

    reloaded = snapshot_from_dict(d)

    assert reloaded.python_ext is None


def test_present_value_is_used_verbatim_not_re_derived() -> None:
    snap = _extension_snapshot()
    d = snapshot_to_dict(snap)
    # Corrupt the persisted value so it disagrees with what re-detection
    # from the ELF metadata would say -- the loader must trust the
    # persisted key, not silently override it.
    d["python_ext"] = copy.deepcopy(d["python_ext"])
    d["python_ext"]["module_name"] = "not_the_real_module"

    reloaded = snapshot_from_dict(d)

    assert reloaded.python_ext is not None
    assert reloaded.python_ext.module_name == "not_the_real_module"


def test_macho_missing_imported_symbols_key_suppresses_backfill() -> None:
    snap = _extension_snapshot()
    snap.elf = None
    snap.macho = MachoMetadata(install_name="@rpath/foo.so")
    snap.python_ext = detect_python_extension(snap)
    d = snapshot_to_dict(snap)
    del d["python_ext"]
    # Simulate a pre-G14 Mach-O snapshot: the imports table itself didn't
    # exist yet, so it's absent from the persisted document (not merely
    # empty).
    assert isinstance(d["macho"], dict)
    del d["macho"]["imported_symbols"]

    reloaded = snapshot_from_dict(d)

    assert reloaded.python_ext is None


def test_macho_with_imported_symbols_key_present_backfills_normally() -> None:
    snap = _extension_snapshot()
    snap.elf = None
    snap.macho = MachoMetadata(
        install_name="@rpath/foo.so", imported_symbols=["PyList_New"]
    )
    snap.python_ext = detect_python_extension(snap)
    d = snapshot_to_dict(snap)
    del d["python_ext"]
    assert isinstance(d["macho"], dict)
    assert "imported_symbols" in d["macho"]

    reloaded = snapshot_from_dict(d)

    assert reloaded.python_ext is not None


# ADR-061 gap E regression (Codex review, PR #1214): `snapshot_from_dict`'s
# split into `storage.snapshot_codec.decode_snapshot` (which unwraps a
# sectioned document *locally*, never propagating the flat shape back to its
# caller) plus the facade's own `backfill_python_ext_from_evidence` call
# briefly passed the *sectioned* envelope to that backfill instead of the
# flat document it requires -- silently defeating both of its own dict-shape
# checks (`"python_ext" not in d`, `d.get("macho")`) for every snapshot
# written since the sectioned shape became the on-disk default
# (ADR-062/063 Phase 8). These two tests are the sectioned-document
# counterparts of `test_explicit_null_is_never_re_derived` and
# `test_macho_missing_imported_symbols_key_suppresses_backfill` above --
# same assertions, but routed through `to_sectioned_document` first so a
# reader that skips the unwrap-before-backfill step fails here even though
# the flat-document tests above cannot see the bug at all.
def _sectioned(d: dict) -> dict:
    return to_sectioned_document(d, max_known_schema_version=SCHEMA_VERSION)


def test_explicit_null_is_never_re_derived_from_a_sectioned_document() -> None:
    snap = _extension_snapshot()
    d = snapshot_to_dict(snap)
    d["python_ext"] = None

    reloaded = snapshot_from_dict(_sectioned(d))

    assert reloaded.python_ext is None


def test_macho_missing_imported_symbols_key_suppresses_backfill_from_a_sectioned_document() -> (
    None
):
    snap = _extension_snapshot()
    snap.elf = None
    snap.macho = MachoMetadata(install_name="@rpath/foo.so")
    snap.python_ext = detect_python_extension(snap)
    d = snapshot_to_dict(snap)
    del d["python_ext"]
    assert isinstance(d["macho"], dict)
    del d["macho"]["imported_symbols"]

    reloaded = snapshot_from_dict(_sectioned(d))

    assert reloaded.python_ext is None
