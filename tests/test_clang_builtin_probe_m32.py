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
"""The clang header backend, end to end, probes the builtins of its own -m32 parse.

A C++ library built for i386 and parsed through the public ``dump`` with
``-m32 -std=c++17``. The probe must measure the 32-bit ``long`` (4 bytes) and
must not receive the C++ standard it cannot compile. The oracle is the
hand-written i386 ``long`` size, not the probe's own output.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.dumper import dump
from abicheck.extract.headers.clang import backend as clang_backend

_HEADER = "#pragma once\nlong next_tick(long t);\n"
_SOURCE = '#include "tick.h"\nlong next_tick(long t) { return t + 1; }\n'


def _clang_m32_available() -> bool:
    clang = shutil.which("clang++")
    if clang is None:
        return False
    probe = subprocess.run(
        [clang, "-m32", "-x", "c++", "-", "-fsyntax-only"],
        input="int main() { return 0; }\n",
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode == 0


# ELF/Linux only: an i386 *shared library* needs the ELF i386 link. On macOS
# ``-m32`` selects an armv4t Mach-O target that cannot link (as CI showed), so
# this is the same platform scope as ``test_clang_header_backend_integration``.
@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="ELF i386 link is Linux-scoped"
)
@pytest.mark.skipif(not _clang_m32_available(), reason="clang++ with -m32 libc")
def test_the_clang_parse_probes_the_i386_long_it_parses_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "tick.h").write_text(_HEADER)
    src = tmp_path / "tick.cpp"
    src.write_text(_SOURCE)
    so = tmp_path / "libtick.so"
    built = subprocess.run(
        [
            "clang++",
            "-m32",
            "-shared",
            "-fPIC",
            "-std=c++17",
            "-I",
            str(tmp_path),
            str(src),
            "-o",
            str(so),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr

    seen: list[tuple[str, ...]] = []
    real_probe = clang_backend.probe_builtin_abi

    def spy(cc_bin: str, options: tuple[str, ...]):
        seen.append(tuple(options))
        return real_probe(cc_bin, options)

    monkeypatch.setattr(clang_backend, "probe_builtin_abi", spy)

    snap = dump(
        so,
        [tmp_path / "tick.h"],
        version="1",
        compiler="c++",
        lang="c++",
        header_backend="clang",
        gcc_options="-m32 -std=c++17",
        public_headers=[tmp_path / "tick.h"],
    )

    # The probe received the parse's target flag and no C++ standard.
    assert seen, "the clang parse never probed its builtins"
    assert "-m32" in seen[-1]
    assert not any(t.startswith(("-std=", "--std=")) for t in seen[-1])
    # And its answer is the i386 layout: long is 4 bytes, not the LP64 8.
    (func,) = [f for f in snap.declarations.functions if f.name == "next_tick"]
    assert func.params[0].type_abi_fact.value.size == 4
