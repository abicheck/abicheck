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

"""Build the sdist/wheel, `twine check` them, and validate their metadata.

Wraps the three steps the CI `fair-metadata` job runs separately (`python -m
build`; `twine check dist/*`; `scripts/check_distribution_metadata.py`) into
one command so `scripts/verify.py`'s `distribution-build` step is
self-contained — running it against a clean checkout (with `build`/`twine`
installed) doesn't require a caller to have already populated `dist/`.

`twine check` needs explicit file paths, not a shell glob (`dist/*`); this
resolves them itself with `Path.glob` so no shell is involved.

Run locally:

    python scripts/build_and_check_distribution.py
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    if DIST.exists():
        shutil.rmtree(DIST)

    build = subprocess.run([sys.executable, "-m", "build"], cwd=ROOT)
    if build.returncode != 0:
        return 1

    artifacts = sorted(DIST.glob("*.tar.gz")) + sorted(DIST.glob("*.whl"))
    if not artifacts:
        print(
            "ERROR: `python -m build` produced no artifacts in dist/", file=sys.stderr
        )
        return 1

    twine = subprocess.run(
        [sys.executable, "-m", "twine", "check", *(str(p) for p in artifacts)],
        cwd=ROOT,
    )
    if twine.returncode != 0:
        return 1

    metadata = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_distribution_metadata.py")],
        cwd=ROOT,
    )
    return 0 if metadata.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
