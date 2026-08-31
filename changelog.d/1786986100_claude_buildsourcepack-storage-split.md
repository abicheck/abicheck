### Changed

- **`BuildSourcePack` no longer carries its own persistence methods.**
  `BuildSourcePack.load(path)`, `pack.write()`, `pack.content_hash()`,
  `pack.verify_integrity()`, and `pack.to_ref(...)` have moved off the class
  into free functions in the new `abicheck.buildsource.pack_io` module
  (`load(root)`, `write(pack)`, `content_hash(pack)`, `verify_integrity(pack)`,
  `to_ref(pack, path_hint=...)`), closing ADR-061 Phase 5's recorded residual:
  `BuildSourcePack`'s five data fields (plus its pure `empty()`/
  `to_embedded_dict()`/`from_embedded_dict()`) are `model`-layer, while
  load/write/hashing are I/O and belong to `storage`, which `model` may not
  import. `BuildSourcePack` was never part of `abicheck.service`'s tracked
  Python API surface, and `verify_integrity()` had no call sites anywhere in
  the repository, so the direct impact is limited to code that called these
  methods directly rather than through `abicheck`'s CLI or typed API.
