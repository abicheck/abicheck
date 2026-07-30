<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Bundle intra-dependency resolution (`compare`'s bundle layer and
  `scan --artifact-set`'s audit-mode detector) could resolve an unversioned
  symbol import against a provider that only exports a non-default GNU
  symbol-version definition** (`foo@V1`, not `foo@@V1`) — the dynamic linker
  can only satisfy an *unversioned* reference against a default definition,
  so this silently under-reported a real load-time failure as resolved.
  `ProviderEntry` now carries `is_default` (from `ElfSymbol.is_default`),
  and the unversioned-import resolution check requires it (Codex review).
