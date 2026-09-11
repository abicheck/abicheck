# Copyright 2026 Nikolay Petrov
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

"""Serialization helpers — AbiSnapshot ↔ JSON.

ADR-061 gap B/E: this module is a thin, delegation-only public compatibility
facade (``architecture/modules.yaml``'s ``public_root_surfaces`` — this
module itself stays deliberately unclassified). The real codec —
``snapshot_to_dict``/``snapshot_to_json``/``snapshot_content_digest``/
``decode_snapshot``/``finalize_snapshot``/``load_snapshot_document``/
``save_snapshot``/``write_snapshot``, the schema-version history, and every
per-field decode rule — lives in :mod:`abicheck.storage.snapshot_codec`,
classified ``storage``. This facade exists for two reasons a genuinely
``storage``-classified module cannot satisfy on its own:

1. **Two pipeline steps are not storage's to own.** ``snapshot_from_dict``'s
   historical five-step pipeline threads through a real ``workflows`` call
   (``backfill_python_ext_from_evidence`` — evidence-derived extraction
   logic, not a fact lookup) and a real ``policy`` call
   (``degraded_reliability_facts`` — an assurance judgement over an
   already-decoded snapshot). Neither fits `storage`'s own
   ``may_import: [model]``; ``storage.snapshot_codec.decode_snapshot``
   returns a snapshot before either has run, and this module's own
   ``snapshot_from_dict`` runs them in between ``decode_snapshot`` and
   ``storage.snapshot_codec.finalize_snapshot`` — see that module's own
   docstring for the fuller account, and the ADR-061 gap E closure history
   in ``docs/contribute/known-gaps.md`` for how the two prior slices
   (the ``python_ext``/``probe_harness`` couplings) closed the same way.
2. **The ``bundle_facts_to_dict``/``bundle_facts_from_dict``/
   ``load_bundle_facts``/``save_bundle_facts`` wrappers below need
   ``storage.bundle_facts_codec``, which itself needs this module's own
   ``snapshot_to_dict``/``snapshot_from_dict`` — the one real, unavoidable
   ``serialization.py <-> storage.bundle_facts_codec`` two-file cycle,
   already documented at those wrappers' own definitions below and kept
   dynamic via ``importlib.import_module`` exactly as before this module's
   own classification changed (not a new ``IMPORT_CYCLE_ALLOWLIST`` entry).

Every public name below keeps its historical, documented signature, so
every existing ``from abicheck.serialization import ...`` caller (and the
Python API docs) resolve unchanged. ``__all__`` states that documented
surface explicitly (Codex review) -- `architecture/modules.yaml`'s
`public_root_surfaces` treatment means `scripts/check_architecture.py`'s
`facade-*` checks don't apply here the way they do to a `facades`-listed
module, so `decode_snapshot`/`finalize_snapshot` (this module's own
orchestration helpers, not part of the documented surface) and the stdlib
imports below stay accessible as module attributes but are not advertised
via `__all__`/`from abicheck.serialization import *`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

__all__ = [
    "SCHEMA_VERSION",
    "bundle_facts_from_dict",
    "bundle_facts_to_dict",
    "from_sectioned_document",
    "is_sectioned_document",
    "load_bundle_facts",
    "load_snapshot",
    "load_snapshot_document",
    "save_bundle_facts",
    "save_snapshot",
    "snapshot_content_digest",
    "snapshot_from_dict",
    "snapshot_to_dict",
    "snapshot_to_json",
    "to_sectioned_document",
    "write_snapshot",
]

if TYPE_CHECKING:
    from .model.bundle_facts import BundleFacts
    from .snapshot_io import SnapshotWriteResult
from .model import AbiSnapshot
from .policy.analysis_assurance_degraded_facts import degraded_reliability_facts
from .storage.sectioned_document import (
    from_sectioned_document as from_sectioned_document,
    is_sectioned_document as is_sectioned_document,
    to_sectioned_document as to_sectioned_document,
)
from .storage.snapshot_codec import (
    SCHEMA_VERSION as SCHEMA_VERSION,
    decode_snapshot,
    finalize_snapshot,
    load_snapshot_document as load_snapshot_document,
    save_snapshot as save_snapshot,
    snapshot_content_digest as snapshot_content_digest,
    snapshot_to_dict as snapshot_to_dict,
    snapshot_to_json as snapshot_to_json,
    write_snapshot as write_snapshot,
)
from .workflows.snapshot_load import backfill_python_ext_from_evidence


def snapshot_from_dict(d: dict[str, Any]) -> AbiSnapshot:
    """Rehydrate an :class:`AbiSnapshot` from its ``snapshot_to_dict()``
    (or single-file sectioned, ADR-062/063 Phase 8) shape.

    The real per-field decode rules, schema-version gates, and reliability-
    flag derivations live in
    :func:`abicheck.storage.snapshot_codec.decode_snapshot` — this function
    is the one place that stitches that storage-legal decode back together
    with the two steps storage cannot own (see this module's own
    docstring): the ``workflows`` evidence backfill, and the ``policy``
    degraded-facts load-time warning. The storage-legal tail (ELF-binding
    backfill, anonymous-type-spelling normalization, anonymous
    closure-identity renumbering) runs last, via
    :func:`abicheck.storage.snapshot_codec.finalize_snapshot`.
    """
    # `SCHEMA_VERSION` here is *this module's own* (patchable) global -- see
    # `storage.snapshot_codec.decode_snapshot`'s own `max_known_schema_version`
    # docstring note for why it must be threaded through explicitly rather
    # than read off that module's own separately-bound constant.
    snap, _schema_version = decode_snapshot(d, max_known_schema_version=SCHEMA_VERSION)

    # ADR-061 gap E: evidence-derived backfill (real extraction logic, not a
    # fact lookup) cannot live in a `storage`-classified decode step, so it
    # runs here as an explicit post-load step from `workflows` — see
    # workflows/snapshot_load.py's own docstring.
    backfill_python_ext_from_evidence(snap, d)

    # A degraded *_facts_reliable flag used to load with no signal at all --
    # the flag itself was computed correctly, but nothing ever told the
    # person running the comparison that any detector would decline to
    # trust a stale-but-real-looking fact on this snapshot. Two separate
    # situations both leave a flag False, and both need the same visible
    # signal rather than silence:
    #   (1) THIS snapshot's own schema_version predates SCHEMA_VERSION, and
    #       the gap crossed one of the per-flag thresholds documented on
    #       `storage.snapshot_codec.SCHEMA_VERSION` -- the direction every
    #       CI baseline actually hits, since a baseline is committed once
    #       and outlives however many abicheck pin bumps happen before it's
    #       next regenerated.
    #   (2) schema_version reads as CURRENT, but an explicit False marker in
    #       `d` carried a degraded flag forward from an earlier, genuinely
    #       older extraction -- the "explicit-marker-wins" round-trip-
    #       stability path every flag's own computation in
    #       `storage.snapshot_codec.decode_snapshot` already implements.
    #       `snapshot_to_dict` always re-stamps schema_version to the
    #       CURRENT SCHEMA_VERSION on save (it describes the writing tool's
    #       format capability, not the snapshot's true field-fact origin),
    #       so a legacy snapshot that was loaded and simply re-saved reads
    #       as current-schema on its next load even though the underlying
    #       facts were never regenerated. Gating this warning on
    #       schema_version alone would make it disappear across exactly that
    #       round-trip (Codex review, PR #720) -- the flags themselves, not
    #       the version number, are the ground truth here.
    # Each entry pairs a flag with whether it is even CONSULTED by the one
    # detector that reads it, given THIS side's own AST producer and header
    # confirmation. Most flags' value computation already collapses to
    # "reliable" for every producer their consumer doesn't gate on (see each
    # flag's own computation in `storage.snapshot_codec.decode_snapshot`) --
    # but two don't: clang_va_list_facts_reliable's value treats "hybrid" the
    # same as "clang" (correct for the fact's own provenance), yet
    # diff_symbols._diff_param_va_list only ever consults it when BOTH sides
    # are exactly "clang" (never "hybrid"). castxml_var_access_facts_reliable
    # is the mirror case for "castxml" vs. diff_symbols._diff_var_access.
    # Listing either one for a hybrid snapshot would claim reduced detection
    # coverage that regenerating the snapshot could never restore, since no
    # detector consults it for that producer regardless of schema version
    # (Codex review, PR #720).
    #
    # Separately, five of the seven flags' one real consumer requires
    # CONFIRMED (non-inferred) header awareness on this side before it ever
    # reads the flag at all: clang_restrict/clang_va_list/
    # castxml_var_access's detectors each exit through
    # diff_symbols._both_header_aware before consulting their flag, and
    # clang_deprecation/clang_field_initializer's shared consumer
    # (fact_provenance.fact_producer) opens with the identical
    # ``from_headers and not from_headers_inferred`` check. A schema-v1..v5
    # snapshot that predates the explicit ``from_headers`` key gets
    # ``from_headers`` GUESSED true from a populated surface -- real, but
    # not "confirmed" -- so none of these five detectors will ever consult
    # their flag for it regardless of whether a fresh dump would restore it
    # (Codex review, PR #720). header_cv_facts_reliable and
    # clang_vtable_facts_reliable are the two exceptions: their consumers
    # (variable/field cv checks, layout/vtable diffing) apply to any
    # snapshot carrying the underlying fact, header-confirmed or not.
    # The table itself (which flag, whether it's actually consulted given
    # this snapshot's own ast_producer/header-confirmation shape) lives in
    # `policy.analysis_assurance_degraded_facts` -- by construction time
    # every one of
    # `snap`'s `*_facts_reliable` fields, plus `from_headers`/
    # `from_headers_inferred`/`ast_producer`, already carries the exact
    # `*_value` `decode_snapshot` computed above, so reading it back off
    # `snap` rather than the locals is the same computation. Shared with
    # `analysis_assurance.compute_analysis_assurance` so the load-time
    # warning below and the reported `analysis_assurance` status can never
    # independently drift on what counts as "degraded".
    _degraded_facts = degraded_reliability_facts(snap)
    if _degraded_facts:
        import warnings

        if _schema_version < SCHEMA_VERSION:
            _reason = (
                f"Snapshot schema_version {_schema_version} predates this "
                f"abicheck's schema_version {SCHEMA_VERSION}"
            )
        else:
            _reason = (
                "This snapshot carries facts preserved from an earlier, "
                f"older extraction (its schema_version reads as the current "
                f"{SCHEMA_VERSION} because it was re-saved since, but the "
                "underlying facts below were never regenerated)"
            )
        warnings.warn(
            f"{_reason}: "
            f"{', '.join(_degraded_facts)} "
            f"{'is' if len(_degraded_facts) == 1 else 'are'} marked unreliable on "
            "this snapshot, so the affected detectors will decline to trust these "
            "stale facts rather than risk a false positive purely from this tool "
            "upgrade. Detection coverage for these fields is reduced until this "
            "snapshot is regenerated with the current abicheck.",
            UserWarning,
            stacklevel=2,
        )

    return finalize_snapshot(snap)


def load_snapshot(path: str | Path) -> AbiSnapshot:
    """Load a snapshot from *path*, transparently handling plain, gzip, and
    zstd storage (ADR-059) — detected from magic bytes, not the filename."""
    from .snapshot_io import read_snapshot_text

    return snapshot_from_dict(json.loads(read_snapshot_text(path)))


# ADR-061 gap E: BundleFacts (de)serialization is classified `storage`
# (`storage.bundle_facts_codec`), alongside every other snapshot/baseline
# codec -- moved out of the flat `bundle_facts_serialization.py` facade
# module, itself moved out of the historical flat `bundle_facts.py` before
# that module had a settled ADR-061 layer at all. Each wrapper below still
# resolves its implementation via `importlib.import_module` (a runtime call,
# not a static `ast.Import`/`ast.ImportFrom` node) rather than a
# `from .storage.bundle_facts_codec import ...` -- that module itself needs
# `snapshot_to_dict`/`snapshot_from_dict` from *this* module, and a static
# import in both directions is exactly the `serialization <->
# storage.bundle_facts_codec` cycle `scripts/check_ai_readiness.py`'s
# `import-cycle-growth` check flags via a full `ast.walk` (so even a
# function-scoped `from ... import ...` counts) -- the same reason
# `abicheck.cli`'s own `__getattr__` resolves its moved names through
# `abicheck.frontends.cli.moved` instead of importing them back. Unlike that
# facade, these are real typed `def`s rather than a blanket module
# `__getattr__`: these four names are called with real argument/return types
# by other first-party modules (`cli_compare_release_helpers.py`, ...), and
# `__getattr__(...) -> Any` would
# silently erase that checking for every caller reaching them through this
# module's documented `from abicheck.serialization import ...` path (Codex
# review).
def _bundle_facts_serialization() -> Any:
    import importlib

    return importlib.import_module(".storage.bundle_facts_codec", __package__)


def bundle_facts_to_dict(facts: BundleFacts) -> dict[str, Any]:
    """Serialize a :class:`~abicheck.bundle_facts.BundleFacts` to a
    JSON-able dict (G38 Phase 2). See
    :func:`abicheck.bundle_facts_serialization.bundle_facts_to_dict`."""
    return cast(
        "dict[str, Any]", _bundle_facts_serialization().bundle_facts_to_dict(facts)
    )


def bundle_facts_from_dict(d: dict[str, Any]) -> BundleFacts:
    """Inverse of :func:`bundle_facts_to_dict`. See
    :func:`abicheck.bundle_facts_serialization.bundle_facts_from_dict`."""
    return cast("BundleFacts", _bundle_facts_serialization().bundle_facts_from_dict(d))


def load_bundle_facts(
    path: str | Path, *, format: str = "auto", max_json_object_nodes: int | None = None
) -> BundleFacts:
    """Load a BundleFacts. See
    :func:`abicheck.bundle_facts_serialization.load_bundle_facts`."""
    return cast(
        "BundleFacts",
        _bundle_facts_serialization().load_bundle_facts(
            path, format=format, max_json_object_nodes=max_json_object_nodes
        ),
    )


def save_bundle_facts(
    facts: BundleFacts,
    path: str | Path,
    *,
    format: str = "json",
    compression: str = "auto",
) -> SnapshotWriteResult:
    """Save *facts*. See
    :func:`abicheck.bundle_facts_serialization.save_bundle_facts`."""
    return cast(
        "SnapshotWriteResult",
        _bundle_facts_serialization().save_bundle_facts(
            facts, path, format=format, compression=compression
        ),
    )
