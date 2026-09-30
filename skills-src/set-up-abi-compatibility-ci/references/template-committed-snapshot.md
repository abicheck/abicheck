# Template C — committed snapshot

Replace every `<placeholder>` with a fact from the repository: `<lib>`
(`libfoo`), `<lib-path>` (exact built file, e.g. `build/libfoo.so`),
`<public-headers>` (`include/`), `<lang>` (`c`/`c++`), `<build-steps>` (the
repository's own install + configure + build, with debug info and a shared
build on both sides).

```yaml
      - uses: abicheck/abicheck@v0.6.0
        with:
          old-library: abi/<lib>.abicheck.json   # committed, reviewed
          new-library: <lib-path>
          new-header: <public-headers>
          lang: <lang>
```

Produce the file once with the release-side `dump` ([template A](template-release-baseline.md)'s dump step,
run locally or via `workflow_dispatch`), commit it, and document the refresh
rule: the snapshot is regenerated **only** when a release is cut, in the
release PR, never in the PR that the check is failing on
([safety invariants](../../shared/safety-invariants.md) item 7).
