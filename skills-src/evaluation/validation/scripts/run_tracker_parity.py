#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Thin alias for ``validate.py --source tracker`` (kept for compatibility).

The fetch → extract → compare engine now lives in ``conda_harness`` and the
orchestration in ``validate``; this wrapper preserves the original
``run_tracker_parity.py <library>`` invocation. New work should call
``validate.py --source tracker --lib <library>`` directly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import validate  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Translate the legacy positional CLI into a ``validate`` tracker run."""
    ap = argparse.ArgumentParser(
        description="Score abicheck against the abi-laboratory oracle (alias for "
        "validate.py --source tracker)."
    )
    ap.add_argument("library", help="tracker library slug (must already be harvested)")
    ap.add_argument(
        "--pkg", help="conda-forge package name if it differs from the slug"
    )
    ap.add_argument(
        "--subdir", default="linux-64", help="conda subdir (default: linux-64)"
    )
    ap.add_argument("--max-pairs", type=int, default=0, help="limit pairs (0 = all)")
    args = ap.parse_args(argv)

    forwarded = ["--source", "tracker", "--lib", args.library, "--subdir", args.subdir]
    if args.pkg:
        forwarded += ["--pkg", args.pkg]
    if args.max_pairs:
        forwarded += ["--max-pairs", str(args.max_pairs)]
    return validate.main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
