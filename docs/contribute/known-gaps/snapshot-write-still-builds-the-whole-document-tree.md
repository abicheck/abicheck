---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Snapshot write still builds the whole document tree

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-31): OPEN.** Storage plan A2.2 is half done. The JSON text
is streamed, but the encoded document tree is still built whole before it
is serialized.

`write_snapshot` streams `json_stream` fragments to disk, so the JSON string
and its bytes are never held. `snapshot_encode.sectioned_document_for_write`
still materialises the full encoded dict tree first. Measured on a
`libstdc++.so.6` dump (23.9 MB JSON), `tracemalloc` peaks at 39.8 MiB to
build the tree and 39.9 MiB for the whole uncompressed write. The tree is
therefore the entire remaining write peak.

**Why it is not a one-pass fix.** About a dozen post-passes run over the
whole flat dict before it is sectioned: `encode_entity_ids`,
`encode_sidecar_entity_ids`, `encode_fact_fields`, `_compact_elf_symbols`,
`encode_surface_graph`, `encode_semantic_ir`, `to_sectioned_document`'s
legacy-section split, and others. Several of them read or rewrite sibling
keys. Encoding field by field means first giving each pass a declared
input/output key set, like D18's `DECODE_STEPS` on the read side, and then
proving that the streamed bytes match the current encoder. The proof can be
a Hypothesis differential over generated snapshots against
`join_json_indented(sectioned_document_for_write(snap))`.

**Do not** stream by emitting sections out of their current key order. The
`publication` digest and existing baselines depend on today's bytes.
