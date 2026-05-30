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

"""libabigail `abipkgdiff` parity tests (package-vs-package comparison).

Mirrors `test_abidiff_parity.py`, but at the *package* level: build two tar
packages each containing a shared library, then compare them with both
abicheck's `compare-release` command and libabigail's `abipkgdiff`. We
assert both reach the same release-level verdict.

Tar packages are used because they need no external packaging tools to
build and `abipkgdiff` accepts them. `abipkgdiff` ships in the same
`abigail-tools` package as `abidiff` and shares its exit-code bit field:
    bit 0 (1) = error
    bit 2 (4) = ABI change present
    bit 3 (8) = incompatible (breaking) change present

abicheck `compare-release` exit codes: 0 = compatible, 2 = API_BREAK,
4 = BREAKING.

Requires: abipkgdiff (libabigail-tools), gcc.
"""

from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests._libabigail import (
    compile_shared_lib,
    decode_exit_code,
    require_tool,
)

# ---------------------------------------------------------------------------
# Library source pairs
# ---------------------------------------------------------------------------

_LIB_V1 = """
int api(int x) { return x + 1; }
int helper(int x) { return x * 2; }
"""

# Identical → compatible.
_LIB_SAME = _LIB_V1

# An exported symbol is removed → unambiguous incompatible ABI change for
# *both* tools. (Return/parameter type changes are deliberately avoided here:
# libabigail is conservative about sub-type drift without --headers-dir and
# reports them as compatible — that divergence is already documented and
# tested in test_abidiff_parity.py, and is not what this lane checks.)
_LIB_REMOVE_SYMBOL = """
int api(int x) { return x + 1; }
"""

# (name, lib_v2_src, abicheck_breaking_expected, abipkgdiff_breaking_expected)
PARITY_CASES = [
    ("identical", _LIB_SAME, False, False),
    ("symbol_removed", _LIB_REMOVE_SYMBOL, True, True),
]


def _make_tar_package(lib: Path, pkg: Path) -> None:
    """Wrap a shared library into a tar package under usr/lib/."""
    with tarfile.open(pkg, "w:gz") as tf:
        tf.add(lib, arcname="usr/lib/libfoo.so.1")


def _run_abipkgdiff(old_pkg: Path, new_pkg: Path) -> str:
    r = subprocess.run(
        ["abipkgdiff", str(old_pkg), str(new_pkg)],
        capture_output=True, text=True, timeout=60,
    )
    return decode_exit_code(r.returncode, zero_verdict="COMPATIBLE")


def _run_abicheck_compare_release(old_pkg: Path, new_pkg: Path) -> str:
    """Map `compare-release` exit code to a verdict bucket."""
    from abicheck.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["compare-release", str(old_pkg), str(new_pkg)])
    code = result.exit_code
    if code == 4:
        return "BREAKING"
    if code == 2:
        return "API_BREAK"
    if code == 0:
        return "COMPATIBLE"
    return f"ERROR({code})"


@pytest.mark.libabigail
@pytest.mark.parametrize(
    "name,lib_v2_src,abicheck_breaking,abipkgdiff_breaking",
    PARITY_CASES,
    ids=[c[0] for c in PARITY_CASES],
)
def test_abipkgdiff_parity(
    name: str,
    lib_v2_src: str,
    abicheck_breaking: bool,
    abipkgdiff_breaking: bool,
    tmp_path: Path,
) -> None:
    require_tool("abipkgdiff")
    require_tool("gcc")

    v1 = tmp_path / "build_v1" / "libfoo.so.1"
    v2 = tmp_path / "build_v2" / "libfoo.so.1"
    v1.parent.mkdir()
    v2.parent.mkdir()
    compile_shared_lib(_LIB_V1, v1, lang="c", soname="libfoo.so.1")
    compile_shared_lib(lib_v2_src, v2, lang="c", soname="libfoo.so.1")

    old_pkg = tmp_path / "foo-1.0.tar.gz"
    new_pkg = tmp_path / "foo-2.0.tar.gz"
    _make_tar_package(v1, old_pkg)
    _make_tar_package(v2, new_pkg)

    ab = _run_abipkgdiff(old_pkg, new_pkg)
    if ab == "ERROR":
        pytest.skip(f"abipkgdiff returned ERROR for case {name}")

    ac = _run_abicheck_compare_release(old_pkg, new_pkg)

    assert (ac == "BREAKING") == abicheck_breaking, (
        f"abicheck compare-release verdict {ac!r} (case {name}, "
        f"expected breaking={abicheck_breaking})"
    )
    assert (ab == "BREAKING") == abipkgdiff_breaking, (
        f"abipkgdiff verdict {ab!r} (case {name}, "
        f"expected breaking={abipkgdiff_breaking})"
    )
