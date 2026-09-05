### Fixed

- **`scan --artifact-set` no longer silently drops a malformed
  `.abicheck.yml` `bundle:` block.** CLI cleanup phase two, PR J removed
  `--bundle-system-providers` entirely in favor of the config-only
  `bundle.system_providers:` setting, but the ambient (cwd-discovered)
  config path still followed the pre-existing "auto-discovered config is
  best-effort" convention (matching `compile:`/`severity:`, which do have
  a CLI-flag fallback): a malformed `bundle:` block was silently treated
  as "no providers declared" and the audit continued, which could produce
  false unresolved-dependency findings from a configuration the user has
  no other way to supply. A malformed ambient `bundle:` block (explicit
  `--build-config` was already covered) now fails loud with a usage error
  (exit 64), the same way an explicit `--config` already does.
