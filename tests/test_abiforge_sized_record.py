# SPDX-License-Identifier: Apache-2.0
"""Cross-check a frozen prefix-sized client against the public ELF/DWARF checker."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "abiforge/fixtures/sized_record"


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="uses the ELF dynamic loader")
def test_sized_input_and_output(tmp_path):
    client = tmp_path / "client"
    baseline = tmp_path / "control/librecord.so"
    for name, flags, observation in (
        ("control", [], "3 7 41 / 44 7 11"),
        ("ignore", ["-DIGNORE_EXTENT"], "44 7 11 / 44 7 11"),
        ("swap", ["-DSWAP_FIELDS"], "44 11 7 / 44 11 7"),
    ):
        directory = tmp_path / name
        directory.mkdir()
        library = directory / "librecord.so"
        subprocess.run(
            [
                "cc",
                "-g",
                "-fPIC",
                "-shared",
                *flags,
                SOURCE / "provider.c",
                "-o",
                library,
            ],
            check=True,
        )
        if name == "control":
            subprocess.run(
                ["cc", SOURCE / "client.c", "-L", directory, "-lrecord", "-o", client],
                check=True,
            )
        result = subprocess.run(
            [client],
            env={**os.environ, "LD_LIBRARY_PATH": str(directory)},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert (result.returncode, result.stdout.strip()) == (
            int(name != "control"),
            observation,
        )
        report = directory / "comparison.json"
        check = subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck",
                "compare",
                baseline,
                library,
                "-o",
                f"json={report}",
            ],
            timeout=60,
        )
        data = json.loads(report.read_text())
        if name == "swap":
            assert check.returncode == 4 and data["verdict"] == "BREAKING"
            assert any(
                item["kind"] == "type_field_offset_changed"
                and item["symbol"] == "record"
                for item in data["changes"]
            )
        else:
            assert check.returncode == 0 and data["verdict"] == "NO_CHANGE"


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="uses the ELF dynamic loader")
def test_dispatch_minimum_and_cleanup_owner(tmp_path):
    source = SOURCE.with_name("callback_cleanup")
    client = tmp_path / "client"
    for name, flags, expected in (
        ("control", [], "-1 0 0 1 / 0 1"),
        ("short_table", ["-DIGNORE_MINIMUM"], "0 1 0 2 / 0 1"),
        ("ownership", ["-DRELEASE_BORROWED"], "-1 0 0 1 / 1 1"),
    ):
        directory = tmp_path / name
        directory.mkdir()
        library = directory / "libops.so"
        subprocess.run(
            ["cc", "-shared", "-fPIC", *flags, source / "provider.c", "-o", library],
            check=True,
        )
        if name == "control":
            subprocess.run(
                ["cc", source / "client.c", "-L", directory, "-lops", "-o", client],
                check=True,
            )
        result = subprocess.run(
            [client],
            env={**os.environ, "LD_LIBRARY_PATH": str(directory)},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert (result.returncode, result.stdout.strip()) == (
            int(name != "control"),
            expected,
        )


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="uses the ELF dynamic loader")
def test_buffer_capacity_and_stream_outcomes(tmp_path):
    source = SOURCE.with_name("buffer_stream")
    client = tmp_path / "client"
    for variant, expected in (
        ("control", 0),
        ("ALWAYS_GROW", 1),
        ("GROW_BORROWED", 2),
        ("SHORT_IS_EOF", 12),
        ("HIDE_ERROR", 8),
    ):
        directory = tmp_path / variant
        directory.mkdir()
        flags = [] if variant == "control" else [f"-D{variant}"]
        subprocess.run(
            [
                "cc",
                "-shared",
                "-fPIC",
                *flags,
                source / "provider.c",
                "-o",
                directory / "libprobe.so",
            ],
            check=True,
        )
        if variant == "control":
            subprocess.run(
                ["cc", source / "client.c", "-L", directory, "-lprobe", "-o", client],
                check=True,
            )
        run = subprocess.run(
            [client], env={**os.environ, "LD_LIBRARY_PATH": str(directory)}, timeout=10
        )
        assert run.returncode == expected
