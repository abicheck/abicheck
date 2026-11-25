# SPDX-License-Identifier: Apache-2.0
"""Run the generated L2 witnesses against both public snapshot frontends."""

import platform
import shutil
import sys

import pytest

from abiforge.run.l2_representatives import run


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform != "linux" or platform.machine() != "x86_64",
    reason="SysV AMD64 register and Itanium vtable experiments",
)
@pytest.mark.parametrize("frontend", ["clang", "castxml"])
def test_generated_representatives(tmp_path, frontend):
    if not shutil.which("clang++") or not shutil.which(frontend):
        pytest.skip(f"requires clang++ and {frontend}")
    run(tmp_path, "clang++", frontend)
