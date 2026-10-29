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

"""Click-side preflight for ``compare``: the dump and pack manifests.

Parsed before ``--dry-run`` returns, so a dry run and the real run refuse the
same manifests; the loading and validation are in
``workflows.compare.manifests``, rendered here as the usage errors
``compare`` prints. (The ADR-049 evaluation configuration moved into the
single-pair session, ``workflows/compare/session.py``.)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from ...workflows.compare_configuration import ResolvedCompareConfig  # noqa: F401
    from ...workflows.extraction import DumpManifest


def _preflight_manifests_and_audit(
    *,
    old_dump_manifest: Path | None,
    new_dump_manifest: Path | None,
    pack_paths: Any,
    policy_file_path: Path | None,
    contract_evaluation: bool,
) -> tuple[DumpManifest | None, DumpManifest | None]:
    """Load the dump manifests and reject what a dry run must not report "ok".

    The loading and validation are in ``workflows.compare.manifests``; this
    renders their typed refusals as the usage errors ``compare`` prints.
    """
    from ...errors import ManifestValidationError, PackManifestError
    from ...workflows.compare.manifests import (
        load_compare_dump_manifests,
        validate_compare_pack_manifests,
    )

    try:
        manifests = load_compare_dump_manifests(old_dump_manifest, new_dump_manifest)
    except ManifestValidationError as exc:
        raise click.UsageError(str(exc)) from exc
    try:
        validate_compare_pack_manifests(
            pack_paths,
            policy_file_path=policy_file_path,
            contract_evaluation=contract_evaluation,
        )
    except PackManifestError as exc:
        raise click.UsageError(str(exc)) from exc
    return manifests
