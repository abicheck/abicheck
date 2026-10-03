### Fixed

- **A header castxml cannot parse is dropped, not its neighbour.** With
  `-H include/` and one header that fails under castxml, the L2 fallback
  excluded the header *before* the failing one and the dump still failed.
  The castxml aggregate gained a preamble include on its first line, and the
  fallback mapped the aggregate's line `N` to header `N-1`. It now names the
  failing input by the file the aggregate includes in the error's include
  chain, so the aggregate's layout no longer matters; an error raised inside
  the preamble itself attributes to no header and re-raises.
