### Fixed

- **Demangling no longer depends on the host locale.** `c++filt` is now fed
  symbol names as UTF-8 on stdin (with `surrogateescape`), instead of in the
  locale encoding. On Windows before Python 3.15 that encoding is cp1252, and
  a single non-ASCII `_Z` name raised `UnicodeEncodeError` for the whole
  demangle batch. The single-symbol path now uses stdin too, so Windows no
  longer re-encodes names through the ANSI code page. `deadline.run_bounded`
  gains `encoding`/`errors` pass-throughs.
- **The Action's report-freshness fingerprint no longer trusts a lossy
  `stat`.** Ubuntu 26.04's default `stat` (uutils) prints `%.9Y` with nine
  digits but only 100ns of precision, so a report rewritten within the same
  100ns at the same size could pass as unchanged, and a pre-existing one could
  read as fresh. Each run now uses the native `stat` only after it reproduces
  a nanosecond mtime set and read back by `os.stat`, and otherwise falls back
  to `os.stat` in the same spelling. The delta-base library checksum helper,
  which reused the `_file_fingerprint` name, is renamed so it no longer
  shadows it.
