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

"""`scan --against`'s own usage-error validation -- split out of
`cli_scan.py` (which sits exactly at the AI-readiness 2000-line hard cap)
rather than grown inline.

`--against`'s Click option (`type=click.Path(exists=True, path_type=Path)`,
no `dir_okay=False`) accepts anything that exists; this module is what
narrows that back down to the two shapes `resolve_input` actually knows how
to turn into a single comparison snapshot -- a single file, or a
`dump --project-snapshot-dir` `ProjectSnapshot` package directory
(ADR-062/ADR-063 storage-v2) -- and rejects the two it doesn't: a package
archive, and a plain directory of libraries (both belong to
`abicheck compare OLD_PACKAGE NEW_PACKAGE`'s fan-out instead).
"""

from __future__ import annotations

from pathlib import Path

import click


def reject_unsupported_against_operand(against: Path | None) -> None:
    """Raise `click.UsageError` if *against* is a package archive or a
    plain (non-`ProjectSnapshot`) directory -- a no-op for `None`, a single
    file, or a real `ProjectSnapshot` package directory.

    Checked before `--dry-run`, so dry-run and the real run agree; before
    this existed, a package archive passed Click's own validation and only
    failed later inside `resolve_input()` with an opaque "cannot detect
    input format" instead of this clear, immediate usage error (Codex
    review, on the package-archive case this function's `is_package` half
    already covered before the directory half was added).
    """
    if against is None:
        return
    from ...workflows.extraction import is_package
    from ...workflows.storage import is_project_snapshot_package_dir

    if is_package(against):
        raise click.UsageError(
            f"--against does not accept a package archive ({against}); "
            "packages are not supported here -- use `abicheck compare "
            "OLD_PACKAGE NEW_PACKAGE` for package-to-package comparisons."
        )
    if against.is_dir() and not is_project_snapshot_package_dir(against):
        raise click.UsageError(
            f"--against does not accept a plain directory ({against}); only "
            "a single file or a `--project-snapshot-dir` package dir is "
            "supported here -- use `abicheck compare OLD_PACKAGE "
            "NEW_PACKAGE` for directory-to-directory comparisons."
        )
