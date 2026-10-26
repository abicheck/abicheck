### Fixed

- **Compiler-flag replay and dump cache now honor more flag spellings and include-search environment** — the castxml emulation now carries a joined `-xc`/`-xc++` language flag, which it dropped before, so a `-xc` build parsed as C++ with the emulated compiler. The dump cache key now folds in `CPATH`, `C_INCLUDE_PATH` and `CPLUS_INCLUDE_PATH`, which castxml reads from the environment, so changing one of them no longer serves a snapshot parsed under the old search path.
