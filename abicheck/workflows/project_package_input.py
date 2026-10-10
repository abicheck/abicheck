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

"""A stored ``ProjectSnapshot`` package as a ``resolve_input`` operand --
the package directory, or its one-file zip transport (storage-format-v2
A1.1).

The archive is unpacked into a private temporary directory, read, and the
directory removed before returning: the single-artifact reader decodes the
whole package into one ``AbiSnapshot``, so nothing outlives this call. (The
CLI's release fan-out, which reads members lazily, unpacks at its argument
boundary instead -- ``frontends/cli/options/operand_path.py``.)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..errors import SnapshotError
from ..model import AbiSnapshot
from .storage import is_project_package_archive, unpack_project_package

__all__ = ["resolve_project_package"]


def resolve_project_package(path: Path) -> AbiSnapshot:
    """*path* -- a package directory or a package archive -- as one snapshot."""
    if path.is_dir():
        return _resolve_project_snapshot_directory(path)
    if not is_project_package_archive(path):
        raise SnapshotError(f"'{path}' is not a ProjectSnapshot package")
    with tempfile.TemporaryDirectory(prefix="abicheck-package-") as workdir:
        unpacked = Path(workdir) / "package"
        unpack_project_package(path, unpacked)
        # The notice names the operand the user gave, not the temp unpack dir.
        return _resolve_project_snapshot_directory(unpacked, display_path=path)


def _resolve_project_snapshot_directory(
    path: Path, *, display_path: Path | None = None
) -> AbiSnapshot:
    """*path* as a directory-backed ADR-062/ADR-063 storage-v2
    `ProjectSnapshot` package (`project_snapshot_legacy
    .read_legacy_snapshot_document` — manifest.json + refs/ + objects/,
    produced via `project_snapshot_legacy.write_legacy_snapshot_package` --
    no `dump` CLI flag writes one today, see that module's own docstring for
    why `dump`'s real output is the single-file sectioned shape instead),
    decoded into an `AbiSnapshot` exactly the way a legacy `.abi.json` file
    already is (`serialization.snapshot_from_dict`).

    Single-artifact packages only, matching what `write_legacy_snapshot_
    package` ever writes (ADR-062 A1.3's "one-artifact project" shape) — a
    real multi-library `ProjectSnapshot` is real, separately-scoped future
    work this function does not guess at (see `read_legacy_snapshot_document`'s
    own docstring for the same limit).

    Raises `SnapshotError` for anything that goes wrong reading or decoding
    the package -- a missing/malformed `manifest.json`, a multi-artifact
    package with no artifact named explicitly, an unreadable section object
    -- the identical translation every other `resolve_input` branch applies
    at its own boundary.
    """
    from ..project_snapshot_legacy import read_legacy_snapshot_document
    from ..project_snapshot_store import read_manifest_summary
    from ..serialization import snapshot_from_dict

    try:
        document = read_legacy_snapshot_document(path)
    except (SnapshotError, OSError, KeyError, ValueError, TypeError) as exc:
        raise SnapshotError(
            f"Failed to load ProjectSnapshot package '{path}': {exc}"
        ) from exc
    try:
        snapshot = snapshot_from_dict(document)
    except (TypeError, ValueError, KeyError, UnicodeDecodeError) as exc:
        raise SnapshotError(
            f"Failed to decode ProjectSnapshot package '{path}': {exc}"
        ) from exc
    # ADR-062 D2: a package produced under a different extractor/resolver
    # generation is read, never refused -- but the run that loaded it says so.
    # `read_legacy_snapshot_document` above already validated the manifest.
    notice = read_manifest_summary(path).semantics_notice
    if notice:
        shown = display_path if display_path is not None else path
        snapshot.load_notices = (f"stored package '{shown}': {notice}",)
    return snapshot
