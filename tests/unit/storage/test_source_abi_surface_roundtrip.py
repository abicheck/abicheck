# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""``SourceAbiSurface`` round trip: decoding keeps the keys a document states.

The linked surface (``source/source_abi.json``) is not part of the
``AbiSnapshot`` dataclass graph, so :mod:`test_snapshot_field_roundtrip` cannot
enumerate it. Its ``roots``, ``mappings`` and ``unmatched`` maps used to be
rebuilt with every default key filled in, so a document an older producer
wrote without, say, ``forced_public`` re-encoded with ``forced_public: []``
that the producer never wrote. Readers use ``.get(key, default)``, so the
absent key and the empty one read the same; the re-encoded document did not.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from abicheck.buildsource.source_abi import SourceAbiSurface

_ROOT_KEYS = ("exported_symbols", "public_header_declarations", "forced_public")
_MAPPING_KEYS = (
    "source_decl_to_binary_symbol",
    "source_type_to_debug_type",
    "public_header_to_target",
    "synthesized_symbol_to_owner",
    "template_instantiation_symbol_to_decl",
    "allocator_interposer_symbol_to_owner",
    "non_public_symbol_to_reason",
)
_UNMATCHED_KEYS = ("symbols_without_decl", "decls_without_symbol")


def _older_document() -> dict[str, Any]:
    """A surface written before ``forced_public`` and two mapping keys existed."""
    return {
        "schema_version": 1,
        "library": "libold.so",
        "target_id": "t",
        "roots": {
            "exported_symbols": ["f"],
            "public_header_declarations": ["ns::f"],
        },
        "mappings": {
            "source_decl_to_binary_symbol": {"ns::f": "f"},
            "public_header_to_target": {"f.h": "t"},
        },
        "unmatched": {"symbols_without_decl": []},
        "coverage": {},
    }


@pytest.mark.parametrize("section", ["roots", "mappings", "unmatched"])
def test_absent_keys_stay_absent_on_reencode(section: str) -> None:
    doc = _older_document()
    out = SourceAbiSurface.from_dict(copy.deepcopy(doc)).to_dict()
    assert out[section] == doc[section]


def test_absent_keys_are_not_read_back_as_defaults() -> None:
    surface = SourceAbiSurface.from_dict(_older_document())
    assert "forced_public" not in surface.roots
    assert "source_type_to_debug_type" not in surface.mappings
    assert "decls_without_symbol" not in surface.unmatched
    # Readers still see the empty default through `.get`, which is how
    # `source_diff` and the assurance rollup consume these maps.
    assert surface.roots.get("forced_public", []) == []
    assert surface.mappings.get("source_type_to_debug_type", {}) == {}


def test_fresh_surface_still_encodes_every_key() -> None:
    # Only a decoded document is canonicalized; an object the linker builds
    # keeps the full key set, so a new producer's output is unchanged.
    out = SourceAbiSurface().to_dict()
    assert tuple(out["roots"]) == _ROOT_KEYS
    assert tuple(out["mappings"]) == _MAPPING_KEYS
    assert tuple(out["unmatched"]) == _UNMATCHED_KEYS


def test_full_document_is_a_fixed_point() -> None:
    full = SourceAbiSurface().to_dict()
    once = SourceAbiSurface.from_dict(copy.deepcopy(full)).to_dict()
    twice = SourceAbiSurface.from_dict(copy.deepcopy(once)).to_dict()
    assert once == full
    assert twice == once
