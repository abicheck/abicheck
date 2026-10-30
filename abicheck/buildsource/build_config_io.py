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

"""Reading a ``.abicheck.yml`` when the *bytes* matter, not just the values.

:func:`~abicheck.buildsource.build_config.load_build_config` answers "what
did the project configure"; this module answers "and from exactly which
content", which is what an ADR-049 D6 receipt needs before it may claim a
project config supplied a field (Codex review, fresh evidence: every
project-derived provenance entry named the file but could not prove which
revision of it produced the persisted values).

Its own module rather than another function in ``build_config.py``: this is
a genuinely separate question about the same file. A **one-directional**
dependency -- it imports :class:`~abicheck.buildsource.build_config.
BuildConfig`, and ``build_config`` does not import this back -- so no import
cycle forms and callers that only need the values keep using
``load_build_config`` unchanged.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .build_config import KEY_TABLE, BuildConfig
from .build_config_overrides import apply_config_overrides, validated_config_overrides


def load_build_config_with_digest(
    path: Path | None, *, config_overrides: Mapping[str, object] | None = None
) -> tuple[BuildConfig, str | None]:
    """Load a ``.abicheck.yml``, and digest the bytes that were parsed.

    One read produces both, so the digest always describes the content the
    returned config was built from. Hashing the path separately could pair
    one revision's digest with another's values -- the trap
    ``compatibility_evaluation_frontend.ProjectCompatibilityInputs.sha256``
    documents as its reason for refusing to compute this itself, and the
    reason this function exists rather than a `hashlib` call at the call
    site.

    The digest covers raw bytes, not decoded text: text mode normalizes
    newlines, so a CRLF file would otherwise hash as its LF translation and
    no consumer could match it against the file on disk. ``None`` means
    there was no file to read -- the same all-defaults case
    ``load_build_config`` tolerates.

    Raises :class:`ValueError` for an unreadable or malformed document, with
    the same message shape ``load_build_config`` raises, so a caller can
    handle either identically.
    """
    if path is None or not path.is_file():
        cfg = BuildConfig.from_dict(
            apply_config_overrides(None, config_overrides, KEY_TABLE)
        )
        return cfg, None
    import yaml  # hard dep; imported out of the try so the except can name it

    try:
        data = path.read_bytes()
        raw = yaml.safe_load(data.decode("utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read build config {path}: {exc}") from exc
    digest = hashlib.sha256(data).hexdigest()
    if not isinstance(raw, dict):
        raw = None
    if raw is None and not config_overrides:
        return BuildConfig(), digest
    # The digest covers the file's bytes only: an override is not file content,
    # so a receipt that needs it must record the override set separately.
    return BuildConfig.from_dict(
        apply_config_overrides(raw, config_overrides, KEY_TABLE)
    ), digest


def validate_config_overrides(overrides: Mapping[str, object]) -> dict[str, Any]:
    """*overrides* checked against :data:`KEY_TABLE` (ADR-091 D4), as a nested mapping."""
    return validated_config_overrides(overrides, KEY_TABLE)


def raw_with_config_overrides(
    raw: dict[str, Any] | None, overrides: Mapping[str, object] | None
) -> dict[str, Any]:
    """The raw ``.abicheck.yml`` mapping *raw* with *overrides* laid over it.

    For a reader that parses one block of the file itself (``capture-variants``
    reads ``bundle_variants:``) and so cannot use :class:`BuildConfig`. The
    overrides are validated against :data:`KEY_TABLE`, the same schema the file
    passes, so the block read is the one the run states.
    """
    return apply_config_overrides(raw, overrides, KEY_TABLE)
