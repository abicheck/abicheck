### Removed

- `compare --old-variant` / `compare --new-variant` are removed
  (one-comparison-product.md Phase 7j, ADR-068 D5). Variant selection is
  still a per-run operand and still on the CLI — only the *spelling*
  changed. Use the side-scoped `--variant` instead, the same
  `old=`/`new=`-prefixed shape every other two-sided `compare` input
  already uses (ADR-040 Lever 1):

  ```sh
  # before
  abicheck compare old_pkg new_pkg --old-variant gcc13 --new-variant gcc14
  # after
  abicheck compare old_pkg new_pkg --variant old=gcc13 --variant new=gcc14
  # a bare value applies to both sides
  abicheck compare old_pkg new_pkg --variant gcc13
  ```

  The old spellings are a hard usage error (`No such option`, exit 64) —
  there is no hidden alias and no deprecation window. The unregistered
  release engine (`cli_compare_release.py`) keeps its own per-side
  `--old-variant`/`--new-variant`, exactly as it kept per-side
  `--old-version`/`--new-version` through the same ADR-040 lever.

### Changed

- An empty variant id is now a usage error instead of a silently-ignored
  selection. `--variant old=`, `--variant new=`, `--variant both=` and a
  bare `--variant ""` each exit 64 with a message naming the flag, checked
  eagerly in the Click callback before any extraction runs (the same
  grammar-validation shape `dump --provenance` uses). Previously the
  pair's `default=None` made "flag absent" and "flag given an empty value"
  indistinguishable, so a package declaring several variants failed much
  later with a message naming neither.
