# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Drive catalog example cases through the ``appcompat`` workflow (G3).

The example catalog (``examples/case*``) is exhaustive about *change
types*, but every case is normally consumed through the single-pair
``compare`` workflow. These tests turn the dormant ``examples/case*/app.c``
consumers into asserted regressions: build the app against v1, swap in
v2, and assert the **app-scoped** verdict.

The key property under test is application-centric filtering: a symbol
removal is only BREAKING for an app that actually imports the removed
symbol. An app that never referenced it stays COMPATIBLE even though the
library-level diff is BREAKING.

Stock ``cc`` only (no castxml — symbol-level appcompat needs none), so
these run in the default lane and self-skip when ``cc`` is absent,
mirroring the gcc-only bundle E2E tests in test_bundle.py. GNU ld's
``-Wl,-soname`` makes them Linux-only.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.appcompat import check_appcompat
from abicheck.checker_policy import Verdict

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="appcompat fixtures use GNU ld -Wl,-soname (Linux only)",
)

EXAMPLES = Path(__file__).parent.parent / "examples"


def _cc() -> str:
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("cc unavailable; cannot build appcompat fixture")
    return cc


def _build_case(
    case: str, tmp_path: Path, *, app_source: str | None = None,
) -> tuple[Path, Path, Path]:
    """Compile ``case``'s v1/v2 into shared libs and build its app vs v1.

    Returns ``(app, lib_v1, lib_v2)``. When *app_source* is given it
    overrides the case's bundled ``app.c`` (used to model an app that does
    not touch the changed symbol).
    """
    cc = _cc()
    case_dir = EXAMPLES / case
    lib_v1 = tmp_path / "libcase.so.1"
    lib_v2 = tmp_path / "libcase.so.2"
    for src, out in ((case_dir / "v1.c", lib_v1), (case_dir / "v2.c", lib_v2)):
        res = subprocess.run(
            [cc, "-shared", "-fPIC", "-g", str(src), "-o", str(out),
             "-Wl,-soname,libcase.so.1"],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            pytest.fail(f"cc failed building {out.name}: {res.stderr}")

    if app_source is None:
        app_src = case_dir / "app.c"
    else:
        app_src = tmp_path / "app.c"
        app_src.write_text(app_source, encoding="utf-8")
    app = tmp_path / "app"
    res = subprocess.run(
        [cc, "-g", str(app_src), f"-I{case_dir}", str(lib_v1),
         "-o", str(app), f"-Wl,-rpath,{tmp_path}"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        pytest.fail(f"cc failed building app for {case}: {res.stderr}")
    return app, lib_v1, lib_v2


def test_appcompat_breaking_when_app_uses_removed_symbol(tmp_path: Path) -> None:
    # case01's bundled app.c calls the removed helper() → BREAKING for it.
    app, v1, v2 = _build_case("case01_symbol_removal", tmp_path)
    result = check_appcompat(app, v1, v2, lang="c")
    assert result.verdict == Verdict.BREAKING
    assert "helper" in result.missing_symbols
    assert any(c.kind.value == "func_removed" for c in result.breaking_for_app)


def test_appcompat_compatible_when_app_skips_removed_symbol(
    tmp_path: Path,
) -> None:
    # Same library break (helper() removed) but this app only calls
    # compute() → application-centric filtering keeps it COMPATIBLE.
    app_source = (
        '#include "v1.h"\n'
        "int main(void){ return compute(5) == 10 ? 0 : 1; }\n"
    )
    app, v1, v2 = _build_case(
        "case01_symbol_removal", tmp_path, app_source=app_source,
    )
    result = check_appcompat(app, v1, v2, lang="c")
    assert result.verdict in (Verdict.COMPATIBLE, Verdict.NO_CHANGE)
    assert "helper" not in result.missing_symbols
    # helper's removal is real but irrelevant to *this* app.
    assert not result.missing_symbols


def test_appcompat_compatible_for_additive_release(tmp_path: Path) -> None:
    # case03 is purely additive: the app's symbol survives, new ones add.
    app, v1, v2 = _build_case("case03_compat_addition", tmp_path)
    result = check_appcompat(app, v1, v2, lang="c")
    assert result.verdict in (Verdict.COMPATIBLE, Verdict.NO_CHANGE)
    assert not result.missing_symbols
