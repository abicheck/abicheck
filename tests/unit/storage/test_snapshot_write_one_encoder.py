# SPDX-License-Identifier: Apache-2.0
"""Every snapshot write formats through one encoder.

``write_snapshot`` streams plain, gzip and size-less zstd writes through
``json_stream.iter_json_indented``; the default zstd write encodes in one
shot so the frame can declare its size. That branch used to format with
``json.dumps`` -- a second formatting path kept equal only by the stream
encoder's own equivalence tests. It now joins the same stream. The oracle
here is ``json.dumps(sectioned_document_for_write(snap), indent=2)``, the
encoding every path must decode to, checked across all four write modes.
"""

from __future__ import annotations

import json

import pytest

from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.serialization import write_snapshot
from abicheck.snapshot_io import read_snapshot_text
from abicheck.storage.snapshot_encode import sectioned_document_for_write


def _snapshot(n: int) -> AbiSnapshot:
    return AbiSnapshot(
        library="libone.so",
        version="1.0",
        functions=[
            Function(
                name=f"f{i}",
                mangled=f"_Z2f{i}i",
                return_type="int",
                params=[Param(name="x", type="int")],
                visibility=Visibility.PUBLIC,
            )
            for i in range(n)
        ],
    )


@pytest.mark.parametrize("n", [0, 1, 40])
@pytest.mark.parametrize(
    ("compression", "suffix", "content_size"),
    [
        ("none", ".json", True),
        ("gzip", ".json.gz", True),
        ("zstd", ".json.zst", True),
        ("zstd", ".json.zst", False),
    ],
)
def test_every_write_mode_decodes_to_the_one_encoding(
    tmp_path, n, compression, suffix, content_size
) -> None:
    snap = _snapshot(n)
    expected = json.dumps(sectioned_document_for_write(snap), indent=2)
    path = tmp_path / f"snap{suffix}"
    write_snapshot(snap, path, compression=compression, zstd_content_size=content_size)
    assert read_snapshot_text(path) == expected
