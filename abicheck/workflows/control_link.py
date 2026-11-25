# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Link a supplied PIC control object against each named provider, without running it."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..model import AbiSnapshot


def control_link(
    object_path: Path, old: Path | AbiSnapshot, new: Path | AbiSnapshot
) -> dict[str, object]:
    linker = shutil.which("ld")
    if not linker or not isinstance(old, Path) or not isinstance(new, Path):
        return {"status": "unavailable"}
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile

    try:
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "control.o"
            shutil.copyfile(object_path, control)
            with control.open("rb") as stream:
                if ELFFile(stream).header.e_type != "ET_REL":
                    return {"status": "requires_relocatable_object"}
            results = {}
            for side, library in (("old", old), ("new", new)):
                command = [
                    linker,
                    "--no-demangle",
                    "-shared",
                    "-z",
                    "defs",
                    "--allow-shlib-undefined",
                    "-o",
                    str(Path(directory) / "probe.so"),
                    str(control),
                    str(library.resolve()),
                ]
                run = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=30,
                    env={**os.environ, "LC_ALL": "C"},
                    cwd=directory,
                )
                results[side] = {
                    "library": str(library),
                    "exit_code": run.returncode,
                    "diagnostic": run.stderr[:8192],
                }
            return {"status": "tested", **results}
    except (ELFError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "reason": str(exc)}
