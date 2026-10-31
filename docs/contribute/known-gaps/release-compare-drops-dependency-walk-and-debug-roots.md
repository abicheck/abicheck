---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: historical
generated: false
---

# A directory/package `compare` ignores the dependency walk and debug roots

Index: [Known gaps](../known-gaps.md#option-vocabulary-adr-091-follow-ups) (closed; see the [archive](../archive/known-gaps-closed.md))

**Status (2026-10-31): CLOSED.** Fixed the same day: the fan-out forwards the four inputs to each member comparison (`ReleaseMemberRunInputs`, `workflows/release_member_request.py`) and drops the `--set deps.*` refusal; the release report shows each member's walk (release schema 2.6). Regression: `tests/test_release_compare_run_inputs.py`.

Originally found while adding the `deps.*`/`debug.info` config homes (ADR-091 D4, 2026-10-31 amendment). The directory/package fan-out (`cli_compare_helpers.run_compare` → `_dispatch_release_compare`) takes no `follow_deps`, `search_paths`, `ld_library_path` or debug-root argument. It forwards only the debug *package* transport (`debug_info1`/`debug_info2`). So `--follow-deps`, `--search-path`, `--ld-library-path` and a directory or detached-file `--debug-info` are accepted on a directory/package operand and do nothing, with no warning.

Measured for `--follow-deps`: `compare old/ new/ --follow-deps` on two directories holding one ELF library each exits 0 with no dependency section. The same flag on the two library files prints "Dependency Analysis". The debug-root half was found by reading the code only.

The config keys inherit the gap: a `.abicheck.yml` with `deps.*` or a directory `debug.info` entry is not applied on that route either. `--set deps.search_paths`/`deps.ld_library_path` are refused there (`frontends/cli/options/config_set._RELEASE_APPLIED_PATHS`), and `--set` refuses the two core keys everywhere, so no stated override is dropped silently. A fix either forwards the four inputs to each member comparison or refuses them on the route, the flags and the config keys alike.
