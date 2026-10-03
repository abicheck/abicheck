### Fixed

- **Dependency exclusion under a cross toolchain** — headers in a Debian/Ubuntu cross sysroot (`/usr/<triple>/include`, e.g. `aarch64-linux-gnu` or `x86_64-w64-mingw32`) were not recognised as system headers, so a cross-target dump kept every libc and libstdc++ declaration (7,707 functions instead of 7 for a one-struct header). They are now treated exactly like `/usr/include`.
