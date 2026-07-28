# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""End-to-end dependency-exclusion scoping through the real dumper (castxml + gcc).

Unit-level ``test_dumper_scoping.py`` exercises ``is_system_header``/
``scope_snapshot_excluding_dependencies`` against hand-written path strings;
this file exercises the same logic against *real* castxml-reported
``source_location`` paths for a real system header (``<time.h>``'s
``struct tm``) on a real compiled ``.so``, to catch any mismatch between the
``_SYSTEM_HEADER_DIRS`` heuristic and what a real toolchain actually reports
(a real compiler's system-include search path, not a hand-picked test string).
Requires castxml and gcc/g++, marked ``integration`` (auto-skipped when
those tools are absent -- see ``conftest.py``).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="this fixture builds an ELF .so (Linux gcc)",
)


def _compile_so(tmp: Path) -> Path:
    """Build a tiny .so whose public header pulls in a real system header
    (<time.h>) alongside its own private header."""
    (tmp / "internal.h").write_text(
        "#ifndef INTERNAL_H\n#define INTERNAL_H\n"
        "struct Internal { int x; int y; };\n"
        "#endif\n"
    )
    (tmp / "api.h").write_text(
        "#ifndef API_H\n#define API_H\n"
        "#include <time.h>\n"
        '#include "internal.h"\n'
        "void public_fn(struct Internal *p, struct tm *t);\n"
        "#endif\n"
    )
    src = (
        '#include "api.h"\n'
        "void public_fn(struct Internal *p, struct tm *t) { (void)p; (void)t; }\n"
    )
    (tmp / "api.c").write_text(src)
    so = tmp / "libapi.so"
    res = subprocess.run(
        [
            "gcc",
            "-g",
            "-I",
            str(tmp),
            "-shared",
            "-fPIC",
            "-o",
            str(so),
            str(tmp / "api.c"),
        ],
        capture_output=True,
    )
    if res.returncode != 0:
        pytest.skip(f"gcc failed: {res.stderr.decode()[:200]}")
    return so


@pytest.mark.integration
def test_real_system_header_type_excluded_by_default(tmp_path: Path) -> None:
    from abicheck.dumper import dump
    from abicheck.dumper_scoping import scope_snapshot_excluding_dependencies

    so = _compile_so(tmp_path)
    api = tmp_path / "api.h"

    # No --public-header set given at all -- confirms the design goal that
    # dependency exclusion needs no public-header input, unlike origin
    # classification (ScopeOrigin stays UNKNOWN throughout this test).
    snap = dump(so, headers=[api], compiler="cc", lang="C")

    type_names = {t.name for t in snap.types}
    assert "tm" in type_names, "castxml did not surface struct tm from <time.h>"
    assert "Internal" in type_names, "castxml did not surface the private struct"
    assert any(f.name == "public_fn" for f in snap.functions)

    scoped = scope_snapshot_excluding_dependencies(snap)
    scoped_type_names = {t.name for t in scoped.types}
    assert "tm" not in scoped_type_names, (
        "struct tm (from a real <time.h> system header) must be excluded by default"
    )
    assert "Internal" in scoped_type_names, (
        "the library's own private struct must be kept -- this is a "
        "header-origin filter, not a public-API-visibility one"
    )
    assert any(f.name == "public_fn" for f in scoped.functions)


@pytest.mark.integration
def test_include_dependencies_keeps_real_system_header_type(tmp_path: Path) -> None:
    from abicheck.dumper import dump

    so = _compile_so(tmp_path)
    api = tmp_path / "api.h"
    snap = dump(so, headers=[api], compiler="cc", lang="C")

    # No scoping applied at all (the CLI --include-dependencies path) -- the
    # unscoped snapshot straight from dump() must still carry struct tm.
    assert "tm" in {t.name for t in snap.types}
