<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->
### Security

- `compile.compiler` (the demoted `--compiler`/`--compiler-prefix` pair)
  could be honored from an **auto-discovered**, untrusted `.abicheck.yml`
  — not just an explicit `--config` — on `compare`'s single-pair and
  directory/package release-fan-out paths, `scan --against`'s project-
  config resolution, and the stored-BundleFacts `compare` dispatch path:
  each of these had already resolved the config path through its own
  auto-discovery before handing it to the shared compile-context resolver,
  which then (incorrectly) treated any non-`None` path as explicitly
  trusted. A PR-controlled `.abicheck.yml` found by directory search (e.g.
  inside a fork PR's own branch content in CI) could therefore select the
  executable used for header extraction, defeating the trust gate that
  setting already has for an explicit `--config`.
- `compile.options` could smuggle a compiler-plugin-loading flag
  indirectly via Clang's own `--config`/`--config=<file>` configuration-
  file mechanism or the `@<file>` response-file convention, both of which
  point at a separate file whose contents the existing plugin-loading scan
  never inspected. Both are now rejected outright, the same as every
  inline plugin-loading spelling.
- The composite Action's compile-context `--config` overlay (synthesized
  from `gcc-path`/`sysroot`/etc. inputs when `build-config` isn't given)
  now fails loud instead of silently replacing an already-checked-out
  project `.abicheck.yml`'s other settings (severity/suppression/scope/
  bundle/...) — the same fail-loud precedent this Action already applies
  when `build-config` is given explicitly alongside these inputs.
