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

"""Manifest preflight for the typed compare pipeline.

Both functions run ahead of any dry-run emit, so a dry run never reports
"ok" for an invocation the real run would reject. They raise the typed
manifest errors; the CLI adapter renders them as usage errors.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..extraction import DumpManifest


def load_compare_dump_manifests(
    old_dump_manifest: Path | None,
    new_dump_manifest: Path | None,
) -> tuple[DumpManifest | None, DumpManifest | None]:
    """Load the two dump manifests; ``None`` for a side given none."""
    from ..extraction import load_manifest

    old_manifest_obj: DumpManifest | None = None
    new_manifest_obj: DumpManifest | None = None
    if old_dump_manifest is not None:
        old_manifest_obj = load_manifest(old_dump_manifest)
    if new_dump_manifest is not None:
        new_manifest_obj = load_manifest(new_dump_manifest)
    return old_manifest_obj, new_manifest_obj


def validate_compare_pack_manifests(
    pack_paths: Any,
    *,
    policy_file_path: Path | None,
    contract_evaluation: bool,
) -> None:
    """Validate the ``--pack`` manifests a compare run names.

    ADR-049 D8 pack-vs-pack conflict detection is deliberately *not* here --
    see ``validate_pack_manifests`` for which layers it needs that are not
    resolved this early.
    """
    from ..pack_resolution import validate_pack_manifests

    validate_pack_manifests(
        pack_paths,
        policy_file_path=policy_file_path,
        contract_evaluation=contract_evaluation,
    )
