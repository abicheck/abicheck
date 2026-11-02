---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Snapshot write still builds the largest section's tree whole

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-31): NARROWED.** Storage plan A2.2's section step is
done: a write no longer builds the whole encoded document tree, only one
section's at a time. The largest section is still built whole.

**What changed.** `storage/snapshot_section_stream.streamed_sectioned_document`
hands the writer a `json_stream.LazyItems` document whose `sections` member
encodes each section when the serializer reaches it. The flat keys a section
owns (`legacy_sections.section_fields`) go through
`snapshot_encode._snapshot_to_dict(..., keys=...)`, which walks only those
fields and skips every post-pass none of whose outputs is wanted
(`encode_surface_graph`, `encode_semantic_ir`, `encode_extraction_scope`,
the sidecar entity ids, the embedded build source). The section is packaged
by `import_v1.legacy_section_dto_dict`, the per-section step the
whole-document split also uses, gets its D14/D15 carriers back, is written
and dropped. Bytes are unchanged:
`tests/test_snapshot_section_stream.py` checks indented and compact output
against the whole-document encoder (`tests/_whole_document_encoder.py`)
over Hypothesis-generated snapshots with record/header extensions, unknown,
blocked and failed sections, and over 51 fixture-corpus snapshots.

**Measured** (`tracemalloc` peak of encode plus the fragment stream; RSS
growth without tracing; same sha256 both ways):

| Input | Whole document | Section by section | Time |
|---|---|---|---|
| Synthetic, 40k functions + 40k records (two equal sections) | 550.5 MiB, RSS +584 MiB | 286.2 MiB, RSS +316 MiB | 10.5 s -> 10.1 s |
| `libstdc++.so.6` binary-only dump (23.9 MB JSON) | 39.8 MiB | 37.1 MiB | 0.76 s -> 0.75 s |

**What remains.** The peak is now the largest section. On a binary-only
dump nearly everything is the `binary` section (the ELF symbol list), so
the libstdc++ figure barely moves. Going below one section needs the
record-level step: the section payload (`canonical_form_shared`, the
section DTO codecs, `restore_record_extras`) would have to accept a lazy
record list, and the per-record passes (`_compact_elf_symbols`,
`encode_fact_fields`, `encode_entity_ids`, ...) would run per record as it
is emitted.

**Do not** stream by emitting sections out of their current key order. The
`publication` digest and existing baselines depend on today's bytes.
