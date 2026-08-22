### Fixed

- **`abicheck.binary_utils._canonical_library_key`**: a stored PE snapshot
  filename (`Foo.dll.abicheck.json`, or its `.gz`/`.zst` compressed forms)
  carries its DLL identity in the filename, not in the represented
  binary's own content — `_pe_is_dll_content` reads the snapshot's actual
  bytes (JSON), never a PE image, so it never recognized one. Without
  matching the embedded `.dll` segment, the same release's PE snapshot
  published under two case spellings (`Foo.dll.abicheck.json` vs.
  `foo.dll.abicheck.json`) never case-folded to one canonical key, and
  `compare-release`'s `_build_match_map` reported them as an unrelated
  removal+addition instead of comparing them. Now matched the same way
  the pre-existing ELF `.so` regex allows an arbitrary trailing suffix
  after the extension.
