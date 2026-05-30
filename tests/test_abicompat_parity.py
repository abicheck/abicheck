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

"""libabigail `abicompat` parity tests (application-vs-library compatibility).

Mirrors `test_abidiff_parity.py`, but for the application-compatibility
dimension: given an app linked against `libfoo` v1, is it still compatible
with `libfoo` v2? abicheck answers via `check_appcompat()`; libabigail
answers via the `abicompat` tool. We assert both reach the same verdict on
canonical scenarios.

`abicompat` ships in the same `abigail-tools` package as `abidiff` and uses
the same exit-code bit field:
    bit 0 (1) = error
    bit 2 (4) = ABI change present
    bit 3 (8) = incompatible (breaking) change present

Requires: abicompat (libabigail-tools), gcc/g++.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Cases: (name, lib_v1, lib_v2, app_src, abicheck_expected, abicompat_expected)
#
# The app only ever calls `stable()` — so changes confined to `stable()`'s
# signature break the app, while changes to *other* symbols do not.
# ---------------------------------------------------------------------------

_LIB_V1 = """
int stable(int x) { return x + 1; }
int other(int x) { return x * 2; }
"""

# 1. Identical library → app stays compatible.
_LIB_SAME = _LIB_V1

# 2. A symbol the app does NOT use is removed → app still compatible.
_LIB_DROP_UNUSED = """
int stable(int x) { return x + 1; }
"""

# 3. The symbol the app DOES use is removed → breaking for the app.
_LIB_DROP_USED = """
int other(int x) { return x * 2; }
"""

_APP_SRC = """
extern int stable(int x);
int main(void) { return stable(41); }
"""

# (name, lib_v2_src, abicheck_expected, abicompat_expected)
PARITY_CASES = [
    ("identical", _LIB_SAME, {"NO_CHANGE", "COMPATIBLE"}, {"NO_CHANGE", "COMPATIBLE"}),
    ("drop_unused_symbol", _LIB_DROP_UNUSED, {"NO_CHANGE", "COMPATIBLE"}, {"NO_CHANGE", "COMPATIBLE"}),
    ("drop_used_symbol", _LIB_DROP_USED, {"BREAKING"}, {"BREAKING"}),
]


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} not found in PATH")


def _compile_so(src: str, out: Path) -> None:
    """Compile a plain ``libfoo.so`` (no version suffix) so ``-lfoo`` links."""
    src_file = out.with_suffix(".c")
    src_file.write_text(src.strip() + "\n", encoding="utf-8")
    cmd = [
        "gcc", "-shared", "-fPIC", "-g", "-fvisibility=default",
        "-Wl,-soname,libfoo.so", "-o", str(out), str(src_file),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        pytest.skip(f"library compile failed: {r.stderr[:200]}")


def _compile_app(src: str, lib: Path, out: Path, tmp_path: Path) -> None:
    src_file = tmp_path / "app.c"
    src_file.write_text(src.strip() + "\n", encoding="utf-8")
    # lib is named libfoo.so → link with -lfoo.
    cmd = [
        "gcc", "-g", "-o", str(out), str(src_file),
        "-L", str(lib.parent), "-lfoo",
        f"-Wl,-rpath,{lib.parent}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        pytest.skip(f"app compile failed: {r.stderr[:200]}")


def _run_abicompat(app: Path, old_lib: Path, new_lib: Path) -> str:
    r = subprocess.run(
        ["abicompat", str(app), str(old_lib), str(new_lib)],
        capture_output=True, text=True, timeout=30,
    )
    code = r.returncode
    if code == 0:
        return "COMPATIBLE"
    if code & 1:
        return "ERROR"
    if code & 8:
        return "BREAKING"
    if code & 4:
        return "COMPATIBLE"
    return "COMPATIBLE"


def _run_abicheck_appcompat(app: Path, old_lib: Path, new_lib: Path) -> str:
    from abicheck.appcompat import check_appcompat

    result = check_appcompat(app, old_lib, new_lib, lang="c")
    return result.verdict.value


@pytest.mark.libabigail
@pytest.mark.parametrize(
    "name,lib_v2_src,abicheck_exp,abicompat_exp",
    PARITY_CASES,
    ids=[c[0] for c in PARITY_CASES],
)
def test_abicompat_parity(
    name: str,
    lib_v2_src: str,
    abicheck_exp: set[str],
    abicompat_exp: set[str],
    tmp_path: Path,
) -> None:
    _require_tool("abicompat")
    _require_tool("gcc")

    v1 = tmp_path / "v1" / "libfoo.so"
    v2 = tmp_path / "v2" / "libfoo.so"
    v1.parent.mkdir()
    v2.parent.mkdir()
    _compile_so(_LIB_V1, v1)
    _compile_so(lib_v2_src, v2)

    app = tmp_path / "app"
    _compile_app(_APP_SRC, v1, app, tmp_path)

    ac = _run_abicheck_appcompat(app, v1, v2)
    ab = _run_abicompat(app, v1, v2)

    if ab == "ERROR":
        pytest.skip(f"abicompat returned ERROR for case {name}")

    assert ac in abicheck_exp, f"abicheck verdict {ac!r} not in {abicheck_exp} (case {name})"
    assert ab in abicompat_exp, f"abicompat verdict {ab!r} not in {abicompat_exp} (case {name})"
