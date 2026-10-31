### Fixed

- **A directory/package `compare` now applies the dependency walk and debug
  roots to every member** — `--follow-deps`, `--search-path`,
  `--ld-library-path` and a directory or detached-file `--debug-info` (and
  their `deps.*`/`debug.info` config homes) were accepted on a
  directory/package operand and silently ignored, so a release of stripped
  libraries with build-id debug roots could exit 0 where the same pair
  compared as files exited 4. Each member now takes exactly the run inputs a
  single-pair compare of its pair takes, `--set deps.search_paths`/
  `deps.ld_library_path` are accepted on that route, and the release report
  shows each member's walk: `libraries[].old_dependency_info`/
  `new_dependency_info` in JSON (release schema 2.6, additive) and a
  per-member "Dependency Analysis" section in Markdown.
  The keyword API `run_compare()` gains `follow_deps`,
  `dependency_search_paths` and `ld_library_path`, mirroring `CompareRequest`.
