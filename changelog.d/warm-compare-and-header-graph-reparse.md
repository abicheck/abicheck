### Performance

- A compare whose sides are both served by the snapshot cache no longer forks a child process per side. Each side is first resolved in-process from the cache only, and only a side that must extract goes to a child. On oneDNN a warm run took ~12 s either way, but forked it peaked at 850 MB instead of 324 MB (3.12) and used about 35% more CPU.
- When the header graph's clang parse fails on some headers (oneDNN's SYCL headers on a host parse), the headers clang's own diagnostics blame are dropped and the rest are re-parsed once, instead of bisecting. On oneDNN this replaces 18 full-closure parses (3.75 GB of AST JSON per side) with two. The bisect remains the fallback when the diagnostics miss a header.

### Fixed

- The snapshot cache no longer misses when the same files are given with a different path spelling (relative, `./x`, `a/../x`, absolute). The dump inputs are made absolute before the key is computed and before the dump runs, so a warm hit and a cold run record the same paths. Symlinks are kept as given, not resolved.
- A header-graph parse that recovers some headers no longer logs "falling back to a declaration-only graph" followed by "the rest were parsed separately". It logs one message that says which headers contribute declarations only and how many still have call/reference edges.
