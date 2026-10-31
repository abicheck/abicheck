---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Some `deps.*` keys have no config home yet

Index: [Known gaps, closed](../archive/known-gaps-closed.md)

**Status (2026-10-31): CLOSED** ([archive](../archive/known-gaps-closed.md)). The dependency settings reachable only from the CLI were `--follow-deps`, `--search-path` and `--ld-library-path` on `compare` and `dump`. Each now has a config home: `deps.follow`, `deps.search_paths` and `deps.ld_library_path`. Each has a registry row (`architecture/option-vocabulary.yaml`). The two non-core keys also have a `--set` path and a typed `config_overrides` channel on `CompareRequest`/`DumpRequest`; `deps.follow` backs a core flag, so those refuse it, naming `--follow-deps`. The flags stay and win. `deps.root` (`deps compare`/`deps tree --root`) gets no key, since it is the operand of the `deps` commands, which read no `.abicheck.yml`. A directory/package `compare` does not apply the walk at all, with the flag or the key: see [that gap](release-compare-drops-dependency-walk-and-debug-roots.md). Tests: `tests/test_config_run_input_homes.py`.
