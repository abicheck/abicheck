### Performance

- Header-AST parse no longer rebuilds the union of both ELF export tables
  for every castxml `Function` element (`extract/headers/castxml/functions.py`,
  and the same test in `dumper_castxml.py`) -- 36% of a `libmkl_rt` scan's
  self time; measured single-library wall 5:15 -> 3:27 with byte-identical
  findings.
- `elf_symbol_filter.is_abi_relevant_elf_symbol` tests each prefix group with
  one tuple-argument `startswith` and memoizes per name (was ~48 Python-level
  calls per symbol, 2.39M calls on a 65k-export library).
- `compare`'s L0 hard-removal fold skips its symbols-only re-resolve and second
  unscoped compare when NEW's ELF table provably keeps every OLD symbol
  (`l0_export_delta.elf_exports_cannot_lose_symbol`) -- the probe can then
  only return nothing. It was ~19% of a `libmkl_rt` scan.
- The compare-time lexical pattern pre-scan blanks comments/strings by
  skipping uniform runs instead of one Python call per character, computes
  line numbers by bisect, and memoizes per-file results on a content digest, so a
  release fan-out no longer re-scans the same headers once per member.

- Repeated whole-table work in one compare is now computed once: the raw ELF
  export index and `exported_symbol_names` are memoized on the `ElfMetadata`
  they read (~190 rebuilds per `libmkl_rt` compare), the public
  function/variable/CPO surface reconciliations join the existing per-pair
  memo in `compare/template_surface.py`, `parse_elf_metadata` keeps a
  content-digest-keyed process memo (the L0 re-resolve and release members
  re-parsed the same binaries), and the structural C++20 header scan memoizes
  its per-file preprocessing and scan on a content digest
  (`extract/digest_memo.py`, `extract/cpp20_header_prep.py`).

### Fixed

- The castxml/clang header-AST disk caches (`~/.cache/abi_check/<backend>`)
  were unbounded (a profiled host held 57 GB). Each backend directory is now
  trimmed least-recently-used to `ABICHECK_AST_CACHE_MAX_BYTES` (default
  16 GiB; `0` disables), re-checked at most every five minutes per
  directory. Each read or write stamps the entry's mtime, and entries used in
  the last hour are never evicted.
