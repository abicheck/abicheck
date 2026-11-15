"""TEMPORARY diagnostic (macOS only): what the builtin probe sees and returns.

Remove once the macOS evidence for test_one_change_one_finding[global_variable_retyped-clang]
is recorded. Fails on purpose on darwin so CI prints the evidence.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from abicheck.extract import builtin_abi as b


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS diagnostic")
def test_probe_diagnostic_macos() -> None:
    lines: list[str] = []
    tmp = Path(tempfile.mkdtemp())
    body = "\n".join(b._probe_line(n) for n in b._PROBED)
    src = tmp / "probe.c"
    src.write_text(b._PROBE_SOURCE.replace("@BODY@", body), encoding="utf-8")
    for cc in ("clang++", "clang"):
        for opts in ((), ("-I" + str(tmp),), ("-std=c++17", "-I" + str(tmp))):
            argv = [cc, *opts, "-x", "c", str(src), "-o", str(tmp / "p")]
            c = subprocess.run(argv, capture_output=True, text=True)
            run = (
                subprocess.run([str(tmp / "p")], capture_output=True, text=True)
                if c.returncode == 0
                else None
            )
            probe = b.probe_builtin_abi(cc, tuple(opts))
            lines.append(
                f"[{cc} {opts}] build rc={c.returncode} stderr={c.stderr.strip()[:200]!r} "
                f"run rc={None if run is None else run.returncode} "
                f"-> probe long={None if probe is None else probe.layout.get('long')}"
            )
    pytest.fail("PROBE DIAG\n" + "\n".join(lines))
