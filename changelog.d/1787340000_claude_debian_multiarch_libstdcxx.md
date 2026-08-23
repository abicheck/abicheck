### Fixed

- **The system-header check for libstdc++'s `usr/include/c++/<version>` tree
  required `c++` immediately after the system prefix, missing the common
  Debian/Ubuntu multiarch layout.** A multiarch-enabled GCC install puts
  libstdc++ under `/usr/include/<multiarch-triple>/c++/<version>/` (e.g.
  `/usr/include/x86_64-linux-gnu/c++/12/`) rather than directly under
  `/usr/include/c++/<version>/`. Since public-surface matching runs before
  system-header detection, an explicit `-I` under this real layout let
  libstdc++ internals (`bits/c++config.h` and similar) survive dependency
  exclusion as ordinary project declarations, risking false ABI findings
  from the toolchain surface. Fixed by accepting a validated multiarch/
  target-triple component between the system prefix and `c++`, the same
  structural validation already required for the `lib/gcc/<triple>/`
  layout.
