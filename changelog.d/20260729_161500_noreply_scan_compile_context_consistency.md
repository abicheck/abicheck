### Fixed

- **`scan --against`'s compile context now always reflects the same
  project config its scope/suppression settings resolve from.** Previously
  a cwd-upward-discovered `.abicheck.yml` (no explicit `--config` or
  `--sources`) fed its `scope:`/`suppression:` settings into the
  comparison but never reached `resolve_compile_context`, so its
  `compile:` block (defines, include dirs, frontend, std, sysroot) was
  silently dropped — risking a false `COMPATIBLE` verdict for a
  macro- or dialect-dependent header API. The project-config path is now
  resolved and loaded once, upfront, and the same already-loaded config is
  threaded into both the compile-context resolver and the scope/
  suppression resolver, matching `compare`'s existing pattern.

(Codex review, PR #657.)
