### Fixed

- **The clang header backend no longer records class members inside an `extern "C"` block as C linkage.** C++ [dcl.link]/4 ignores a C language linkage for class members, but the clang walk passed an enclosing `extern "C" { ... }` block's state into the record's members. Those members got `is_extern_c=True`. With an `asm("...")` label they also got an unmangled symbol, which collapsed their identity onto a free function's. They now keep C++ linkage and their mangled identity, as the castxml backend already recorded them. `EXTRACTOR_GENERATION` is now 83.
