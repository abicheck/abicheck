# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Executable opt-in SYCL campaigns, including the public checker CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="ELF consumer fixtures"),
]


def _run(module: str, out: Path, *args: str) -> dict:
    subprocess.run(
        [sys.executable, "-m", f"abiforge.run.{module}", str(out), *args], check=True
    )
    return json.loads((out / "receipt.json").read_text())


def test_pi_and_ur_export_mutations(tmp_path: Path) -> None:
    if shutil.which("cc") is None:
        pytest.skip("needs a C compiler")
    rows = _run("sycl_plugins", tmp_path)
    from abicheck.model.availability import FactStatus
    from abicheck.serialization import load_snapshot

    failed = load_snapshot(tmp_path / "unreadable.json").sycl
    assert failed is not None
    assert failed.plugin_inventory_status is FactStatus.FAILED
    assert "libur_adapter_opencl.so" in failed.plugin_scan_errors[0]
    assert len(rows) == 18
    assert all(row["matches"] for row in rows.values())


def test_onedal_queue_consumers(tmp_path: Path) -> None:
    prefix = os.environ.get("ABIFORGE_ONEDAL_PREFIX")
    if not prefix:
        pytest.skip(
            "set ABIFORGE_ONEDAL_PREFIX to a oneDAL/DPC++ SDK with a shared-USM device"
        )
    rows = _run("onedal_sycl", tmp_path, "--prefix", prefix)
    assert len(rows["compare-withdrawn"]["affected"]) == 7
    assert all(row["matches"] for row in rows.values())
