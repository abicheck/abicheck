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

"""``dump --dump-manifest``: parse the multi-TU manifest (ADR-050 D3) and
reject the ``-H``/``--header`` list it is exclusive with."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click


def load_dump_manifest_or_reject(
    dump_manifest_path: Path | None,
    headers: tuple[Path, ...],
) -> Any:
    """Parse ``--dump-manifest``, rejecting the flag it is exclusive with.

    A manifest's own ``roots`` field and base profile declare the public
    surface, so ``-H``/``--header`` would be a second, conflicting
    declaration. Returns the parsed manifest, or ``None`` when no
    ``--dump-manifest`` was given.
    """
    if dump_manifest_path is None:
        return None
    if headers:
        raise click.UsageError(
            "--dump-manifest and -H/--header are mutually exclusive -- the "
            "manifest's own 'roots' field declares the public surface instead."
        )
    from ...errors import ManifestValidationError
    from ...workflows.extraction import load_manifest

    try:
        parsed_dump_manifest = load_manifest(dump_manifest_path)
    except ManifestValidationError as exc:
        raise click.UsageError(str(exc)) from exc
    return parsed_dump_manifest
