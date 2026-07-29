### Fixed

- **A misspelled Clang `target:` is now actually rejected instead of
  passing unconditionally.** The prior fix exempted Clang bindings from
  the GCC-style `-dumpmachine` target comparison (correctly, since a single
  Clang binary is inherently multi-target), but exempted the *entire*
  check, so `target: not-a-real-target` bound to a real Clang passed too.
  `abicheck.buildsource.toolchain_probe` now actually invokes Clang with
  the declared `--target=` on a trivial empty translation unit and checks
  whether it's accepted, catching a bogus/misspelled target without
  rejecting a genuinely valid cross-compilation profile.
- **The `target:` environment comparison now distinguishes ABI variants
  within the same libc family.** `arm-linux-gnueabi` (soft-float) vs.
  `arm-linux-gnueabihf` (hard-float), and `x86_64-linux-gnu` vs.
  `x86_64-linux-gnux32` (the x32 ILP32-on-x86_64 ABI), previously all
  reduced to the same generic `"gnu"` environment and passed as matching.

