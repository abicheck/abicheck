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

"""Shared loader and fixtures for the real-integration L2 profile tests.

`scripts/l2_real_profiles.py` is a script, not an importable package module, so
every test module that exercises it needs the same `importlib` load. Keeping
that here (rather than repeating it) also means both test modules share one
loaded module object, so a monkeypatch helper written against one works in the
other.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location(
    "l2_real_profiles", SCRIPTS / "l2_real_profiles.py"
)
assert _spec and _spec.loader
profiles = importlib.util.module_from_spec(_spec)
sys.modules["l2_real_profiles"] = profiles
_spec.loader.exec_module(profiles)


def materialize_operands(profile, prepared_root: Path) -> Path:
    """Create every operand *profile* declares, on both sides, under a root.

    Status tests need a prepared root that really holds what the profile says it
    needs, because readiness is answered from the operands rather than from the
    root's mere existence. Writing the paths out of the profile (rather than
    hard-coding a layout) is what keeps these tests honest when a profile's
    artifact or header paths change -- which is exactly what happened to SVS.
    """
    for side in ("old", "new"):
        root = profile.side_root(prepared_root, side)
        for lib in profile.l2_libraries:
            artifact = root / lib.artifact
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"\x7fELF")
            for header in lib.public_headers:
                target = root / header
                if target.suffix in (".h", ".hpp"):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("/* header */")
                else:
                    target.mkdir(parents=True, exist_ok=True)
                    (target / "api.h").write_text("/* header */")
    return prepared_root
