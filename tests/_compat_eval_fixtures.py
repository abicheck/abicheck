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

"""Shared fixtures for the ADR-049 front-end resolver test modules.

A non-``test_`` helper module (the same pattern ``_detector_mutations.py``
follows) so ``test_compatibility_evaluation_frontend.py`` — which resolves
values — and ``test_compatibility_evaluation_receipts.py`` — which checks
what the receipt records — build their inputs the same way.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from abicheck.compatibility_evaluation_frontend import (
    resolve_compatibility_evaluation_config,
)
from abicheck.policy_file import PolicyFile


def write_pack(path: Path, *, pack_id: str, kind: str, assignments: str) -> Path:
    path.write_text(
        textwrap.dedent(f"""\
            id: {pack_id}
            version: 1
            kind: {kind}
            assignments:
{textwrap.indent(assignments.strip(), "              ")}
            """)
    )
    return path


def policy_file(tmp_path: Path, body: str) -> PolicyFile:
    path = tmp_path / "policy.yml"
    path.write_text(textwrap.dedent(body))
    return PolicyFile.load(path)


def resolve(**kwargs):
    return resolve_compatibility_evaluation_config(**kwargs)


