"""TEMPORARY macOS diagnostic: where does the one-side-headers evidence come from?

Runs only on macOS. It dumps each side of the depth fixture both ways and fails
on purpose with the provenance it saw, so the macOS CI log carries it. Removed
once the expectation of test_common_evidence_depth is decided.
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
    for label, extra in (
        ("binary-only", []),
        ("with-headers", ["-H", str(tmp_path / "a.h")]),
    ):
        out = tmp_path / f"{label}.json"
        res = CliRunner().invoke(main, ["dump", str(so), *extra, "-o", str(out)])
        lines.append(f"[{label}] dump exit={res.exit_code}")
        if res.exit_code != 0:
            lines.append(res.output[:400])
            continue
        snap = load_snapshot(out)
        ev = debug_info_evidence(snap)
        lines.append(f"[{label}] debug_info_evidence={ev!r}")
        decl = snap.declarations
        lines.append(
            f"[{label}] debug_layout={getattr(decl, 'debug_layout', None)!r}"[:300]
        )
        lines.append(
            f"[{label}] debug_advanced={getattr(decl, 'debug_advanced', None)!r}"[:300]
        )
        lines.append(
            f"[{label}] types={len(snap.types)} functions={len(snap.functions)}"
        )
        lines.append(f"[{label}] provenance={snap.dump_provenance!r}"[:600])
    raise AssertionError("PROVENANCE\n" + "\n".join(lines))
