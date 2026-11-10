# SPDX-License-Identifier: Apache-2.0
"""Build native GCC plugins explicitly: python -m abicheck.extract.headers.gcc.build."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from .discovery import cache_directory, compiler_output, resolve_compiler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", default="g++")
    parser.add_argument("--headers", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    compiler = resolve_compiler(args.compiler)
    headers = (
        args.headers
        or Path(compiler_output(compiler, "-print-file-name=plugin")) / "include"
    )
    if not (headers / "gcc-plugin.h").is_file():
        parser.error(f"Matching GCC plugin-dev headers are missing: {headers}")
    output = args.output or cache_directory(compiler)
    output.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    build_plugin(compiler, headers, output, "gcc_facts.so", [])
    build_plugin(compiler, headers, output, "gcc_cpp_facts.so", ["-DGCC_CPP"])


def build_plugin(
    compiler: str, headers: Path, output: Path, name: str, defines: list[str]
) -> None:
    # Compile to a temporary file and rename atomically; an active scan
    # must never mmap a half-written shared object.
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        artifact = Path(tmp) / name
        subprocess.run(
            [
                compiler,
                "-std=c++17",
                "-x",
                "c++",
                "-O2",
                "-shared",
                "-fPIC",
                "-fno-rtti",
                *defines,
                "-I",
                str(headers),
                str(Path(__file__).with_name("gcc_facts.cc")),
                "-o",
                str(artifact),
            ],
            check=True,
        )
        os.replace(artifact, output / name)
    logging.getLogger(__name__).info("Built %s", output / name)


if __name__ == "__main__":
    main()
