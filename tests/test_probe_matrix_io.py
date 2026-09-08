# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Unit tests for the probe-matrix JSON I/O owned by
``abicheck.workflows.findings`` (ADR-061 gap E).

This is a behavior-preserving move: these assertions previously lived in
``test_probe_harness.py`` against ``MatrixSnapshot.to_json()``/
``MatrixSnapshot.from_dict()``, which moved here alongside the
implementation when ``probe_harness.py`` (``compare``) stopped owning
snapshot (de)serialization (a ``storage`` operation ``compare`` may not
own).
"""

from __future__ import annotations

import json

from abicheck.probe_harness import MatrixSnapshot, ProbeResult
from abicheck.workflows.findings import (
    matrix_snapshot_from_dict,
    matrix_snapshot_to_json,
    write_matrix_snapshot,
)


class TestMatrixSnapshotJsonRoundtrip:
    def test_roundtrip_json_no_snapshot(self) -> None:
        m = MatrixSnapshot(
            library="lib",
            version="1",
            spec_name="s",
            cxx_stds={"a": 20, "b": 17},
            defaults={"backend": "tbb"},
            results=[
                ProbeResult(
                    configuration_id="a",
                    probe_id="p1",
                    object_path="build/a__p1.o",
                    error=None,
                ),
                ProbeResult(
                    configuration_id="b",
                    probe_id="p1",
                    error="compiler not found",
                ),
            ],
        )
        roundtrip = matrix_snapshot_from_dict(json.loads(matrix_snapshot_to_json(m)))
        assert roundtrip.library == "lib"
        assert roundtrip.cxx_stds == {"a": 20, "b": 17}
        assert roundtrip.defaults == {"backend": "tbb"}
        assert len(roundtrip.results) == 2
        assert roundtrip.results[1].error == "compiler not found"

    def test_write_matrix_snapshot_round_trips_through_load(self, tmp_path) -> None:
        from abicheck.workflows.findings import load_matrix_snapshot

        m = MatrixSnapshot(
            library="lib",
            version="2",
            spec_name="s",
            results=[ProbeResult(configuration_id="a", probe_id="p1")],
        )
        out = tmp_path / "matrix.json"
        write_matrix_snapshot(m, out)
        loaded = load_matrix_snapshot(out)
        assert loaded.library == "lib"
        assert loaded.version == "2"
        assert loaded.results[0].configuration_id == "a"
