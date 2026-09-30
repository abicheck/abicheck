# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Round-trip an in-memory snapshot as a *legacy* stored document.

ADR-063 5B: header-fact detectors gate on each declaration's ``FactStatus``,
and for a stored baseline that status is derived on load
(``storage.fact_backfill``). A scenario about "which evidence can this
document be trusted for" (non-header, unknown producer, pre-v19 clang,
legacy hybrid) therefore has to go through that load, not through an
in-memory object whose statuses nobody derived.
"""

from __future__ import annotations

import warnings
from typing import Any

from abicheck.model import AbiSnapshot
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.storage.fact_schema_versions import (
    _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS,
)

LEGACY_SCHEMA = _MIN_SCHEMA_VERSION_FOR_DEPRECATION_FACTS - 1


def _strip_facts(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_facts(v) for k, v in node.items() if not k.endswith("_fact")}
    if isinstance(node, list):
        return [_strip_facts(v) for v in node]
    return node


def load_as_legacy(snap: AbiSnapshot, **overrides: Any) -> AbiSnapshot:
    """*snap* as a pre-5B document (no ``*_fact`` keys) loaded from disk."""
    doc = _strip_facts(snapshot_to_dict(snap))
    doc["schema_version"] = LEGACY_SCHEMA
    doc.update(overrides)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return snapshot_from_dict(doc)
