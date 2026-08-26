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

"""Persisted ``BundleFacts`` field validators, split out of
``bundle_facts.py`` (a flat, not-yet-ADR-061-migrated root module) purely
to stay under that module's 800-line production cap. Fits `storage/`'s own
ADR-061 D1 remit ("serialize snapshots/baselines, own their schemas") and
its `model`-only dependency rule -- these functions depend on nothing at
all. A dependency-free leaf: no import of ``bundle_facts.py`` or
``serialization.py``, so importing it introduces no cycle either way.

Duplicated from (not imported from) ``serialization._validated_alias_map``/
``_validated_filename_map`` -- see ``bundle_facts.py``'s own module-level
comment for why importing that module directly isn't an option.
"""

from __future__ import annotations


def validated_alias_map(raw: object) -> dict[str, tuple[str, ...]]:
    """Validate and convert a persisted ``filesystem_aliases`` mapping --
    rejects a non-mapping container, a non-list value, and a list with a
    non-string element, rather than silently iterating a stray string's
    characters into single-letter "aliases"."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"bundle facts: 'filesystem_aliases' must be a mapping, got "
            f"{type(raw).__name__}"
        )
    aliases: dict[str, tuple[str, ...]] = {}
    for name, values in raw.items():
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError(
                f"bundle facts: 'filesystem_aliases[{name!r}]' must be a list of "
                f"strings, got {values!r}"
            )
        aliases[name] = tuple(values)
    return aliases


def validated_filename_map(raw: object) -> dict[str, str]:
    """Validate and convert a persisted ``library_filenames`` mapping --
    rejects a non-string value instead of silently coercing it
    (``str(None)`` -> ``"None"``)."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"bundle facts: 'library_filenames' must be a mapping, got "
            f"{type(raw).__name__}"
        )
    filenames: dict[str, str] = {}
    for name, filename in raw.items():
        if not isinstance(filename, str):
            raise ValueError(
                f"bundle facts: 'library_filenames[{name!r}]' must be a string, "
                f"got {filename!r}"
            )
        filenames[name] = filename
    return filenames
