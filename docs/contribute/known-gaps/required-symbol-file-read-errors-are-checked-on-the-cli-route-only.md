---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `--required-symbol @FILE` read errors are checked on the CLI route only

Index: [Known gaps](../known-gaps.md)

**Status (recorded 2026-10-22): OPEN.**

An unreadable `@FILE` is refused as a typed `CompareSettingsError` (exit 64, path named) by `workflows/compare_configuration.load_required_symbols`. That function is the only place `@FILE` is resolved. The CLI reaches it through `compare`, and `tests/test_compare_settings_cli_translation.py` pins both the typed function and the CLI exit.

No bug-class harness covers this path. The typed `CompareRequest` API does not accept `@FILE` required symbols, so there is no second route to compare the refusal against. Family F2's route-parity matrix (`tests/test_family_f2_route_parity.py`) compares successful reports across routes and has nothing to attach to here.

Why it is recorded rather than fixed: the refusal itself is correct and tested. The gap is that a future route exposing `@FILE` (for example a Python API parameter) would not be checked against this CLI behaviour until someone adds a cell to a harness.

What would close it: expose `@FILE` resolution on a second route, then add a route-parity cell that refuses the same unreadable input identically on both.
