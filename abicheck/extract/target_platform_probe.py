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

"""Run the compiler probe behind :mod:`.target_platform` (the pure part)
for a header-AST parse, as ``dumper_toolchain._stamp_ast_parser`` records it.
"""

from __future__ import annotations

import subprocess

from .._compiler_options import split_gcc_options
from ..deadline import run_bounded
from ..model.execution_cache import memoized, path_witness
from .castxml_compiler_emulation import emulation_arguments
from .target_platform import (
    effective_triple,
    platform_from_macros,
    target_platform_metadata,
)


def recorded_target_platform(
    metadata: dict[str, str],
    producer: str,
    executable: str,
    dialect: str | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    target_triple: str | None,
    cxx: bool,
) -> dict[str, str]:
    """The parse's effective target platform (``extract.target_platform``):
    clang probes itself; castxml probes its emulated compiler with the
    arguments castxml forwards to it. MSVC dialect: nothing recorded."""
    try:
        args = [*split_gcc_options(gcc_options or ""), *gcc_option_tokens]
    except ValueError:
        return {}
    cc = executable if producer == "clang" else metadata.get("compiler_selected")
    if not cc or dialect != "gnu":
        return {}
    if producer != "clang":
        args = emulation_arguments(args, cc_bin=cc, cc_id=dialect)
    width, endian = _probe_target_platform(cc, tuple(args), cxx)
    resolved = target_triple or metadata.get("compiler_target_triple")
    return target_platform_metadata(
        effective_triple(resolved, args, width), width, endian
    )


@memoized(maxsize=32, witness=lambda cc, args, cxx: path_witness(cc))
def _probe_target_platform(
    cc: str, args: tuple[str, ...], cxx: bool
) -> tuple[int | None, str | None]:
    """``(pointer_width, endianness)`` *cc* predefines under *args*."""
    cmd = [cc, *args, "-E", "-dM", "-x", "c++" if cxx else "c", "-"]
    try:
        r = run_bounded(cmd, input="", capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    return platform_from_macros(r.stdout) if r.returncode == 0 else (None, None)
