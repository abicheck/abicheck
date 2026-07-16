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
through the real castxml/gcc toolchain on the CI host. Three outcomes are all
acceptable and asserted for:

* the host toolchain is new enough — the parse succeeds and the header's
  declarations are observable end-to-end; or
* the failure matches one of G16's specific host-toolchain-mismatch
  signatures (sized-float, ``__assume__``, ``--lang c`` mismatch) — raised as
  the actionable ``HeaderToolchainError`` (not an opaque castxml stderr
  dump); or
* the failure is some other, diagnosable-but-not-toolchain-specific header
  problem (e.g. a missing umbrella/prelude context) — a plain
  ``SnapshotError``, but still carrying ``_castxml_failure_hint``'s generic
  remediation text, not a bare unclassified stderr dump.

What must NOT happen is a bare, unclassified error with no hint at all: that
would mean a known host-toolchain signature slipped past
``_castxml_failure_hint`` — exactly the 21-issue real-world regression G16
closes.

Linux-only: every signature ``_castxml_failure_hint`` recognises (glibc
sized-float types, the GCC 13+ libstdc++ ``__assume__`` attribute) is a
glibc/GCC-specific host-header quirk — the real-world scan campaign this plan
responds to was entirely Linux. macOS's Xcode SDK headers can fail this same
``<math.h>`` probe for an unrelated reason (Xcode's ``libc++`` using
``__has_cpp_attribute`` in a way the castxml-bundled clang frontend doesn't
support), which is a different, out-of-scope problem this test must not be
conflated with.

Requires: gcc/g++, castxml (Linux).
"""
from __future__ import annotations

import shutil
import sys
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
        if not sys.platform.startswith("linux"):
            pytest.skip(
                "G16's known signatures are glibc/GCC-specific (Linux-only); "
                "non-Linux hosts can fail this <math.h> probe for unrelated "
                "SDK reasons this plan does not cover."
            )
        _require_tool("castxml")
        _require_tool("g++")

        header = tmp_path / "mathwrap.h"
        header.write_text(MATH_HEADER, encoding="utf-8")

        try:
            root = _castxml_dump([header], [], compiler="c++")
        except HeaderToolchainError as exc:
            # Degraded-but-diagnosed: a known G16 host-toolchain signature,
            # with the actionable remediation folded into the message (not a
            # raw stderr dump).
            assert "Hint:" in str(exc)
            return
        except SnapshotError as exc:
            # Not one of G16's specific signatures, but still must carry a
            # diagnostic hint (e.g. _castxml_failure_hint's generic case 4) —
            # a bare, unclassified stderr dump is the regression this guards.
            if "Hint:" not in str(exc):  # pragma: no cover — regression guard
                pytest.fail(
                    "castxml failed on a <math.h>-including header with a "
                    f"bare, unclassified stderr dump (no diagnostic hint at "
                    f"all): {exc}"
                )
            return

        # Succeeded: the header's own declarations are observable end-to-end
        # (not just "didn't crash") — the real assertion behind "public vs.
        # private surface classification" that header-scoped scans exist for.
        names = {el.get("name") for el in root.iter() if el.get("name")}
        assert "mathwrap_hypot" in names
        assert "mathwrap_sqrt" in names
