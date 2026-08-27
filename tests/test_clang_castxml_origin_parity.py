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

"""Phase 3 of ``docs/contribute/plans/bug-class-regression-testing.md``:
cross-backend (castxml vs. direct-clang) agreement on
``abicheck.provenance.classify_origin``'s PUBLIC_HEADER/PRIVATE_HEADER split.

Split out as its own module rather than folded into the pre-existing
``test_clang_header_backend_integration.py`` (which already carries the
plain public-surface parity test this one complements), because that module
predates ADR-061 and carries a `debt.yaml` no-growth line-count baseline
(1604 lines) -- adding a new test there would have breached it (Codex
review, PR #894: `[debt-no-growth] tests/test_clang_header_backend_
integration.py: 1676 lines exceeds adoption baseline 1604`). Per this
repo's own convention for oversized files ("prefer extending a split-out
module over growing the parent toward the cap"), a genuinely new test gets
a new sibling file instead.

Requires clang + g++ + castxml; skips cleanly otherwise. Marked
``integration`` since it needs castxml in addition to clang/g++ -- without
that marker, a host with castxml installed (e.g. a pixi environment, which
provisions castxml for the integration/libabigail/abicc marker lanes) would
have this test silently selected and executed by the fast/PR "not
integration" lane, spending real g++/clang/castxml subprocess time in a lane
meant to stay fast.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.dumper import dump
from abicheck.model import ScopeOrigin

# Scoped to Linux/ELF for the identical reason
# test_clang_header_backend_integration.py is: the clang L2 backend's target
# host, and a g++-built ELF .so with predictable Itanium mangling.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="clang/castxml origin parity test is ELF/Linux-scoped",
    ),
]


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def test_clang_and_castxml_agree_on_public_vs_private_header_origin(
    tmp_path: Path,
) -> None:
    """``classify_origin``'s PUBLIC_HEADER/PRIVATE_HEADER split must agree
    across header-AST backends. A declaration's origin is a function of the
    real ``-H``/``--public-header-dir`` set the dump was invoked with --
    never of which backend parsed the header -- so a function declared in
    the explicit public umbrella and one declared only in a transitively
    ``#include``d private header must classify PUBLIC_HEADER / PRIVATE_HEADER
    identically on both frontends.
    """
    if not (_have("clang") and _have("g++")):
        pytest.skip("clang and g++ are required for this backend-parity test")
    if not _have("castxml"):
        pytest.skip("castxml required for the clang↔castxml parity oracle")

    private_header = tmp_path / "detail.h"
    private_header.write_text("#pragma once\nint detail_helper(int x);\n")
    public_header = tmp_path / "api.h"
    public_header.write_text(
        '#pragma once\n#include "detail.h"\nint api_call(int x);\n'
    )
    src = tmp_path / "api.cpp"
    src.write_text(
        '#include "api.h"\n'
        "int detail_helper(int x) { return x + 1; }\n"
        "int api_call(int x) { return detail_helper(x) * 2; }\n"
    )
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )

    def origins(snap: object) -> dict[str, str]:
        return {
            f.name: f.origin.value  # type: ignore[attr-defined]
            for f in snap.functions  # type: ignore[attr-defined]
            if f.name in ("api_call", "detail_helper")
        }

    clang_snap = dump(
        so, [public_header], header_backend="clang", public_headers=[public_header]
    )
    castxml_snap = dump(
        so, [public_header], header_backend="castxml", public_headers=[public_header]
    )

    clang_origins = origins(clang_snap)
    castxml_origins = origins(castxml_snap)
    # Both backends must actually see both functions -- an empty/partial
    # dict would make the equality below vacuously true.
    assert set(clang_origins) == {"api_call", "detail_helper"}
    assert set(castxml_origins) == {"api_call", "detail_helper"}
    assert clang_origins == castxml_origins
    assert clang_origins["api_call"] == ScopeOrigin.PUBLIC_HEADER.value
    assert clang_origins["detail_helper"] == ScopeOrigin.PRIVATE_HEADER.value
