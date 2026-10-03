### Fixed

- **`static_tls_introduced` on AArch64** — static-TLS use was read only from `DF_STATIC_TLS`, which GNU ld does not write on AArch64, so an initial-exec library was reported as `dlopen()`-safe there. The requirement is now also read from the TP-relative dynamic relocations it creates (per architecture, numbers from glibc's `elf.h`), so the finding no longer depends on the linker's summary flag.
