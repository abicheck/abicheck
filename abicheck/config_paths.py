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

"""Shared candidate locations for a project's ``.abicheck.yml``.

Historically ``.abicheck.yml`` was recognized only at a directory's own root.
Many projects prefer to keep tool configuration out of the repo root and
already have a convention for that — GitHub's own ``.github/`` directory,
which is where CI workflows, issue templates, and `CODEOWNERS` already live.
This module is the single place that enumerates the recognized locations
within one directory, so every discovery entry point (`compare`'s
``discover_project_config()`` in ``cli_helpers_compare.py``, `dump`'s
``discover_build_config()`` in ``buildsource/inline.py``) agrees on the same
set instead of drifting independently.

Precedence within one directory, checked in order:

1. ``.abicheck.yml`` — the original, root-level spelling. Always wins, so an
   existing config is never shadowed by a newly-added ``.github`` copy.
2. ``.github/.abicheck.yml`` — alongside workflows/``CODEOWNERS``.
3. ``.github/abicheck/.abicheck.yml`` — a dedicated subdirectory, for a
   project that wants its abicheck config kept apart from other ``.github``
   content (or that already uses ``.github/<tool>/`` for several tools).

Only the *filename* location changes — the file's own content, schema, and
strictness rules (see ``docs/reference/config-file.md``) are unaffected
regardless of which of these three paths it was found at.
"""

from __future__ import annotations

from pathlib import Path

#: The recognized config filename itself, unchanged regardless of location.
CONFIG_FILE_NAME = ".abicheck.yml"

#: Paths, relative to a directory, checked for a project config file, in
#: precedence order (first match wins). See the module docstring.
CONFIG_RELATIVE_CANDIDATES: tuple[str, ...] = (
    CONFIG_FILE_NAME,
    f".github/{CONFIG_FILE_NAME}",
    f".github/abicheck/{CONFIG_FILE_NAME}",
)


def find_config_in_dir(directory: Path) -> Path | None:
    """Return the first recognized config file directly inside *directory*.

    Checks :data:`CONFIG_RELATIVE_CANDIDATES` in order and returns the first
    one that exists as a regular file, or ``None`` if none do. Does not walk
    up to parent directories — callers that want that (``compare``'s
    project-root discovery) do it themselves, one directory at a time.
    """
    for rel in CONFIG_RELATIVE_CANDIDATES:
        candidate = directory / rel
        if candidate.is_file():
            return candidate
    return None


def discover_build_config(source_tree: Path | None) -> Path | None:
    """Return the project config at *source_tree*'s own root, if present.

    Checks the same recognized locations :func:`find_config_in_dir` does —
    the root spelling, ``.github/.abicheck.yml``, and
    ``.github/abicheck/.abicheck.yml`` — but only at *source_tree* itself,
    never walking up to its parents: ``dump --sources``/``--build-info``
    is anchored to the tree it was pointed at, unlike `compare`'s
    ``discover_project_config()`` (``cli_helpers_compare.py``), which walks
    up from the current directory.
    """
    if source_tree is None or not source_tree.is_dir():
        return None
    return find_config_in_dir(source_tree)
