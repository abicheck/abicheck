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

"""Persisted ``BundleFacts`` field validators, its JSON container-node
budget check, and ``load_bundle_facts``'s full dispatch body, split out of
``bundle_facts.py``/``serialization.py`` (both flat, not-yet-ADR-061-
migrated root modules) purely to stay under ``bundle_facts.py``'s 800-line
production cap and ``serialization.py``'s ADR-061 no-growth debt entry
(``architecture/debt.yaml``). Fits `storage/`'s own ADR-061 D1 remit
("serialize snapshots/baselines, own their schemas") and its `model`-only
dependency rule: every function here depends on nothing first-party but
``errors`` (a `public_root_surfaces` exemption) and `storage.json_budget`
(same package) -- ``load_bundle_facts_dispatch`` takes its `bundle_facts.py`/
`snapshot_io.py`/`serialization.py` collaborators as injected callables
instead of importing them, since none of those three is classified into a
layer yet and this module, migrated into `storage/`, may not import an
unclassified first-party module directly. A leaf itself: no import of
``bundle_facts.py`` or ``serialization.py``, so importing it introduces no
cycle either way.

``validated_alias_map``/``validated_filename_map`` are duplicated from
(not imported from) ``serialization._validated_alias_map``/
``_validated_filename_map`` -- see ``bundle_facts.py``'s own module-level
comment for why importing that module directly isn't an option.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


def load_bundle_facts_dispatch(
    path: str | Path,
    format: str,
    *,
    read_snapshot_text: Callable[[str | Path], str],
    maybe_read_bundle_facts_archive: Callable[..., Any],
    bundle_facts_from_dict: Callable[[dict[str, Any]], Any],
    snapshot_from_dict: Callable[[dict[str, Any]], Any],
    max_json_object_nodes: int,
) -> Any:
    """Full body of ``serialization.load_bundle_facts`` -- moved here (not
    left in that module) because ``serialization.py`` is an ADR-061
    no-growth-tracked legacy file (``architecture/debt.yaml``): this is a
    genuinely new, additive capability (a container-node budget on the
    plain-JSON path, matching the archive path), not a fix to code already
    there, so it belongs where growth is still allowed.

    Every collaborator (`read_snapshot_text`, `maybe_read_bundle_facts_
    archive`, `bundle_facts_from_dict`, `snapshot_from_dict`) is injected
    by the caller rather than imported here -- ``storage/`` may only import
    `model` (ADR-061), and none of `bundle_facts.py`/`snapshot_io.py`/
    `serialization.py` are classified into a layer yet, so importing any of
    them directly from a migrated `storage/` module would itself be an
    architecture violation. Caller resolves *max_json_object_nodes*'s
    ``None`` default (``bundle_facts.DEFAULT_MAX_JSON_OBJECT_NODES``) before
    calling, since that constant lives in one of those unclassified modules
    too."""
    import json as _json

    archived = maybe_read_bundle_facts_archive(
        path, format, snapshot_from_dict=snapshot_from_dict, max_json_object_nodes=max_json_object_nodes
    )
    if archived is not None:
        return archived
    raw_text = read_snapshot_text(path)
    check_bundle_facts_json_budget(
        raw_text.encode("utf-8"), max_json_object_nodes, path=path, description="bundle facts JSON"
    )
    return bundle_facts_from_dict(_json.loads(raw_text))


def check_bundle_facts_json_budget(
    raw: bytes,
    max_json_object_nodes: int,
    *,
    path: str | Path,
    description: str,
) -> None:
    """Shared container-node/nesting-depth budget check for BundleFacts
    JSON -- one implementation for both the G40 archive path's per-blob
    decode (``bundle_facts.read_bundle_facts_archive``) and
    ``serialization.load_bundle_facts``'s plain ``.json``/``.json.zst``
    path, which previously enforced no budget at all (Codex review, fresh
    evidence: identical bytes were checked one way and not the other).
    Raises :class:`~abicheck.errors.SnapshotError`; never decodes *raw*
    itself. A leaf function (only ``errors``/``json_budget``, both
    dependency-free) -- see this module's own docstring for why it lives
    here rather than in ``bundle_facts.py`` itself."""
    from ..errors import SnapshotError
    from .json_budget import (
        JsonContainerBudgetExceeded,
        JsonNestingTooDeepError,
        check_json_container_budget,
    )

    try:
        check_json_container_budget(raw, max_json_object_nodes)
    except JsonContainerBudgetExceeded:
        raise SnapshotError(
            f"{path}: {description} contains more than "
            f"{max_json_object_nodes} JSON containers -- refusing to decode "
            "(possible container-count amplification attack; pass a larger "
            "max_json_object_nodes if this is a known-large, trusted payload)"
        ) from None
    except JsonNestingTooDeepError:
        raise SnapshotError(f"{path}: {description} is too deeply nested to parse") from None


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
