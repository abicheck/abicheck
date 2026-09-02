### Fixed

- Legacy snapshot loading no longer infers a debug-info producer from a
  document's `dwarf`/`dwarf_advanced` block. Those blocks are debug
  *storage*, not a format claim: `BtfMetadata.to_dwarf_metadata` (and its
  CTF sibling) and `pdb_metadata.parse_pdb_debug_info` all write into them
  with `has_dwarf=True`, and `dwarf_presence._section_presence_metadata`
  sets the advanced block's flag for BTF/CTF too — so nothing in a legacy
  document says which producer ran. A populated block now evidences no
  producer at all, which downgrades `TypeField.is_const`/`is_volatile` to
  `NOT_COLLECTED` on a non-header legacy document still holding their
  resting default (the only two case-(a) facts naming `dwarf`). The claim
  is narrowed, never the value: a real non-default value is preserved and
  stays `PRESENT`. Fresh snapshots are unaffected — they persist each
  `<field>_fact` directly, so the backfill never runs on them.
