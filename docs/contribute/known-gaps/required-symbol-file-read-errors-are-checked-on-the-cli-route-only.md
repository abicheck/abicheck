---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `consumers.required_symbols` `@FILE` read errors are checked on the CLI route only

Index: [Known gaps](../known-gaps.md)

**Status (recorded 2026-10-22, spelling updated 2026-10-31): OPEN.**

The `@FILE` form (one symbol per line, `#` comments ignored) is an entry of the
`consumers.required_symbols` config key, set in `.abicheck.yml` or per run with
`compare --set 'consumers.required_symbols=["@plugin.syms"]'`. It replaced the
removed `compare` flag of the same name (ADR-091 Phase 5). A relative `@FILE`
in `.abicheck.yml` resolves against the config file
(`frontends/cli/compare_config_scope.py`).

An unreadable `@FILE` is refused as a typed `CompareSettingsError` (exit 64, path named) by `workflows/compare_configuration.load_required_symbols`. That function is the only place `@FILE` is resolved. The CLI reaches it through `compare`, and `tests/test_compare_settings_cli_translation.py` pins both the typed function and the CLI exit.

No bug-class harness covers this path. The typed API has no second route: `CompareRequest` has no required-symbols field, and `run_compare(config_overrides={"consumers.required_symbols": [...]})` is refused with `ConfigOverrideError` ("no typed CompareRequest field applies this key"). Family F2's route-parity matrix (`tests/test_family_f2_route_parity.py`) compares successful reports across routes and has nothing to attach to here.

Why it is recorded rather than fixed: the refusal itself is correct and tested. The gap is that a future route exposing `@FILE` (for example a typed `consumers` field) would not be checked against this CLI behaviour until someone adds a cell to a harness.

What would close it: expose `consumers.required_symbols` on a second route, then add a route-parity cell that refuses the same unreadable input identically on both.
