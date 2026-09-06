#!/usr/bin/env python3
"""Run an ABI compatibility check from Python instead of the CLI -- the
same classification `abicheck compare` uses, called directly so a build
script or test suite can inspect the result programmatically instead of
parsing CLI output."""

import sys
from pathlib import Path

from abicheck.service import run_compare

result = run_compare(
    Path("libshapes_v1.so"),
    Path("libshapes_v2.so"),
    old_headers=[Path("v1/shapes.h")],
    new_headers=[Path("v2/shapes.h")],
)

print(f"Verdict: {result.diff.verdict.value}")
for change in result.diff.changes:
    print(f"  - {change.kind.value}: {change.description}")

sys.exit(result.exit_decision.code)
