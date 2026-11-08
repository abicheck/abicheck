# The typed compare() route omits exported_not_public that the CLI reports for the same catalog case

Opened 2026-11-08. The index entry is in [known-gaps.md](../known-gaps.md).

**Status (2026-11-08): OPEN.** Found by the typed-API route check in `tests/test_invariant_oracles_catalog.py`. On `case09_cpp_vtable` (castxml, with headers), the CLI reports `exported_not_public` (a binary export with no header declaration) and the typed `abicheck.checker.compare()` on the same dumped snapshots does not, even though `cross_source_checks` defaults to `True`. Verdict and every other kind agree; `surface_metrics=True` closes the `public_surface_*` gap on `case01`, so the remaining difference is the cross-source stage's input. The test is a strict xfail for `case09` until the typed route receives the same resolved inputs as the CLI. Owner: `checker.py` (the typed entry point's defaults) and `workflows/` (the request the CLI resolves).
`false-clean` — the typed route can read a catalog case as having one fewer hygiene finding than the CLI.
