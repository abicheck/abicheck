### Fixed

- **A slow toolchain no longer changes what a dump records** — every probe of
  a compiler, castxml, `c++filt` or `git` (target triple, pointer width and
  endianness, compiler family and version, default language standard, include
  search path, `--target=` validation, demangled names, the commit) now waits
  up to one shared hang guard, `deadline.PROBE_HANG_TIMEOUT_S` (120 s), instead
  of 5–30 s. On a loaded host a working tool used to time out and record an
  empty fact, or fail a declared `target:` as "could not be verified"; an
  active `--budget` still bounds every call.
