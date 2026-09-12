### Changed

- **One strict YAML manifest loader.** The three independent
  duplicate-key-checking `yaml.SafeLoader` subclasses in `dump_manifest.py`,
  `compatibility_evaluation_packs.py`, and `impact/use_cases.py` are replaced
  by a single shared primitive, `abicheck.model.yaml_strict.load_strict_yaml`.
  The copies had already diverged, so two malformed-manifest cases now report
  correctly where they previously did not: a sequence/mapping used as a
  mapping key (`? [a, b]` / `--bundle-facts-library-manifest`, `--dump-manifest`)
  raised a raw `TypeError` instead of the command's own manifest error, and
  an out-of-range implicit scalar (`2023-99-99`) escaped `--dump-manifest`'s
  error contract as a bare `ValueError`. Every malformed document — syntax
  error, duplicate key, unhashable key, invalid implicit scalar — now reaches
  the user as that manifest format's documented error type. Suppression files
  are deliberately unaffected: their format resolves YAML merge keys and keeps
  `safe_load`'s last-value-wins semantics on purpose.
