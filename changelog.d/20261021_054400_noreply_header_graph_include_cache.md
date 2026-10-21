### Performance

- **The header include pass no longer re-runs `clang -M` for unchanged headers** —
  the header-only graph's per-header include probe now keeps a disk cache
  beside the AST cache. A warm `dump` or `compare` issues no probe for a header
  whose content, argv, language and clang binary are unchanged, and a changed
  header or any file its include closure reports invalidates just that entry.
  Partial runs (any probe diagnostic) are never stored, and
  `ABICHECK_REFERENCE_MODE=1` bypasses the cache.
