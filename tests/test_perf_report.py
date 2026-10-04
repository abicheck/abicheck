from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import perf_report  # noqa: E402


def test_report_has_every_section_and_writes_file(tmp_path):
    out = tmp_path / "r.md"
    assert perf_report.main(["--n", "20", "--top", "3", "-o", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    for heading in (
        "## Hot functions",
        "## Same-argument repeats",
        "## Calls per declaration",
        "## Anti-pattern sites",
    ):
        assert heading in text
    hot = text.split("## Hot functions")[1].split("##")[0]
    assert "abicheck/" in hot and "(compare)" not in hot
