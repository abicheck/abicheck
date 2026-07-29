### Fixed

- **A profile declaring only `target:`/`compiler_version:` (no
  `compiler_family:`) whose binding resolves to a real `cl.exe` is now
  silently skipped, matching this module's own documented MSVC exemption.**
  The MSVC skip only checked the *declared* `compiler_family`, so a
  binding resolving to `cl.exe` still reached the `--version` probe (which
  `cl.exe` doesn't support) and was reported as "could not be probed" —
  contradicting the module's documented "a declared MSVC family/binding is
  silently skipped." Now also checked against the *resolved* binding
  path's stem.
- **GNU ABI-variant target suffixes no longer need to be individually
  enumerated to be distinguished.** After three rounds of adding specific
  suffix markers (`gnueabihf`/`gnueabi`/`gnux32`/`gnuabi64`/`gnuabin32`/
  `gnu_ilp32`), `powerpc-linux-gnuspe` (PowerPC SPE) was still found
  collapsing to the same generic `"gnu"` bucket as `powerpc-linux-gnu`.
  `_env_family` now returns the target triple's whole trailing component
  verbatim whenever it contains a base libc/environment marker
  (`gnu`/`musl`/`msvc`), instead of matching against a fixed enumeration —
  preserving any ABI suffix, known or not, automatically.
