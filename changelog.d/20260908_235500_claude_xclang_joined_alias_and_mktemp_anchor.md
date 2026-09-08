<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->
### Fixed

- `compile.options` plugin-loading rejection now also catches Clang's
  documented `-Xclang=<arg>` joined alias (`-Xclang=-load`), not just the
  separate `-Xclang -load` two-token form — the joined spelling previously
  let a PR-controlled `.abicheck.yml` smuggle an arbitrary compiler plugin
  past the check.
- The composite Action's compile-context config overlay is now anchored
  under `$RUNNER_TEMP` instead of a bare `mktemp` call, fixing a Windows/Git
  Bash `FileNotFoundError` when an explicit compile-context input
  (`gcc-path`/`sysroot`/`gcc-options`/etc.) is given.
