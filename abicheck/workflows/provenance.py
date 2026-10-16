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

"""Snapshot provenance: who/when/what-commit a dumped snapshot came from.

The one owner of the ``created_at``/``git_tag``/``build_id``/``git_commit``
stamp a dump records (snapshot schema v4). It used to live in the CLI
runtime, which left it reachable only through ``abicheck dump``; it is an
operation on a snapshot, not a terminal concern, so any front end that
writes a snapshot reaches it here.
"""

from __future__ import annotations

import datetime
import os
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..model import AbiSnapshot

__all__ = ["provenance_timestamp", "stamp_provenance"]


def provenance_timestamp(source_date_epoch: str | None) -> str:
    """ISO-8601 UTC timestamp, honouring ``SOURCE_DATE_EPOCH`` when valid."""
    if source_date_epoch:
        try:
            epoch = int(source_date_epoch.strip())
            return datetime.datetime.fromtimestamp(epoch, tz=datetime.UTC).isoformat()
        except (ValueError, OverflowError, OSError):
            # Non-numeric or out-of-range epoch — fall back to wall clock
            # rather than aborting the dump.
            pass
    return datetime.datetime.now(datetime.UTC).isoformat()


def stamp_provenance(
    snap: AbiSnapshot,
    *,
    git_tag: str | None,
    build_id: str | None,
    no_git: bool,
) -> None:
    """Fill provenance metadata on a snapshot (mutates in place).

    ``created_at`` honours ``SOURCE_DATE_EPOCH`` (the reproducible-builds
    standard): when set to a Unix timestamp, that fixed time is used instead of
    the wall clock, so two dumps of an identical library are byte-identical —
    enabling content-addressable caching and reproducible-build verification.
    An unset or malformed value falls back to the current time.
    """
    snap.created_at = provenance_timestamp(os.environ.get("SOURCE_DATE_EPOCH"))
    snap.git_tag = git_tag
    snap.build_id = build_id

    if not no_git:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                snap.git_commit = result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # git not available or not a repo — leave as None
