### Fixed

- **The GitHub Action's `gcc-options` input (mapped to `--compiler-option`)
  now preserves shell-style quoting**, e.g. `-DMSG="hello world" -DOK=1`
  stays two tokens instead of splitting into three malformed ones. The
  previous mechanical `--gcc-options` → `--compiler-option` migration routed
  the value through the Action's own plain bash word-splitting, losing the
  quote-aware `shlex.split()` semantics abicheck's server side used to apply
  to the old scalar `--gcc-options` flag.
