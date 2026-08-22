### Fixed

- **`abicheck.product_baseline.pack_product_baseline`**: OUTPUT written
  under a not-yet-existing subdirectory of SOURCE_DIR
  (`SOURCE_DIR/artifacts/base.tar.zst`) made that output-only
  `artifacts/` scaffold directory itself an archive member -- an
  unpacked baseline gained a directory that was never part of the
  original product, and the archive's own contents depended on where
  the caller chose to place OUTPUT. A first fix (excluding by the
  existing existence-gated scaffold set) introduced a determinism
  regression of its own: the scaffold directory already exists by the
  second identical `pack` call, so it was excluded on the first run
  and silently included on the second. Fixed with a new,
  existence-independent `_output_parent_chain()` used only for
  archive-content exclusion, keeping the existing existence-gated set
  for the unrelated "nothing to pack" check it was built for.
- **`abicheck.package.TarExtractor._safe_extract_zst_tar`**: `.tar.zst`
  decompression used an unbounded `shutil.copyfileobj()` that
  materialized the entire decoded tar before any archive member was
  ever validated -- a tiny, highly compressible archive could fill the
  host/CI runner's disk before extraction got a chance to raise.
  Decompression is now bounded and incremental (an 8 GiB default,
  overridable via `_ABICHECK_TAR_ZST_MAX_DECODED_BYTES`), with
  `max_window_size` reusing `snapshot_io.py`'s existing zstd-memory-bomb
  defense rather than a second, independently-derived window ceiling.
