"""``AbiSnapshot.dump_provenance`` is carried through the document codec.

The ``dump`` write path folds a ``dump_provenance`` block into the document,
and ``load_snapshot`` -> ``save_snapshot`` must keep it. These tests pin the
model-level contract directly, without a compiler: a present block round-trips
through both the flat and the sectioned encodings, an absent one adds no key
to the document, and a non-object one is refused rather than dropped.
"""

from __future__ import annotations

import pytest

from abicheck.errors import SnapshotError
from abicheck.model import AbiSnapshot
from abicheck.serialization import snapshot_from_dict
from abicheck.storage.snapshot_encode import (
    sectioned_document_for_write,
    snapshot_to_dict,
)

_PROVENANCE = {
    "requested_depth": "headers",
    "effective_depth": "headers",
    "degraded": False,
    "frontend": "clang",
    "ast_toolchain": {"castxml_version": "0.7.0"},
}


def _snap(**kwargs: object) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so", version="1.0", **kwargs)  # type: ignore[arg-type]


def test_absent_provenance_adds_no_key_to_the_flat_document() -> None:
    doc = snapshot_to_dict(_snap())
    assert "dump_provenance" not in doc


@pytest.mark.parametrize("encode", ["flat", "sectioned"])
def test_present_provenance_round_trips_through_the_document(encode: str) -> None:
    snap = _snap(dump_provenance=dict(_PROVENANCE))
    doc = (
        snapshot_to_dict(snap)
        if encode == "flat"
        else sectioned_document_for_write(snap)
    )
    loaded = snapshot_from_dict(doc)
    assert loaded.dump_provenance == _PROVENANCE


def test_non_object_provenance_is_refused_not_dropped() -> None:
    doc = snapshot_to_dict(_snap())
    doc["dump_provenance"] = "clang"
    with pytest.raises(SnapshotError, match="dump_provenance must be a JSON object"):
        snapshot_from_dict(doc)
