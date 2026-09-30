"""Build a genuinely legacy persisted snapshot for reliability tests.

A pre-fix baseline is not an in-memory ``AbiSnapshot`` with a flag flipped:
it is a *document* written by an older schema, carrying no ``*_fact`` keys and no
reliability markers,
whose trustworthiness the loader decides (``storage.snapshot_reliability_flags``
plus ``storage.fact_backfill``). :func:`as_legacy_baseline` produces exactly
that document from a modern snapshot and loads it back through the real
decoder, so a test asserts what a user comparing against an old baseline sees.
"""

from __future__ import annotations

from typing import Any

from abicheck.model import AbiSnapshot
from abicheck.serialization import snapshot_from_dict
from abicheck.storage.snapshot_encode import snapshot_to_dict


def _strip_fact_keys(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            k: _strip_fact_keys(v)
            for k, v in node.items()
            if not k.endswith(("_fact", "_facts_reliable"))
        }
    if isinstance(node, list):
        return [_strip_fact_keys(v) for v in node]
    return node


def legacy_document(
    snap: AbiSnapshot, schema_version: int, **overrides: Any
) -> dict[str, Any]:
    """*snap* as a schema-*schema_version* writer would have persisted it."""
    d = _strip_fact_keys(snapshot_to_dict(snap))
    d["schema_version"] = schema_version
    d.update(overrides)
    return d


def as_legacy_baseline(
    snap: AbiSnapshot, schema_version: int, **overrides: Any
) -> AbiSnapshot:
    """Round-trip *snap* through a schema-*schema_version* document."""
    return snapshot_from_dict(legacy_document(snap, schema_version, **overrides))
