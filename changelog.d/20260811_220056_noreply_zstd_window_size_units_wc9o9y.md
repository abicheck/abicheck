### Fixed

- **zstd-compressed snapshots (`.json.zst`) written at a realistic window
  size could not be read back.** `snapshot_io.py`'s zstd decompressor
  passed its window-size ceiling to `python-zstandard`'s
  `ZstdDecompressor(max_window_size=...)` after dividing it by 1024 — that
  parameter's own docstring claims kibibytes, but the underlying
  implementation passes the value straight through to
  `ZSTD_DCtx_setMaxWindowSize()`, which takes raw bytes. The effective
  accepted window shrank to ~2 MiB instead of the intended 2 GiB, so any
  `.json.zst` snapshot the writer compressed with a larger window (e.g. its
  8 MiB baseline compression level) failed to decode with
  `ZstdError: Frame requires too much memory for decoding` — surfaced to
  users as a misleading `Cannot detect format of '...'` error, since format
  sniffing swallows the underlying decompression failure.
