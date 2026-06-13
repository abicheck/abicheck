# Copyright 2026 Nikolay Petrov
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

"""Compiler feature probing for example-case gating.

Some example cases exercise very new language features (e.g. C23 ``_BitInt``)
that older but still-current toolchains do not implement — GCC only gained
``_BitInt`` in GCC 14, so the GCC 13 shipped on ``ubuntu-latest`` cannot
compile such a fixture at all. The compile fails *before* abicheck ever runs,
so it cannot be modelled as a verdict ``known_gap``/xfail; the case must be
*skipped* when the toolchain lacks the feature.

A case opts in by adding ``"requires_feature": "<name>"`` to its
``ground_truth.json`` entry. Both example harnesses
(``tests/test_example_autodiscovery.py`` and ``tests/validate_examples.py``)
consult :func:`compiler_supports` to decide whether to skip.

The probe actually compiles a tiny snippet with the platform's native
compiler, so it is correct across gcc/clang/MSVC and all platforms rather than
hard-coding version numbers.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from functools import cache
from pathlib import Path

# Each feature maps to a (is_cpp, snippet, std_flag_attempts) probe. The probe
# succeeds if ANY of the std-flag attempts compiles the snippet to an object.
_FEATURE_PROBES: dict[str, tuple[bool, str, tuple[str, ...]]] = {
    # C23 bit-precise integers — GCC 14+, Clang 16+.
    "_BitInt": (False, "_BitInt(64) probe_bitint_global;\n",
                ("-std=c23", "-std=c2x", "")),
    # C++20 char8_t — GCC 9+, Clang 9+ (only relevant where it must compile).
    "char8_t": (True, "char8_t probe_char8_global;\n",
                ("-std=c++20", "-std=c++2a", "")),
    # C11 atomics.
    "_Atomic": (False, "_Atomic int probe_atomic_global;\n",
                ("-std=c11", "")),
}


def _env_compiler(var: str) -> str | None:
    """Compiler named by env var *var* (CC/CXX) if on PATH, else None.

    The clang examples-validation lane selects its toolchain via ``CC``/``CXX``;
    the probe must honor the *same* compiler before falling back to the native
    order, otherwise a clang-buildable feature (e.g. ``_BitInt`` on the runner's
    Clang) is judged unsupported because GCC is tried first and lacks it — which
    would silently skip the case and report a false green on the clang lane.
    """
    cc = os.environ.get(var)
    if cc and shutil.which(cc):
        return cc
    return None


def _c_compiler() -> str | None:
    env = _env_compiler("CC")
    if env:
        return env
    for cc in ("gcc", "clang", "cc"):
        if shutil.which(cc):
            return cc
    return None


def _cxx_compiler() -> str | None:
    env = _env_compiler("CXX")
    if env:
        return env
    for cxx in ("g++", "clang++", "c++"):
        if shutil.which(cxx):
            return cxx
    return None


def _msvc() -> str | None:
    return shutil.which("cl") if sys.platform == "win32" else None


@cache
def compiler_supports(feature: str) -> bool:
    """Return True if the native toolchain can compile *feature*.

    Unknown features return True (fail open) so a typo never silently skips a
    whole case — the real compile in the harness will surface any problem.
    Results are cached per process.
    """
    probe = _FEATURE_PROBES.get(feature)
    if probe is None:
        return True
    is_cpp, snippet, std_attempts = probe

    # MSVC does not implement _BitInt; treat probe sources uniformly via cl.
    msvc = _msvc()
    compiler = (_cxx_compiler() if is_cpp else _c_compiler()) if not msvc else msvc
    if compiler is None:
        # No compiler at all — let the harness's own compiler check handle it.
        return True

    suffix = ".cpp" if is_cpp else ".c"
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"probe{suffix}"
        src.write_text(snippet)
        obj = Path(td) / "probe.o"
        for std in std_attempts:
            if compiler == "cl":
                args = [compiler, "/c", "/Fo:" + str(obj), str(src)]
                if std:
                    args.insert(1, "/std:" + std.split("=")[-1])
            else:
                args = [compiler, "-c", "-o", str(obj), str(src)]
                if std:
                    args.insert(1, std)
            try:
                r = subprocess.run(args, capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode == 0:
                return True
    return False
