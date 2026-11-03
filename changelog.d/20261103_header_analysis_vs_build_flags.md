### Fixed

- **A C library's build data no longer breaks or perturbs its header
  analysis** — two shapes, both found in the catalog's build-source lane:
  - A compile database carrying a C `-std=` (`-std=gnu11`) made the header
    parse fail: the usual `#ifdef __cplusplus extern "C"` guard selected C++
    mode, and the frontend rejected the C standard in a C++ parse. A C
    `-std=` from the build now selects C mode over that weak signal (never
    over C++-only syntax, a C++ `-std=` or an explicit `--lang`).
  - A build that force-includes the very public header being compared
    (`-include v1.h` with `-H v1.h`) carried that header into the header
    parse, where it entered the comparability fingerprint by content: the
    API change itself made the pair "not comparable". A forced include that
    is one of the declared public headers is no longer rendered into the
    parse; other forced includes (`config.h`) still are.
