<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Documentation

- `_compose_gcc_options`'s docstring now spells out a direct consequence of
  its no-further-escaping composition: an `abi_macros` value (like every
  other composed atom) can never contain a space, since `_safe_profile_atom`
  validation is the only shell-safety guarantee this function relies on.
- `reusable-workflows.md`'s "Shared analysis options" section now states
  explicitly that a profile's `compile:` overlay **replaces** — rather than
  merges with — `check-project.yml`'s global `gcc-path`/`gcc-options`
  inputs for that cell, and what to do if a project needs a global flag to
  still apply on an overlaid cell (repeat it in the overlay's own `args`).
