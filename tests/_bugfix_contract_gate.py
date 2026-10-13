"""Shared gate loader and fixtures for the bug-fix test-contract tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_bugfix_test_contract.py"
)
_spec = importlib.util.spec_from_file_location("check_bugfix_test_contract", _PATH)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
sys.modules["check_bugfix_test_contract"] = gate
_spec.loader.exec_module(gate)


COMPLETE_BODY = """\
## Bug-fix test contract

- Bug class: window size computed in KiB where zstd wants bytes
- Publicly observable failure: written .json.zst could not be read back
- Regression test fails on base: yes, test_zstd_round_trip_at_production_scale
- Negative control: gzip round-trip at the same scale is unaffected
- Public-surface test: through write_snapshot_bytes / read_snapshot_bytes
- Axes covered: zstd and gzip, both compression levels, Linux + macOS
- General invariant: every supported algorithm round-trips at production scale
"""


def _content_diff(path: str, added: str = "    assert f(2) == 4") -> str:
    """One file with one added line, in `git diff --unified=0 -M` shape."""
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,0 +2 @@\n"
        f"+{added}\n"
    )


@pytest.fixture(autouse=True)
def _no_inherited_body_file(monkeypatch: pytest.MonkeyPatch) -> None:
    # The gate reads BUGFIX_CONTRACT_BODY_FILE from the environment when
    # main() runs. A caller that validates a real PR description (verify.py's
    # bugfix-test-contract step) exports it, and every test here that expects
    # "no body file" would then read the real PR body instead. Each test that
    # needs a body file sets its own.
    monkeypatch.delenv("BUGFIX_CONTRACT_BODY_FILE", raising=False)
