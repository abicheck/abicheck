"""TEMPORARY macOS diagnostic: where does the one-side-headers evidence come from?

Runs only on macOS. It dumps each side of the depth fixture binary-only and with
headers, collects every field it reads defensively, and fails on purpose with the
provenance it saw, so the macOS CI log carries it. Removed once the expectation of
test_common_evidence_depth is decided.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model.debug_evidence import debug_info_evidence
from abicheck.serialization import load_snapshot
from tests.test_common_evidence_depth import _HEADER, _SOURCE


def _read(fn):
    try:
        return fn()
    except Exception as exc:  # diagnostic: record the failure, never crash
        return f"<{type(exc).__name__}: {exc}>"


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS diagnostic only")
def test_macos_one_side_headers_provenance(tmp_path: Path) -> None:
    (tmp_path / "a.h").write_text(_HEADER)
    (tmp_path / "a.cpp").write_text(_SOURCE)
    so = tmp_path / "libk.so"
    subprocess.run(
        ["g++", "-g", "-shared", "-fPIC", "-o", str(so), str(tmp_path / "a.cpp")],
        check=True,
    )
    lines = [f"dsym_present={(tmp_path / (so.name + '.dSYM')).exists()}"]
    header = tmp_path / "a.h"
    for label, extra in (("binary-only", []), ("with-headers", ["-H", str(header)])):
        out = tmp_path / f"{label}.json"
        res = CliRunner().invoke(main, ["dump", str(so), *extra, "-o", str(out)])
        lines.append(f"[{label}] dump exit={res.exit_code}")
        if res.exit_code != 0:
            lines.append(f"[{label}] output={res.output[:400]}")
            continue
        snap = load_snapshot(out)
        lines.append(f"[{label}] evidence={_read(lambda: debug_info_evidence(snap))!r}")
        decl = _read(lambda: snap.declarations)
        for field in ("debug_layout", "debug_advanced", "types"):
            value = _read(lambda f=field: getattr(decl, f))
            lines.append(f"[{label}] declarations.{field}={str(value)[:200]}")
        lines.append(
            f"[{label}] provenance={_read(lambda: repr(snap.dump_provenance))[:500]}"
        )
    raise AssertionError("PROVENANCE\n" + "\n".join(lines))
