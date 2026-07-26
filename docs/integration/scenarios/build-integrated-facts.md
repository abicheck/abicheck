# Scenarios S8 & S9: Source Facts From the Build Itself

Instead of replaying a compile database after the fact
([S7](source-replay.md)), your build can emit normalized source facts
*while it compiles* — no second pass over the sources, and no compile
database needed at check time. [ADR-047](../../contribute/adr/047-github-actions-integration-model.md)
§8 names two producers for this:

- **S8 — the `abicheck-cc` compiler wrapper.** Set `CC`/`CXX` (or your build
  system's compiler-launcher equivalent) to `abicheck-cc`; it transparently
  wraps your real compiler and writes normalized facts alongside each
  translation unit as it's compiled. Works with any build system that
  respects `CC`/`CXX` — Make, EPICS, Autotools, CMake (via a launcher, no
  reconfiguration needed).
- **S9 — the Clang facts plugin.** A `-fplugin=` loaded into Clang invocations
  directly, for projects where a compiler wrapper isn't practical. Opt-in
  (needs an LLVM-major-matched plugin build), not the onboarding default.

Both producers write the same normalized `abicheck_inputs/` pack shape —
downstream, a check consumes either one identically via
`evidence-producer: wrapper` or `evidence-producer: clang-plugin`
(`actions/collect-facts`/`actions/check-target`); which one you pick is a
build-integration decision, not a downstream one.

## The two-step choreography

Both producers need a `collect-facts phase: prepare` step to run **before**
your project's own build (setting up the wrapper/plugin so the compiler
actually gets invoked through it), and, once the build finishes, an
explicit `phase: verify` step to validate the pack it collected. Unlike
`producer: replay` (S7), where `phase: auto` alone can do both the setup
and the validation in one step, `producer: wrapper`/`clang-plugin` need the
prepare/verify split spelled out explicitly — `phase: auto` isn't a
meaningful choice for these two producers, since there's no single step
that can run both before and after your build:

```yaml
jobs:
  build-and-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: abicheck/abicheck/actions/collect-facts@v0.5.0
        with:
          phase: prepare
          producer: wrapper
      - name: Build
        run: CC=abicheck-cc CXX=abicheck-cc make
      - uses: abicheck/abicheck/actions/collect-facts@v0.5.0
        with:
          phase: verify
          producer: wrapper
      - uses: abicheck/abicheck/actions/check-target@v0.5.0
        with:
          name: libfoo
          requested-depth: source
          evidence-producer: wrapper
          # ... baseline/candidate inputs per your baseline channel ...
```

See [Producing Source Facts](../../use/producing-source-facts.md) for
the full wrapper/plugin setup (including `ABICHECK_CC_EXTRACTOR`, wiring into
Make/CMake/Autotools, and the public-header-roots resolution trap), and the
[`check-target` reference](../../reference/check-target.md) for how
`evidence-producer` composes with `collect-facts`.

## When to move past this scenario

- **You'd rather not touch the build at all** → [S7: Source Scan via
  Compile-DB Replay](source-replay.md) — replay after the fact instead.
- **Your build is Make-based with no compile database at all** → S11,
  still [Producing Source Facts](../../use/producing-source-facts.md)
  (the wrapper is exactly the fix — it doesn't need a compile database).
- **Several DSOs should share one facts pack** → S16,
  [Build Info & Sources](../../learn/build-source-data.md).

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [Producing Source Facts](../../use/producing-source-facts.md) — the canonical setup reference.
- [`check-target` Action Reference](../../reference/check-target.md) — the `evidence-producer` contract.
