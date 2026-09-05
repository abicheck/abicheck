### Removed

- **`compare --manifest` renamed to `--instantiation-manifest`.** The bare
  spelling collided with `aggregate`'s own `--manifest` and the product's
  several other manifest-shaped concepts (dump manifest, run plan, bundle
  facts, project config). No deprecation alias, per this repository's
  standing hard-cleanup stance — the old spelling now fails with
  `No such option`, exit `64`. Closes CLI cleanup phase two's PR J rename
  item (`docs/contribute/plans/cli-cleanup-phase-two.md`).
- **`compare`'s `--bundle-system-providers`/`--bundle-cohort` and `scan
  --artifact-set`'s `--bundle-system-providers` are gone**, with no
  deprecation alias. Both were per-invocation flags for a stable,
  reviewed-in-a-PR property of a release's topology, not a per-run analysis
  input — closes the other half of CLI cleanup phase two's PR J.

### Added

- **`.abicheck.yml` gains a `bundle:` block** — `system_providers:` (extra
  sonames to treat as system-provided, extending the built-in
  libc/libstdc++/libgcc/libtbb allow-list) and `cohorts:` (co-versioned
  library name prefixes enabling the `BUNDLE_SONAME_SKEW` check) — the sole
  source for both settings now, auto-discovered the same way `severity:`/
  `scope:`/`suppression:` already are for `compare`'s directory/package
  release fan-out, and from `--build-config`/auto-discovery relative to
  `sources` for `scan --artifact-set`.
