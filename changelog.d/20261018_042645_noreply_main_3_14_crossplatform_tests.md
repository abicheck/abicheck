### Fixed

- **Compile-database evidence stores POSIX paths on every OS** — `CompileUnit.source` and `directory` read from a `compile_commands.json` are now normalised to `/` separators, as the other buildsource adapters already are. On Windows a unit recorded as `D:\a\build\src\foo.cpp` now reads `D:/a/build/src/foo.cpp`, so it matches the `/`-keyed graph paths built from the same evidence and no longer differs by the collecting host. Output on Linux and macOS is unchanged.
