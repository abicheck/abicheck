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

"""AbiSnapshot <-> JSON codec (ADR-061 gap E) -- the real storage-owned
implementation behind :mod:`abicheck.serialization`'s public compatibility
facade.

``abicheck/serialization.py`` stays the documented, stable import path
(``load_snapshot``/``save_snapshot``/``write_snapshot`` and friends); this
module and its siblings are where the codec itself now lives, classified
``storage`` per ADR-061's responsibility-package architecture. The codec is
split across several sibling modules purely to keep each one under the
ADR-061 new-file production line ceiling (a mechanical extraction, not a
redesign -- every comment and Codex-review citation moved verbatim):

* ``snapshot_schema_versions`` -- ``SCHEMA_VERSION``, its full per-bump
  history, and the derived ``_MIN_SCHEMA_VERSION_FOR_*``/
  ``_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION`` thresholds.
* ``snapshot_encode`` -- the encode direction (``snapshot_to_dict``/
  ``snapshot_to_json``/``snapshot_content_digest``), re-exported here for
  the facade's own convenience.
* ``snapshot_decode_declarations`` -- the functions/variables/types/enums/
  typedefs decode (the largest single contiguous slice).
* ``snapshot_reliability_flags`` -- the seven ``*_facts_reliable`` flag
  computations.
* ``snapshot_section_decode`` -- one decoder per section kind and the
  declared joins between them (ADR-062 D18); each reads the flat document
  only through a view of its declared sections.

This module itself owns: the sectioned-document unwrap and schema-version
gate, the composition of the D18 decode steps into the final
``AbiSnapshot(...)`` assembly, and the two functions :mod:`abicheck.serialization` calls
directly. Two of the historical ``snapshot_from_dict`` pipeline's steps are
genuinely NOT storage's to own and stay in that facade instead of living
here:

* ``workflows.snapshot_load.backfill_python_ext_from_evidence`` -- real
  evidence-derived extraction logic, not a fact lookup, so it cannot run
  inside a module whose own ``may_import`` is ``[model]`` only (see that
  function's own docstring, and the ADR-061 gap E closure history in
  ``docs/contribute/known-gaps.md``).
* ``policy.analysis_assurance_degraded_facts.degraded_reliability_facts`` --
  the load-time "which facts are stale" warning is a ``policy`` judgement
  over an already-decoded snapshot, not a decode step.

:func:`decode_snapshot` therefore returns a snapshot that has NOT yet run
either step, paired with the raw ``schema_version`` integer the facade's own
warning message needs; :func:`finalize_snapshot` is the storage-legal tail
(ELF-binding backfill, anonymous-type-spelling normalization, anonymous
closure-identity renumbering) that runs after those two facade-level steps.
``abicheck.serialization.snapshot_from_dict`` is the one place that stitches
all of it back into the original, unchanged five-step pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..snapshot_io import SnapshotWriteResult
from ..errors import IncompatibleSnapshotSchemaError
from ..model import AbiSnapshot
from ..model.lazy_declarations import defer_declaration_step
from ..model.semantic_ir_legacy_adapter import finalize_snapshot_semantic_ir
from ..model.snapshot_reliability import FACT_FAMILIES, flag_name
from .extraction_scope_codec import decode_extraction_scope
from .fact_codec import (
    apply_legacy_fact_backfill,
    evidenced_producers,
)
from .generation_migrations import migrate_and_describe
from .sectioned_document import from_sectioned_document, is_sectioned_document
from .semantic_ir_codec import decode_semantic_ir
from .snapshot_encode import (
    snapshot_content_digest as snapshot_content_digest,
    snapshot_to_dict as snapshot_to_dict,
    snapshot_to_json as snapshot_to_json,
)
from .snapshot_lazy_decode import decode_lazily, lazy_ir_eligible
from .snapshot_load_normalization import (
    backfill_missing_elf_binding,
    normalize_and_renumber_closure_identities_on_load,
)
from .snapshot_reliability_flags import decode_reliability_flags
from .snapshot_schema_versions import (
    _MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION,
    _RETIRED_SCHEMA_VERSIONS,
    SCHEMA_VERSION as SCHEMA_VERSION,
)
from .snapshot_section_decode import (
    decode_binary_section,
    decode_build_section,
    decode_debug_section,
    decode_declarations_section,
    decode_dump_provenance,
    decode_layout_section,
    decode_preprocessor_hidden,
    decode_provenance_section,
    decode_typedef_abi_facts,
    decode_types_section,
    infer_from_headers,
    view_for,
)
from .surface_graph_codec import decode_surface_graph
from .wire_extensions import WIRE_EXTENSIONS_KEY, attach as attach_wire_extensions


def decode_snapshot(
    d: dict[str, Any], *, max_known_schema_version: int = SCHEMA_VERSION
) -> tuple[AbiSnapshot, int]:
    # `max_known_schema_version` defaults to this module's own SCHEMA_VERSION
    # but is accepted as an explicit parameter so `abicheck.serialization`'s
    # facade-level (patchable) `SCHEMA_VERSION` global is the value actually
    # consulted below when called through `serialization.snapshot_from_dict`
    # -- a test that does `monkeypatch.setattr(serialization, "SCHEMA_VERSION",
    # ...)` must still change what this function treats as "known", which a
    # bare module-level reference to this module's own constant could not
    # honor once the codec moved out of the facade module itself.
    #
    # ADR-062/063 Phase 8 (redesign): a document written as the single-file
    # sectioned shape (`storage.sectioned_document`) is unwrapped into this
    # function's own long-established flat shape *before* anything below
    # runs -- every existing reliability-backfill/schema-version rule stays
    # exactly as it was, now just fed a document `export_legacy_snapshot`
    # reconstructed rather than one written flat. `is_sectioned_document`
    # checks for a `"sections"` key, never a real `AbiSnapshot` field, so a
    # flat document from any schema version cannot collide with it.
    if is_sectioned_document(d):
        d = from_sectioned_document(d, defer_graph=True, carry_extensions=True)
    carried = d.pop(WIRE_EXTENSIONS_KEY, None)  # ADR-062 D14/D15
    # ADR-062 D2: read the generation stamp, report drift through the shared
    # reader-compatibility owner, and migrate spellings an older extractor
    # generation wrote (storage/generation_migrations.py).
    d, _fact_generations, _spellings_generation, _generation_notices = (
        migrate_and_describe(d)
    )
    # Inspect schema version for future migration hooks.
    # Snapshots without schema_version are treated as v1 (pre-versioning format).
    # Currently only v1 and v2 exist and have the same on-disk layout, so no
    # migration is required.  This baseline lets future PRs add migration logic here.
    _schema_version: int = int(d.get("schema_version", 1))
    if _schema_version in _RETIRED_SCHEMA_VERSIONS:
        raise IncompatibleSnapshotSchemaError(
            f"Snapshot schema_version {_schema_version} was written by an "
            "unreleased, reverted abicheck build and is never read. Re-dump it."
        )
    if (
        _schema_version > max_known_schema_version
        and _schema_version >= _MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION
    ):
        # ADR-050 D1 — this reader has no code path that even looks for a
        # verdict-blocking field introduced at or after this threshold
        # (starting with ``contract``). Warn-and-continue here would let this
        # reader silently compare two possibly-incomparable snapshots and
        # produce an ordinary, wrong verdict — the exact failure mode this
        # ADR exists to close. Raised as a SnapshotError subclass so existing
        # ``except SnapshotError`` handling (e.g. cli_resolve.py's clean
        # click.UsageError/ClickException translation) still catches it.
        raise IncompatibleSnapshotSchemaError(
            f"Snapshot schema_version {_schema_version} requires abicheck "
            f"supporting at least schema_version "
            f"{_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION} to read safely "
            f"(this reader supports up to schema_version {max_known_schema_version}). "
            "Upgrade abicheck to read this snapshot."
        )
    if _schema_version > max_known_schema_version:
        import warnings

        warnings.warn(
            f"Snapshot schema_version {_schema_version} is newer than this abicheck "
            f"(supports up to schema_version {max_known_schema_version}). "
            "Data may be incomplete or misinterpreted. "
            "Upgrade abicheck to read this snapshot correctly.",
            UserWarning,
            stacklevel=2,
        )

    # ADR-062 D18: each section decodes from a view holding only its own
    # keys, and every cross-section derivation is a declared join
    # (storage/snapshot_section_decode.DECODE_STEPS). A document on which
    # every join touching `declarations`/`types`/debug layout is a no-op
    # leaves them pending until first read (storage/snapshot_lazy_decode.py).
    if lazy_ir_eligible(d, _schema_version):
        snap = decode_lazily(d, _schema_version)
    else:
        snap = _decode_eagerly(d, _schema_version)
    decode_surface_graph(view_for(d, "graph"), snap)  # surface_graph_codec (v29)
    snap.fact_generations = _fact_generations
    snap.spellings_generation = _spellings_generation
    snap.dump_provenance = decode_dump_provenance(view_for(d, "provenance"))
    snap.live_preprocessor_hidden = decode_preprocessor_hidden(view_for(d, "layout"))
    snap.typedef_abi_facts = decode_typedef_abi_facts(
        view_for(d, "declarations"), _schema_version
    )
    snap.load_notices = (*snap.load_notices, *_generation_notices)
    if carried is not None:
        attach_wire_extensions(snap, carried)
        snap.unreadable_sections = tuple(carried.unreadable)
    return snap, _schema_version


def _decode_eagerly(d: dict[str, Any], sv: int) -> AbiSnapshot:
    """Every section decoded now. Same steps, same order of checks, same
    snapshot as the former single-function decode."""
    decls = decode_declarations_section(view_for(d, "declarations"), sv)
    types = decode_types_section(view_for(d, "types"), sv)
    binary = decode_binary_section(view_for(d, "binary"), sv)
    debug = decode_debug_section(view_for(d, "debug"), sv)
    build = decode_build_section(view_for(d, "build"), sv)
    from_headers, from_headers_inferred = infer_from_headers(
        view_for(d, "from_headers"),
        elf_only_mode=binary["elf_only_mode"],
        declarations=decls,
        types=types,
    )
    reliability_flags = decode_reliability_flags(
        view_for(d, "reliability_flags"),
        from_headers=from_headers,
        ast_producer_value=debug["ast_producer"],
        schema_version=sv,
    )
    _apply_legacy_fact_backfill(
        view_for(d, "legacy_fact_backfill"),
        sv,
        decls=decls,
        types=types,
        ast_producer=debug["ast_producer"],
        header_provenance_confirmed=from_headers and not from_headers_inferred,
        reliability_flags=reliability_flags,
    )
    layout = decode_layout_section(view_for(d, "layout"), sv)
    provenance = decode_provenance_section(view_for(d, "provenance"), sv)

    snap = AbiSnapshot(
        **provenance,
        **binary,
        **decls,
        **types,
        **debug,
        **build,
        **layout,
        from_headers=from_headers,
        from_headers_inferred=from_headers_inferred,
        # ADR-063 Phase 10: the eight former `*_facts_reliable` booleans,
        # recorded as one set (storage/snapshot_reliability_flags.py).
        stale_fact_families=frozenset(
            family
            for family in FACT_FAMILIES
            if not reliability_flags[flag_name(family)]
        ),
    )
    decode_semantic_ir(view_for(d, "semantic_ir"), snap)  # semantic_ir_codec (v38)
    # storage/extraction_scope_codec.py (v52)
    decode_extraction_scope(view_for(d, "extraction_scope"), snap)
    # ADR-063 Track T3: decode_semantic_ir() mutates snap.semantic_ir directly,
    # after AbiSnapshot.__post_init__ already ran (with semantic_ir still None
    # at that point) -- so a loaded snapshot's own Track T3 consistency check
    # never ran at all without this second, explicit call (Codex review).
    # ADR-063 6B: the same call also fills record layout a pre-v53 IR never
    # recorded, from the snapshot's own records.
    finalize_snapshot_semantic_ir(snap)
    return snap


def _apply_legacy_fact_backfill(
    d: dict[str, Any],
    schema_version: int,
    *,
    decls: dict[str, Any],
    types: dict[str, Any],
    ast_producer: Any,
    header_provenance_confirmed: bool,
    reliability_flags: dict[str, bool],
) -> None:
    """The ``legacy_fact_backfill`` join (ADR-063 Phase 0 and Phase 5's
    case-(a) batches; storage/fact_codec.py): one call, below every
    reliability flag it is judged by. ``evidenced`` names the producers the
    document actually evidences -- recorded (never inferred) header
    provenance, plus its own platform; a debug block names no producer."""
    apply_legacy_fact_backfill(
        d,
        types["types"],
        decls["functions"],
        schema_version,
        reliability_flags["clang_vtable_facts_reliable"],
        reliability_flags["clang_va_list_facts_reliable"],
        ast_producer,
        evidenced=evidenced_producers(
            header_provenance_confirmed=header_provenance_confirmed,
            ast_producer=ast_producer,
            platform=d.get("platform"),
        ),
        variables=decls["variables"],
        enums=decls["enums"],
        header_cv_facts_reliable_value=reliability_flags["header_cv_facts_reliable"],
        clang_field_initializer_facts_reliable_value=reliability_flags[
            "clang_field_initializer_facts_reliable"
        ],
        clang_deprecation_facts_reliable_value=reliability_flags[
            "clang_deprecation_facts_reliable"
        ],
        clang_restrict_facts_reliable_value=reliability_flags[
            "clang_restrict_facts_reliable"
        ],
        castxml_var_access_facts_reliable_value=reliability_flags[
            "castxml_var_access_facts_reliable"
        ],
        param_kind_facts_reliable_value=reliability_flags["param_kind_facts_reliable"],
        # T9 / ADR-063 Phase 6 item 4: the raw per-declaration provenance
        # map, read independently of the snapshot field's own copy.
        fact_provenance=dict(d.get("fact_provenance", {})),
    )


def finalize_snapshot(snap: AbiSnapshot) -> AbiSnapshot:
    """The storage-legal tail of the historical ``snapshot_from_dict``
    pipeline, run after the facade's own workflows/policy steps
    (``backfill_python_ext_from_evidence``, the degraded-facts warning) have
    already run against the :func:`decode_snapshot` result -- see this
    module's own docstring for why those two steps cannot live here.
    """
    # Rewrites declarations only: queued into a still-pending declaration
    # store (ADR-062 D18) rather than decoding it here.
    defer_declaration_step(snap, backfill_missing_elf_binding)
    return normalize_and_renumber_closure_identities_on_load(snap)


def save_snapshot(
    snap: AbiSnapshot,
    path: str | Path,
    *,
    compression: str = "auto",
) -> None:
    """Save *snap* to *path*.

    *compression* is one of ``"auto"`` (default; inferred from *path*'s
    canonical suffix — ``.json.gz``/``.json.zst``, else plain),
    ``"none"``, ``"gzip"``, or ``"zstd"`` (ADR-059). Keyword-only so every
    existing positional ``save_snapshot(snap, path)`` call keeps working
    unchanged.

    Returns nothing, matching the historical signature; use
    :func:`write_snapshot` for the richer :class:`SnapshotWriteResult`.
    """
    write_snapshot(snap, path, compression=compression)


def write_snapshot_compact(
    snap: AbiSnapshot,
    path: str | Path,
    *,
    compression: str = "zstd",
    zstd_level: int | None = None,
) -> SnapshotWriteResult:
    """Save *snap* to *path* as compact (no-whitespace) JSON, streamed.

    For a machine-only file nobody reads by eye -- the private snapshot cache
    entry -- indentation is pure overhead: compact entries are ~15%
    smaller (2.12 vs 2.48 MiB on an MKL entry) than :func:`write_snapshot`'s
    indented output; the encode itself is a small part of a write, which is
    dominated by building the sectioned document. Like :func:`write_snapshot` it streams
    (:func:`~.json_stream.iter_json_compact` into
    ``write_snapshot_text_stream``), so neither the whole text nor its bytes
    is held. Key order is the sectioned encoder's, so the decoded snapshot
    and its content digest are unchanged. User-facing snapshot files keep
    :func:`write_snapshot`'s indented output.
    """
    from ..snapshot_io import SnapshotCompression
    from .acyclic_json import gc_paused
    from .json_stream import iter_json_compact
    from .snapshot_encode import sectioned_document_for_write
    from .snapshot_stream_write import write_snapshot_text_stream

    with gc_paused():
        return write_snapshot_text_stream(
            iter_json_compact(sectioned_document_for_write(snap)),
            path,
            compression=SnapshotCompression(compression),
            zstd_level=zstd_level,
        )


def write_snapshot(
    snap: AbiSnapshot,
    path: str | Path,
    *,
    compression: str = "auto",
    zstd_level: int | None = None,
    zstd_content_size: bool = True,
) -> SnapshotWriteResult:
    """Save *snap* to *path* and return a :class:`SnapshotWriteResult`
    (compression used, decoded/stored sizes, stored digest) — ADR-059.

    The document is streamed to disk (`json_stream.iter_json_indented` into
    `snapshot_stream_write.write_snapshot_text_stream`), so neither the
    whole JSON text nor its encoded bytes is ever held -- on a 238 MB
    snapshot that was +0.52 GiB of peak memory, and the streamed write is
    also slightly faster. Plain and gzip output is byte-identical to the
    one-shot ``json.dumps`` write.

    *zstd_content_size*: a zstd frame can declare its decoded size only
    when the size is known before compression starts, which a stream does
    not know. The default keeps that declaration -- and the reader's
    "truncated mid-header" cross-check it enables -- by encoding a zstd
    write in one shot (the same encoder, joined). A caller for which that check adds nothing passes
    ``False`` to stream zstd too: the snapshot cache, whose entries are
    private and treat any read failure as a miss.
    """
    from ..snapshot_io import (
        SnapshotCompression,
        resolve_write_compression,
        write_snapshot_text,
    )
    from .acyclic_json import gc_paused
    from .json_stream import iter_json_indented, join_json_indented
    from .snapshot_encode import sectioned_document_for_write
    from .snapshot_stream_write import write_snapshot_text_stream

    resolved = resolve_write_compression(Path(path), SnapshotCompression(compression))
    if resolved is SnapshotCompression.ZSTD and zstd_content_size:
        # One shot, so the frame can declare its size -- but formatted by the
        # same encoder as every streamed write, not a second ``json.dumps``.
        with gc_paused():
            text = join_json_indented(sectioned_document_for_write(snap), indent=2)
        return write_snapshot_text(
            text, path, compression=resolved, zstd_level=zstd_level
        )
    with gc_paused():
        return write_snapshot_text_stream(
            iter_json_indented(sectioned_document_for_write(snap), indent=2),
            path,
            compression=resolved,
            zstd_level=zstd_level,
        )
