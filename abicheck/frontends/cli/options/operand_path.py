# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""``compare``'s OLD/NEW operand type: an existing path, with a
``ProjectSnapshot`` package archive unpacked on the way in (storage-format-v2
A1.1).

Unpacking at the argument boundary means everything downstream -- operand
classification, the release fan-out, the stored-package readers -- sees the
ordinary package directory it already handles, exactly as a compressed
``.abi.json.zst`` is transparently decompressed before anything reads it.
The directory lives in a private temporary location removed when the command
finishes (``Context.call_on_close``).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import click

from ....errors import SnapshotError
from ....workflows.storage import is_project_package_archive, unpack_project_package

__all__ = ["CompareOperandPath"]


class CompareOperandPath(click.Path):
    """``click.Path(exists=True, path_type=Path)`` plus archive unpacking."""

    def __init__(self) -> None:
        super().__init__(exists=True, path_type=Path)

    def convert(
        self, value: Any, param: click.Parameter | None, ctx: click.Context | None
    ) -> Any:
        path = super().convert(value, param, ctx)
        if not isinstance(path, Path) or not is_project_package_archive(path):
            return path
        workdir = Path(tempfile.mkdtemp(prefix="abicheck-package-"))
        if ctx is not None:
            ctx.call_on_close(lambda: shutil.rmtree(workdir, ignore_errors=True))
        # Named after the archive, so a report naming the operand still
        # says which package it was.
        dest = workdir / (path.stem or "package")
        try:
            unpack_project_package(path, dest)
        except SnapshotError as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            self.fail(str(exc), param, ctx)
        return dest
