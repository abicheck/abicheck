import os
import subprocess
import sys

import pytest

from abiforge.run.erased_buffer import write_sources


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="uses ELF shared libraries")
def test_element_count_survives_type_erasure(tmp_path):
    write_sources(tmp_path)
    client = tmp_path / "client"
    for variant, expected in (
        ("baseline", 0),
        ("equivalent", 0),
        ("count_as_bytes", 2),
    ):
        directory = tmp_path / variant
        directory.mkdir()
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-shared",
                "-fPIC",
                tmp_path / f"{variant}.cpp",
                "-o",
                directory / "libtransfer.so",
            ],
            check=True,
        )
        if variant == "baseline":
            subprocess.run(
                [
                    "g++",
                    "-std=c++17",
                    tmp_path / "client.cpp",
                    "-L",
                    directory,
                    "-ltransfer",
                    "-o",
                    client,
                ],
                check=True,
            )
        result = subprocess.run(
            [client], env={**os.environ, "LD_LIBRARY_PATH": str(directory)}
        )
        assert result.returncode == expected
