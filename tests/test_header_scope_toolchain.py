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

"""Header-scoped source-mode toolchain robustness, real-host end-to-end (plan G16).

The unit-level classifier/version-note tests in
``tests/test_castxml_toolchain_robustness.py`` are fully mocked so they run in
the default fast lane. This file is the remaining acceptance criterion from
the G16 plan: a header that transitively includes ``<math.h>`` (the real
trigger for the glibc sized-float aborts seen in the real-world scan
campaign — ``_Float32``/``_Float64``/``_Float128`` land in
``bits/floatn-common.h``, pulled in by ``<math.h>`` on a modern glibc) is run
through the real castxml/gcc toolchain on the CI host. Two outcomes are both
acceptable and asserted for:

* the host toolchain is new enough — the parse succeeds and the header's
  declarations are observable end-to-end; or
* the host toolchain is too old — the parse fails, but with the actionable
  ``HeaderToolchainError`` (not an opaque castxml stderr dump).

What must NOT happen is a bare, unclassified ``SnapshotError``/crash: that
would mean a known host-toolchain signature slipped past
``_castxml_failure_hint`` — exactly the 21-issue real-world regression G16
closes.

Requires: gcc/g++, castxml.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from abicheck.dumper import _castxml_dump
from abicheck.errors import HeaderToolchainError, SnapshotError

pytestmark = pytest.mark.integration


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} not found in PATH")


# A tiny header that transitively pulls in <math.h> — the real trigger for the
# glibc sized-float (_Float32/_Float64/_Float128) parse failures documented in
# the G16 plan (ISSUE-RW-20260606-1539-04, -1509-03, -1645-03).
MATH_HEADER = """\
#ifndef LIBMATHWRAP_H
#define LIBMATHWRAP_H

#include <math.h>

double mathwrap_hypot(double a, double b);
double mathwrap_sqrt(double x);

#endif
"""


class TestHeaderScopeSurvivesMathH:
    def test_castxml_parses_or_degrades_with_actionable_hint(
        self, tmp_path: Path
    ) -> None:
        _require_tool("castxml")
        _require_tool("g++")

        header = tmp_path / "mathwrap.h"
        header.write_text(MATH_HEADER, encoding="utf-8")

        try:
            root = _castxml_dump([header], [], compiler="c++")
        except HeaderToolchainError as exc:
            # Degraded-but-diagnosed: a known host-toolchain signature, with
            # the actionable remediation folded into the message (not a raw
            # stderr dump) — G16's acceptance criterion for a too-old host
            # castxml/clang frontend.
            assert "Hint:" in str(exc)
            return
        except SnapshotError as exc:  # pragma: no cover — regression guard
            pytest.fail(
                "castxml failed on a <math.h>-including header with an "
                f"unclassified SnapshotError (should be HeaderToolchainError "
                f"if the signature is a known host-toolchain mismatch, or "
                f"succeed on a new-enough host toolchain): {exc}"
            )

        # Succeeded: the header's own declarations are observable end-to-end
        # (not just "didn't crash") — the real assertion behind "public vs.
        # private surface classification" that header-scoped scans exist for.
        names = {el.get("name") for el in root.iter() if el.get("name")}
        assert "mathwrap_hypot" in names
        assert "mathwrap_sqrt" in names
