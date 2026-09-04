### Fixed

- **PDB/CodeView 32-bit pointer members now resolve to the correct
  4-byte size** — `TypeDatabase._resolve_type_size()` checked the wrong
  CodeView `SimpleTypeMode` constant for near32 pointers (`0x02`, which
  is actually the legacy 16-bit `FarPointer` mode); the real near32
  constant is `0x04`, so every ordinary 32-bit pointer member previously
  fell through to the generic 8-byte default while the basic channel
  still reported itself fully parsed.
- **CTF parsing no longer rejects a small, valid compressed blob as
  truncated** — `parse_ctf_from_bytes` previously enforced the full
  36-byte v3 header length *before* decompressing a `CTF_F_COMPRESS`
  blob, even though only the 4-byte preamble is guaranteed uncompressed;
  a valid compressed blob (e.g. one containing a single integer type) can
  easily be smaller than that in total. Header parsing now reads just the
  preamble first, decompresses if needed, and only then parses (and
  length-checks) the real header body.
