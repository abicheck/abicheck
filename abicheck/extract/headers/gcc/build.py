# SPDX-License-Identifier: Apache-2.0
"""Build the native GCC fact plugins (gcc_facts.so, gcc_cpp_facts.so) for one exact GCC; scans never build them."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import click

from ....deadline import run_bounded
from .discovery import cache_directory, compiler_output, resolve_compiler

#: An explicit, user-invoked plugin compile; generous, but never unbounded.
BUILD_TIMEOUT_S = 900.0


@click.command(help=__doc__)
@click.option(
    "--compiler",
    default="g++",
    show_default=True,
    help="The exact GCC driver the plugins must match.",
)
@click.option(
    "--headers",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="GCC plugin-dev include directory (default: <gcc -print-file-name=plugin>/include).",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Output directory (default: the versioned user cache scans look in).",
)
def main(compiler: str, headers: Path | None, output: Path | None) -> None:
    resolved = resolve_compiler(compiler)
    include = (
        headers
        or Path(compiler_output(resolved, "-print-file-name=plugin")) / "include"
    )
    if not (include / "gcc-plugin.h").is_file():
        raise click.UsageError(
            f"Matching GCC plugin-dev headers are missing: {include}"
        )
    target = output or cache_directory(resolved)
    target.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    build_plugin(resolved, include, target, "gcc_facts.so", [])
    build_plugin(resolved, include, target, "gcc_cpp_facts.so", ["-DGCC_CPP"])


def build_plugin(
    compiler: str, headers: Path, output: Path, name: str, defines: list[str]
) -> None:
    # Compile to a temporary file and rename atomically; an active scan
    # must never mmap a half-written shared object.
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        artifact = Path(tmp) / name
        command = [
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
        ]
        result = run_bounded(
            command, timeout=BUILD_TIMEOUT_S, capture_output=True, text=True
        )
        if result.returncode:
            raise click.ClickException(
                f"Building {name} failed ({result.returncode}): "
                f"{' '.join(command)}\n{result.stderr}"
            )
        os.replace(artifact, output / name)
    logging.getLogger(__name__).info("Built %s", output / name)


if __name__ == "__main__":
    main()
